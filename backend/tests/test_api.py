"""Flask API + job queue end to end: real Postgres, fakeredis, fake models and an in-memory
registry (see conftest.py). Jobs run in an in-process RQ burst worker (`run_jobs`).

`client` here is the artist's browser, signed in with SIWE; `anon` has no session."""

import io
import json
import time

import pytest
from sqlalchemy import text

from chain.eip712 import Verdict, recover_signer
from tests.conftest import ARTIST, DEV_VERIFIER, OTHER_KEY


@pytest.fixture
def client(artist_client):
    return artist_client


@pytest.fixture
def anon(server):
    return server.app.test_client()


@pytest.fixture
def reviewer_key(server):
    return server.config.REVIEWER_KEY


@pytest.fixture
def submit(client, fixture_bytes):
    def run(name, wallet=None, expect=202):
        resp = (wallet or client).upload(fixture_bytes(name), name)
        assert resp.status_code == expect, resp.get_json()
        return resp.get_json()

    return run


@pytest.fixture
def analyse(client, submit, run_jobs):
    """Submit, run the worker, return the finished report."""

    def run(name, wallet=None):
        job = submit(name, wallet)
        run_jobs()
        done = client.get(f"/api/jobs/{job['job_id']}").get_json()
        assert done["status"] == "done", done
        return done["report"]

    return run


def review(client, rid, key, approve=True, note=""):
    return client.post(f"/api/review/{rid}", json={"reviewer_key": key, "approve": approve, "note": note})


def attest(client, rid, **body):
    return client.post(f"/api/attest/{rid}", json=body)


# ---- submission + jobs ----------------------------------------------------------------------


def test_analyze_validates_input(client, anon, fixture_bytes):
    assert client.post("/api/analyze").status_code == 400
    assert client.upload(b"", "x.png").status_code == 400
    # without a wallet session, nothing can be submitted
    resp = anon.post("/api/analyze")
    assert resp.status_code == 401 and "sign in" in resp.get_json()["error"]


def test_upload_must_be_signed_by_the_session_wallet(client, fixture_bytes):
    raw = fixture_bytes("photo.png")
    wrong = client.upload(raw, key=OTHER_KEY)  # someone else's signature over the same file
    assert wrong.status_code == 401 and "signed-in wallet" in wrong.get_json()["error"]
    stale = client.upload(raw, issued_at="2026-01-01T00:00:00.000Z")
    assert stale.status_code == 401 and "expired" in stale.get_json()["error"]
    bad_time = client.upload(raw, issued_at="yesterday")
    assert bad_time.status_code == 401
    unsigned = client.post("/api/analyze", data={"image": (io.BytesIO(raw), "p.png")})
    assert unsigned.status_code == 401


def test_signed_claim_is_stored_with_the_report(client, analyse, fixture_bytes):
    import hashlib

    from auth import recover

    r = analyse("photo.png")
    claim = client.get(f"/api/report/{r['report_id']}").get_json()["claim"]
    assert claim["file_sha256"] == hashlib.sha256(fixture_bytes("photo.png")).hexdigest()
    assert f"File SHA-256: 0x{claim['file_sha256']}" in claim["message"]
    assert recover(claim["message"], claim["signature"]) == ARTIST  # anyone can re-check who claimed it


def test_submit_is_async_and_fast(server, client, submit, run_jobs):
    t = time.perf_counter()
    job = submit("photo.png")
    assert time.perf_counter() - t < 0.5  # no model work in the request (p95 budget: 200 ms)
    assert job["status"] == "queued" and job["status_url"] == f"/api/jobs/{job['job_id']}"
    assert client.get(job["status_url"]).get_json()["status"] == "queued"
    assert (server.config.INCOMING_DIR / job["job_id"]).exists()

    run_jobs()
    done = client.get(job["status_url"]).get_json()
    r = done["report"]
    assert done["status"] == "done" and r["report_id"] == job["job_id"]
    assert r["decision"] == r["status"] == "PASS", r["reasons"]
    assert r["artist"] == ARTIST
    assert set(r["checks"]) == {"resnet", "ai_detector", "copy_move", "copy_exact", "copy_semantic"}
    assert (server.config.UPLOADS_DIR / f"{r['report_id']}.png").exists()
    assert not (server.config.INCOMING_DIR / job["job_id"]).exists()
    full = client.get(f"/api/report/{r['report_id']}").get_json()
    assert full["evidence"]["copy_move_heatmap"]  # stored next to the report, served on demand


def test_invalid_image_fails_the_job(client, submit, run_jobs):
    resp = client.upload(b"definitely not a png", "x.png")
    assert resp.status_code == 202
    run_jobs()
    out = client.get(f"/api/jobs/{resp.get_json()['job_id']}").get_json()
    assert out["status"] == "error" and "not a valid image" in out["error"]


def test_unknown_job_and_report(client):
    assert client.get("/api/jobs/" + "0" * 32).status_code == 404
    assert client.get("/api/jobs/not-an-id").status_code == 404
    assert client.get("/api/report/" + "0" * 32).status_code == 404
    assert client.get("/api/verify/not-a-report-id").status_code == 404


def test_lost_redis_job_is_requeued(server, client, submit, run_jobs, fake_redis):
    job = submit("photo.png")
    fake_redis.flushall()  # Redis restarted without persistence
    assert client.get(job["status_url"]).get_json()["status"] == "queued"  # re-enqueued from the DB row
    run_jobs()
    assert client.get(job["status_url"]).get_json()["status"] == "done"


def test_state_survives_restart(server, client, analyse, fake_redis):
    """Everything lives in Postgres / storage: new connections (a restarted process) see it all."""
    rid = analyse("photo.png")["report_id"]
    server.db.reset()
    fake_redis.flushall()
    assert client.get(f"/api/report/{rid}").get_json()["status"] == "PASS"
    assert client.get(f"/api/jobs/{rid}").get_json()["status"] == "done"


def test_queue_down_returns_503(server, client, fixture_bytes, monkeypatch):
    from redis.exceptions import ConnectionError

    def down(_):
        raise ConnectionError("redis down")

    monkeypatch.setattr(server.jq, "enqueue_analysis", down)
    resp = client.upload(fixture_bytes("photo.png"), "p.png")
    assert resp.status_code == 503
    with server.db.session() as s:
        assert s.execute(text("SELECT status FROM submissions")).scalar() == "error"


def test_rate_limit(server, submit, monkeypatch):
    monkeypatch.setattr(server.config, "RATE_ANALYZE_PER_HOUR", 2)
    submit("photo.png")
    submit("photo.png")
    assert submit("photo.png", expect=429)["error"].startswith("rate limit")


def test_security_headers_and_health(client):
    resp = client.get("/healthz")
    assert resp.get_json() == {"ok": True, "db": True, "redis": True}
    assert "script-src 'self'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


# ---- verdicts, review, attestations ------------------------------------------------------------


def test_pass_attestation_is_bound_to_the_artist(server, client, analyse, fake_registry):
    r = analyse("photo.png")
    assert fake_registry.txs == []  # PASS verdicts are submitted by the artist, not the server
    resp = attest(client, r["report_id"])
    assert resp.status_code == 200, resp.get_json()
    a = resp.get_json()
    assert a["call"] == "submitAndMint" and a["ready"] and a["chain_id"] == 31337
    v = Verdict.from_json(a["verdict"])
    assert v.artist == ARTIST and v.report_hash.hex() == r["report_hash"] and v.decision == 1
    assert recover_signer(v, a["signature"], 31337, a["registry"]) == DEV_VERIFIER
    assert 23 * 3600 < v.expires_at - time.time() <= 24 * 3600
    assert a["token_uri"] == f"http://testserver/metadata/{r['report_id']}.json"
    meta = json.loads((server.config.METADATA_DIR / f"{r['report_id']}.json").read_text())
    assert meta["mint_gate"]["report_hash"] == r["report_hash"]

    again = attest(client, r["report_id"]).get_json()
    assert again["verdict"] == a["verdict"]  # an unexpired attestation is reused, not re-signed
    assert attest(client, r["report_id"], network="nope").status_code == 400
    # the signature is accepted by the (fake) registry exactly once
    fake_registry.submit_verdict(v, a["signature"])
    assert fake_registry.verdict_of(r["report_hash"])["decision"] == "PASS"


def test_review_flow(server, client, analyse, run_jobs, reviewer_key, fake_registry):
    from jobs import tasks

    tasks.pipeline().resnet.value = 0.99  # REVIEW-only check: may flag, never fail
    r = analyse("photo.png")
    rid = r["report_id"]
    assert r["decision"] == "REVIEW" and r["checks"]["resnet"]["status"] == "REVIEW"
    run_jobs()  # chain queue: the backend anchors the REVIEW verdict itself
    assert fake_registry.verdict_of(r["report_hash"])["decision"] == "REVIEW"
    assert fake_registry.verdict_of(r["report_hash"])["artist"] == ARTIST

    assert attest(client, rid).status_code == 403  # not approved yet
    assert review(client, rid, "wrong").status_code == 403
    assert [x["report_id"] for x in client.get("/api/reports?status=REVIEW").get_json()] == [rid]

    ok = review(client, rid, reviewer_key, note="hand-drawn, checked sketches")
    assert ok.status_code == 200 and ok.get_json()["status"] == "REVIEW_APPROVED"
    assert ok.get_json()["chain_job_id"]
    assert attest(client, rid).get_json()["ready"] is False  # resolveReview not mined yet
    run_jobs()
    assert fake_registry.verdict_of(r["report_hash"])["decision"] == "REVIEW_APPROVED"
    assert fake_registry.txs == ["submitVerdict", "resolveReview"]
    assert review(client, rid, reviewer_key, approve=False).status_code == 409  # already resolved

    a = attest(client, rid).get_json()
    assert a["call"] == "mint" and a["ready"] is True and a["image_hash"] == "0x" + r["image_sha256"]
    stored = client.get(f"/api/report/{rid}").get_json()
    assert stored["review"]["approved"] and stored["review"]["tx_hash"]
    with server.db.session() as s:
        actions = [a for (a,) in s.execute(text("SELECT action FROM audit_log ORDER BY id"))]
    assert actions == [
        "auth.signin",
        "submission.created",
        "report.created",
        "verdict.submitted",
        "review.resolved",
        "review.resolved_on_chain",
    ]


def test_rejected_review_cannot_mint(server, client, analyse, run_jobs, reviewer_key):
    from jobs import tasks

    tasks.pipeline().resnet.value = 0.99
    rid = analyse("photo.png")["report_id"]
    review(client, rid, reviewer_key, approve=False)
    run_jobs()
    assert attest(client, rid).status_code == 403


def test_copy_of_protected_painting_fails_and_is_anchored(server, client, analyse, run_jobs, fake_registry):
    r = analyse("painting.png")  # identical to the protected reference "art/painting.png"
    assert r["decision"] == "FAIL"
    assert r["checks"]["copy_exact"]["value"] == 0 and r["checks"]["copy_exact"]["nearest"] == "art/painting.png"
    resp = attest(client, r["report_id"])
    assert resp.status_code == 403 and "blocked by Mint-Gate policy" in resp.get_json()["error"]
    run_jobs()
    assert fake_registry.verdict_of(r["report_hash"])["decision"] == "FAIL"  # failures stay on record


def test_anchoring_retries_when_the_chain_is_down(server, analyse, run_jobs, fake_registry, fake_redis):
    from rq import Queue
    from rq.registry import ScheduledJobRegistry

    from jobs import tasks

    tasks.pipeline().resnet.value = 0.99
    fake_registry.down = True
    r = analyse("photo.png")
    run_jobs()
    scheduled = ScheduledJobRegistry(queue=Queue("chain", connection=fake_redis))
    assert len(scheduled) == 1  # RQ will retry with back-off
    fake_registry.down = False
    tasks.anchor_verdict(r["report_id"], "localtest")  # what the retry runs
    assert fake_registry.verdict_of(r["report_hash"])["decision"] == "REVIEW"
    tasks.anchor_verdict(r["report_id"], "localtest")  # idempotent
    assert fake_registry.txs == ["submitVerdict"]


def test_ai_generated_upload_fails(server, analyse):
    from jobs import tasks

    tasks.pipeline().ai.value = 0.97
    r = analyse("photo.png")
    assert r["decision"] == "FAIL"
    assert "AI-generated probability (CLIP probe + spectral) crossed the hard-fail threshold" in r["reasons"]


def test_minted_artwork_protects_against_reupload(server, client, analyse, wallet):
    """The indexer sees ImageMinted -> reference_items row -> every worker adds it to its index."""
    from chain import networks
    from indexer.core import Indexer

    first = analyse("photo.png")
    net = networks.get("localtest")
    ix = Indexer(net, w3=server.Web3())
    with server.db.session() as s:
        ix.apply(
            s,
            {
                "block": 5,
                "tx": "0x" + "ab" * 32,
                "contract": net.registry.address,
                "event": "ImageMinted",
                "args": {
                    "imageHash": "0x" + first["image_sha256"],
                    "reportHash": "0x" + first["report_hash"],
                    "collection": server.Web3.to_checksum_address(net.nft.address),
                    "tokenId": 1,
                    "artist": ARTIST,
                },
            },
        )
    assert client.get(f"/api/report/{first['report_id']}").get_json()["status"] == "MINTED"
    assert attest(client, first["report_id"]).status_code == 403
    tok = client.get("/api/token/1").get_json()
    assert tok["report_id"] == first["report_id"] and tok["owner"] == ARTIST and tok["status"] == "active"

    second = analyse("photo.png", wallet=wallet(OTHER_KEY))
    assert second["decision"] == "FAIL"
    assert second["checks"]["copy_exact"]["nearest"] == f"minted/{first['report_id']}.png"


def test_verify_detects_tampering(server, client, analyse, run_jobs):
    r = analyse("painting.png")  # FAIL -> anchored by the chain worker
    rid = r["report_id"]
    run_jobs()
    v = client.get(f"/api/verify/{rid}").get_json()
    assert v["report_untampered"] and v["on_chain_matches"]
    assert v["recomputed_hash"] == v["report_hash"] == r["report_hash"]

    with server.db.session() as s:  # someone edits the database
        s.execute(text("UPDATE reports SET score = 100, body = jsonb_set(body, '{score}', '100')"))
    v = client.get(f"/api/verify/{rid}").get_json()
    assert not v["report_untampered"]
    assert not v["on_chain_matches"]  # the chain still holds the original score


def test_verify_when_chain_down(client, analyse, fake_registry):
    rid = analyse("photo.png")["report_id"]
    fake_registry.down = True
    assert client.get(f"/api/verify/{rid}").status_code == 503


def test_status(client, analyse):
    analyse("photo.png")
    s = client.get("/api/status").get_json()
    assert s["policy"]["version"]
    assert s["queues"] == {"analysis": 0, "chain": 0}
    assert s["networks"]["localtest"]["chain_id"] == 31337
    assert s["chain"]["connected"] is False  # nothing indexed yet
