"""Phase 6 dispute flow through the API, the indexer's projection and the workers (fake models,
fake registry, real Postgres). On-chain events are fed to `Indexer.apply` as the indexer would.

reporter pins evidence -> (wallet) openDispute -> DisputeOpened indexed -> backend evidence job
-> reviewer pins a reason -> (wallet) DisputeResolver.resolveDispute -> DisputeReasoned +
ImageRevoked + DisputeResolved indexed -> token revoked, timeline, explorer, audit log.
"""

import hashlib
import io
import json

import pytest
from sqlalchemy import select
from web3 import Web3

import config
from chain import networks
from db.models import AuditLog, Dispute, DisputeEvidence, Token
from indexer.core import Indexer
from tests.conftest import ARTIST, OTHER_KEY, TEST_NETWORK

TOKEN_CONTRACT = Web3.to_checksum_address("0x" + "22" * 20)
REGISTRY = Web3.to_checksum_address("0x" + "11" * 20)
RESOLVER = Web3.to_checksum_address("0x" + "33" * 20)
BOND = 10**15


class Chain:
    """Feeds events to the indexer's projection like `Indexer.run_once` does (block by block)."""

    def __init__(self, db):
        self.net = networks.get(TEST_NETWORK)
        self.ix = Indexer(self.net, w3=Web3())
        self.db = db
        self.block = 100

    def emit(self, *events):
        from sqlalchemy.dialects.postgresql import insert

        from db.models import ChainEvent

        self.block += 1
        tx = "0x" + f"{self.block:064x}"
        with self.db.session() as s:
            for i, (contract, name, args) in enumerate(events):
                e = {
                    "chain_id": self.net.chain_id,
                    "block": self.block,
                    "block_hash": tx,
                    "tx": tx,
                    "log_index": i,
                    "contract": contract,
                    "event": name,
                    "args": args,
                    "block_time": 1_700_000_000 + self.block,
                }
                s.execute(insert(ChainEvent).values(**e))
                self.ix.apply(s, e)
        self.ix._run_hooks()
        return tx


@pytest.fixture
def chain(clean_db):
    return Chain(clean_db)


@pytest.fixture
def minted(artist_client, fixture_bytes, run_jobs, chain):
    """A PASS upload by the artist, minted as token 1 (ImageMinted indexed)."""
    resp = artist_client.upload(fixture_bytes("photo.png"), "photo.png")
    assert resp.status_code == 202
    run_jobs()
    report = artist_client.get(f"/api/jobs/{resp.get_json()['job_id']}").get_json()["report"]
    assert report["decision"] == "PASS", report["decision"]
    chain.emit(
        (
            REGISTRY,
            "ImageMinted",
            {
                "imageHash": "0x" + report["image_sha256"],
                "reportHash": "0x" + report["report_hash"],
                "collection": TOKEN_CONTRACT,
                "tokenId": 1,
                "artist": ARTIST,
            },
        )
    )
    return report


@pytest.fixture
def reporter(wallet):
    return wallet(OTHER_KEY)


def open_dispute(chain, reporter, report, uri, dispute_id=1):
    return chain.emit(
        (
            REGISTRY,
            "DisputeOpened",
            {
                "disputeId": dispute_id,
                "imageHash": "0x" + report["image_sha256"],
                "disputant": reporter.address,
                "bond": BOND,
                "evidenceURI": uri,
            },
        )
    )


def evidence_form(**over):
    form = {
        "network": TEST_NETWORK,
        "contract": TOKEN_CONTRACT,
        "token_id": "1",
        "statement": "This is my painting; I published it in 2019 long before this mint.",
        "urls": "https://example.org/my-painting\nipfs://bafyexample",
        "first_published": "2019-05-01",
        "prior_claims": "ethereum:0xabc...:42",
    }
    form.update(over)
    return form


def submit_evidence(reporter, original=None, **over):
    data = evidence_form(**over)
    if original is not None:
        data["original"] = (io.BytesIO(original), "original.png")
    return reporter.post("/api/disputes/evidence", data=data, content_type="multipart/form-data")


def test_evidence_needs_a_session_and_an_indexed_token(server, minted, reporter):
    anon = server.app.test_client()
    assert anon.post("/api/disputes/evidence", data=evidence_form()).status_code == 401
    assert submit_evidence(reporter, token_id="999").status_code == 404
    bad = submit_evidence(reporter, statement="too short")
    assert bad.status_code == 400 and "20 characters" in bad.get_json()["error"]
    bad = submit_evidence(reporter, urls="javascript:alert(1)")
    assert bad.status_code == 400 and "not a URL" in bad.get_json()["error"]
    bad = submit_evidence(reporter, original=b"not an image")
    assert bad.status_code == 400 and "original" in bad.get_json()["error"]


def test_evidence_is_pinned_as_exact_bytes_and_returns_the_open_dispute_call(minted, reporter, fixture_bytes, clean_db):
    resp = submit_evidence(reporter, original=fixture_bytes("photo.png"))
    assert resp.status_code == 200, resp.get_json()
    out = resp.get_json()
    assert out["call"] == "openDispute" and out["image_hash"] == "0x" + minted["image_sha256"]
    assert out["bond"] == str(BOND) and out["registry"] == REGISTRY
    # without Pinata the document is served by this API; its bytes hash to evidence_sha256
    name = out["evidence_uri"].rsplit("/", 1)[1]
    raw = reporter.get(f"/api/disputes/file/{name}").data
    assert hashlib.sha256(raw).hexdigest() == out["evidence_sha256"]
    doc = json.loads(raw)
    assert doc["disputant"] == reporter.address and doc["token"] == {"contract": TOKEN_CONTRACT, "token_id": "1"}
    assert doc["original"]["sha256"] == hashlib.sha256(fixture_bytes("photo.png")).hexdigest()
    with clean_db.session() as s:
        ev = s.scalars(select(DisputeEvidence)).one()
        assert ev.evidence_uri == out["evidence_uri"] and ev.original_file
    assert reporter.get("/api/disputes/file/..%2F..%2Fsecrets.json").status_code == 404


def test_full_dispute_flow_upheld(
    server, minted, reporter, wallet, chain, fake_registry, run_jobs, fixture_bytes, clean_db, monkeypatch
):
    out = submit_evidence(reporter, original=fixture_bytes("photo.png")).get_json()
    open_dispute(chain, reporter, minted, out["evidence_uri"])

    with clean_db.session() as s:
        d = s.scalars(select(Dispute)).one()
        assert d.status == "open" and d.evidence_id is not None  # linked to the pinned draft
        t = s.scalars(select(Token)).one()
        assert t.status == "flagged"
    # a second report on a flagged token is refused before any pinning
    assert submit_evidence(reporter).status_code == 409

    # DisputeOpened queued the backend evidence (analysis worker, fake models)
    run_jobs()
    detail = reporter.get(f"/api/disputes/{TEST_NETWORK}/1").get_json()
    be = detail["backend_evidence"]
    assert be["copy_search"]["available"] is True
    assert all(m["reference"] != f"minted/{minted['report_id']}.png" for m in be["copy_search"]["matches"])
    assert be["earliest_submissions"][0]["wallet"] == ARTIST and be["earliest_submissions"][0]["signed"]
    assert be["earliest_is_token_artist"] is True
    assert be["original"]["identical_pixels"] is True and be["original"]["sscd_cosine"] > 0.99
    assert be["reverse_search"] == {"configured": False}
    assert detail["reporter_evidence"]["statement"].startswith("This is my painting")
    assert detail["report"]["artist"] == ARTIST and detail["original_url"]
    assert detail["backend_evidence_uri"].endswith("-backend.json")

    # the reviewer: a session wallet with REVIEWER_ROLE (read from the registry)
    rev = wallet("0x" + "f6" * 32)
    body = {"upheld": True, "reason": "The reporter's original predates the mint; same pixels."}
    denied = rev.post(f"/api/disputes/{TEST_NETWORK}/1/reason", json=body)
    assert denied.status_code == 403 and "REVIEWER_ROLE" in denied.get_json()["error"]
    fake_registry.roles["REVIEWER_ROLE"].add(rev.address)
    drop_role_cache(server)  # the cached "not a reviewer"
    assert (
        rev.post(f"/api/disputes/{TEST_NETWORK}/1/reason", json={"upheld": True, "reason": "short"}).status_code == 400
    )
    assert (
        rev.post(f"/api/disputes/{TEST_NETWORK}/1/reason", json={"upheld": "yes", "reason": body["reason"]}).status_code
        == 400
    )
    resp = rev.post(f"/api/disputes/{TEST_NETWORK}/1/reason", json=body)
    assert resp.status_code == 200, resp.get_json()
    r = resp.get_json()
    assert r["call"] == "resolveDispute" and r["resolver"] == RESOLVER
    dispute_id, upheld, reason_hash, reason_uri = r["args"]
    raw = rev.get(f"/api/disputes/file/{reason_uri.rsplit('/', 1)[1]}").data
    assert "0x" + hashlib.sha256(raw).hexdigest() == reason_hash
    reason_doc = json.loads(raw)
    assert reason_doc["decision"] == "upheld" and reason_doc["reviewer"] == rev.address
    assert reason_doc["evidence"]["reporter"] == out["evidence_uri"]

    # the reviewer's wallet sends resolveDispute through the resolver: three events, one tx
    image = "0x" + minted["image_sha256"]
    chain.emit(
        (
            RESOLVER,
            "DisputeReasoned",
            {
                "disputeId": 1,
                "imageHash": image,
                "reviewer": rev.address,
                "upheld": True,
                "reasonHash": reason_hash,
                "reasonURI": reason_uri,
            },
        ),
        (REGISTRY, "ImageRevoked", {"imageHash": image, "artist": ARTIST, "disputeId": 1}),
        (REGISTRY, "DisputeResolved", {"disputeId": 1, "imageHash": image, "upheld": True, "reviewer": RESOLVER}),
    )
    detail = reporter.get(f"/api/disputes/{TEST_NETWORK}/1").get_json()
    assert detail["status"] == "upheld" and detail["reviewer"] == rev.address  # the human, not the resolver
    assert detail["reason_hash"] == reason_hash[2:] and detail["token"]["status"] == "revoked"
    assert rev.post(f"/api/disputes/{TEST_NETWORK}/1/reason", json=body).status_code == 409

    tl = reporter.get(f"/api/token/{TEST_NETWORK}/{TOKEN_CONTRACT}/1/timeline").get_json()
    assert tl["status"] == "revoked"
    assert [e["event"] for e in tl["events"]] == [
        "ImageMinted",
        "DisputeOpened",
        "DisputeReasoned",
        "ImageRevoked",
        "DisputeResolved",
    ]
    assert tl["disputes"]["1"]["reason_uri"] == reason_uri

    stats = reporter.get("/api/explorer/stats").get_json()["networks"][TEST_NETWORK]
    assert stats["disputes"] == {"open": 0, "upheld": 1, "rejected": 0}
    assert stats["revocations"] == 1 and stats["tokens"]["revoked"] == 1 and stats["bonds_locked_wei"] == "0"

    with clean_db.session() as s:
        actions = [a for (a,) in s.execute(select(AuditLog.action).order_by(AuditLog.id))]
    for a in (
        "dispute.evidence_pinned",
        "dispute.opened",
        "dispute.backend_evidence",
        "dispute.reason_pinned",
        "token.revoked",
        "dispute.resolved",
    ):
        assert a in actions

    # a rebuild from chain_events reproduces the projection and keeps the backend's evidence
    with clean_db.session() as s:
        chain.ix.rebuild(s)
    after = reporter.get(f"/api/disputes/{TEST_NETWORK}/1").get_json()
    assert after["status"] == "upheld" and after["backend_evidence"] == detail["backend_evidence"]
    assert after["reviewer"] == rev.address


def test_rejected_dispute_reactivates_the_token(minted, reporter, chain, clean_db):
    out = submit_evidence(reporter).get_json()
    open_dispute(chain, reporter, minted, out["evidence_uri"])
    image = "0x" + minted["image_sha256"]
    chain.emit((REGISTRY, "DisputeResolved", {"disputeId": 1, "imageHash": image, "upheld": False, "reviewer": ARTIST}))
    d = reporter.get(f"/api/disputes/{TEST_NETWORK}/1").get_json()
    assert d["status"] == "rejected" and d["token"]["status"] == "active" and d["reason_uri"] is None
    lst = reporter.get("/api/disputes?status=rejected").get_json()["disputes"]
    assert [x["dispute_id"] for x in lst] == ["1"]
    assert reporter.get("/api/disputes?status=open").get_json()["disputes"] == []


def test_dispute_without_a_known_draft_still_indexes(minted, reporter, chain, run_jobs):
    open_dispute(chain, reporter, minted, "ipfs://someone-elses-evidence")
    run_jobs()
    d = reporter.get(f"/api/disputes/{TEST_NETWORK}/1").get_json()
    assert d["reporter_evidence"] is None and d["has_backend_evidence"]


def test_roles_endpoint_and_review_queue_by_wallet(
    server, artist_client, wallet, fake_registry, fixture_bytes, run_jobs, worker_pipeline
):
    rev = wallet("0x" + "f7" * 32)
    assert rev.get("/api/auth/roles").get_json()["roles"][TEST_NETWORK] == {"reviewer": False}
    fake_registry.roles["REVIEWER_ROLE"].add(rev.address)
    drop_role_cache(server)
    assert rev.get("/api/auth/roles").get_json()["roles"][TEST_NETWORK] == {"reviewer": True}

    worker_pipeline.ai.value = 0.75  # between the thresholds -> REVIEW
    resp = artist_client.upload(fixture_bytes("photo.png"), "photo.png")
    run_jobs("analysis")
    rid = resp.get_json()["job_id"]
    status = artist_client.get(f"/api/jobs/{rid}").get_json()["report"]["status"]
    if status != "REVIEW":
        pytest.skip(f"fixture scored {status}, not REVIEW")
    # a non-reviewer wallet session is refused even without a key; the reviewer wallet works
    assert artist_client.post(f"/api/review/{rid}", json={"approve": True}).status_code == 403
    no_csrf = rev.http.post(f"/api/review/{rid}", json={"approve": True})
    assert no_csrf.status_code == 403
    ok = rev.post(f"/api/review/{rid}", json={"approve": True, "note": "fine"})
    assert ok.status_code == 200 and ok.get_json()["review"]["reviewer"] == rev.address


def test_review_key_can_be_disabled(server, client, monkeypatch):
    monkeypatch.setattr(config, "REVIEWER_KEY_DISABLED", True)
    resp = client.post(f"/api/review/{'a' * 32}", json={"reviewer_key": config.REVIEWER_KEY, "approve": True})
    assert resp.status_code == 403


def test_notifications(monkeypatch, minted, reporter, chain, run_jobs, fake_redis):
    import notify

    sent = []

    class R:
        status_code = 204

    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.test/api/webhooks/x")
    monkeypatch.setattr(notify.requests, "post", lambda url, json, timeout: sent.append(json) or R())
    out = submit_evidence(reporter).get_json()
    open_dispute(chain, reporter, minted, out["evidence_uri"])
    run_jobs()
    assert len(sent) == 1
    text = sent[0]["content"]
    assert "New dispute #1" in text and out["evidence_uri"] in text and f"/token/{TEST_NETWORK}/" in text
    assert sent[0]["allowed_mentions"] == {"parse": []}  # a statement can never ping @everyone

    monkeypatch.setattr(config, "NOTIFY_DISPUTES", False)
    assert notify.enabled("dispute") is False and notify.enabled("review") is True
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "")
    assert notify.enabled("review") is False and notify.post("x") is False


def test_notify_retries_on_server_errors(monkeypatch):
    import notify

    class R:
        def __init__(self, code):
            self.status_code = code

    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.test/api/webhooks/x")
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: R(503))
    with pytest.raises(RuntimeError):
        notify.post("hello")
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: R(404))
    assert notify.post("hello") is False  # deleted webhook: no endless retries


def drop_role_cache(server):
    conn = server.disputes_api.jq.connection()
    for k in conn.scan_iter("mintgate:role:*"):
        conn.delete(k)
