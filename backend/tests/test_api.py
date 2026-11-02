"""Flask API end to end, with fake models and an in-memory chain (see conftest.py)."""

import io
import json

import pytest

ARTIST = "0x00000000000000000000000000000000000000a1"


@pytest.fixture
def reviewer_key(server):
    return server.config.REVIEWER_KEY


@pytest.fixture
def analyse(client, fixture_bytes):
    def run(name):
        resp = client.post(
            "/api/analyze", data={"image": (io.BytesIO(fixture_bytes(name)), name)}, content_type="multipart/form-data"
        )
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()["report"]

    return run


def mint(client, rid, recipient=ARTIST):
    return client.post(f"/api/mint/{rid}", json={"recipient": recipient})


def test_analyze_requires_image(client):
    assert client.post("/api/analyze").status_code == 400


def test_analyze_rejects_invalid_bytes(client):
    resp = client.post(
        "/api/analyze",
        data={"image": (io.BytesIO(b"definitely not a png"), "x.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "not a valid image" in resp.get_json()["error"]


def test_security_headers(client):
    resp = client.get("/healthz")
    assert resp.get_json() == {"ok": True, "references": 2}
    assert "script-src 'self'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_genuine_upload_passes_and_is_anchored(server, analyse):
    r = analyse("photo.png")
    assert r["decision"] == r["status"] == "PASS", r["reasons"]
    assert set(r["checks"]) == {"resnet", "ai_detector", "phash", "orb", "copy_move", "clip"}
    assert r["chain"]["anchored"] is True
    onchain = server.chain().verification(r["report_hash"])
    assert (onchain["decision"], onchain["score"]) == ("PASS", r["score"])
    assert (server.config.UPLOADS_DIR / f"{r['report_id']}.png").exists()


def test_review_flow(server, client, analyse, reviewer_key):
    server.pipeline.resnet.value = 0.99  # REVIEW-only check: may flag, never fail
    r = analyse("photo.png")
    rid = r["report_id"]
    assert r["decision"] == "REVIEW"
    assert r["checks"]["resnet"]["status"] == "REVIEW"

    assert mint(client, rid).status_code == 403  # not approved yet
    bad = client.post(f"/api/review/{rid}", json={"reviewer_key": "wrong", "approve": True})
    assert bad.status_code == 403
    assert client.get(f"/api/report/{rid}").get_json()["status"] == "REVIEW"
    assert [x["report_id"] for x in client.get("/api/reports?status=REVIEW").get_json()] == [rid]

    ok = client.post(
        f"/api/review/{rid}",
        json={"reviewer_key": reviewer_key, "approve": True, "note": "hand-drawn, checked sketches"},
    )
    assert ok.status_code == 200 and ok.get_json()["status"] == "REVIEW_APPROVED"
    assert server.chain().verification(r["report_hash"])["decision"] == "REVIEW_APPROVED"
    again = client.post(f"/api/review/{rid}", json={"reviewer_key": reviewer_key, "approve": False})
    assert again.status_code == 409  # already resolved

    assert mint(client, rid, recipient="not-an-address").status_code == 400
    minted = mint(client, rid)
    assert minted.status_code == 200, minted.get_json()
    assert minted.get_json()["mint"]["token_id"] == 1
    assert client.get(f"/api/report/{rid}").get_json()["status"] == "MINTED"
    meta = json.loads((server.config.METADATA_DIR / f"{rid}.json").read_text())
    assert meta["mint_gate"]["report_hash"] == r["report_hash"]
    assert mint(client, rid).status_code == 403  # MINTED is not mintable


def test_rejected_review_cannot_mint(client, server, analyse, reviewer_key):
    server.pipeline.resnet.value = 0.99
    rid = analyse("photo.png")["report_id"]
    client.post(f"/api/review/{rid}", json={"reviewer_key": reviewer_key, "approve": False})
    assert mint(client, rid).status_code == 403


def test_copy_of_protected_painting_fails_and_cannot_mint(server, client, analyse):
    r = analyse("painting.png")  # identical to the protected reference "art/painting.png"
    assert r["decision"] == "FAIL"
    assert r["checks"]["clip"]["value"] == 1.0
    assert r["checks"]["phash"]["value"] == 0
    assert r["checks"]["clip"]["nearest"] == "art/painting.png"
    resp = mint(client, r["report_id"])
    assert resp.status_code == 403 and "blocked by Mint-Gate policy" in resp.get_json()["error"]
    assert server.chain().tokens == {}
    assert server.chain().verification(r["report_hash"])["decision"] == "FAIL"  # failures stay on record


def test_ai_generated_upload_fails(server, analyse):
    server.pipeline.ai.value = 0.97
    r = analyse("photo.png")
    assert r["decision"] == "FAIL"
    assert "AI-generated probability (CLIP probe + spectral) crossed the hard-fail threshold" in r["reasons"]


def test_minted_artwork_protects_against_reupload(server, client, analyse):
    first = analyse("photo.png")
    assert mint(client, first["report_id"]).status_code == 200
    assert "minted/" + first["report_id"] + ".png" in server.pipeline.index.files

    second = analyse("photo.png")
    assert second["decision"] == "FAIL"
    assert second["checks"]["clip"]["nearest"] == f"minted/{first['report_id']}.png"


def test_chain_rule_backs_up_server_gate(server, client, analyse):
    """Even if the server-side status were wrong, the contract refuses a second mint of one image."""
    a, b = analyse("photo.png"), analyse("photo.png")  # both PASS before either is minted
    assert a["decision"] == b["decision"] == "PASS"
    assert mint(client, a["report_id"]).status_code == 200
    resp = mint(client, b["report_id"])
    assert resp.status_code == 409
    assert "already been minted" in resp.get_json()["error"]


def test_verify_detects_tampering(server, client, analyse):
    r = analyse("photo.png")
    rid = r["report_id"]
    v = client.get(f"/api/verify/{rid}").get_json()
    assert v["report_untampered"] and v["on_chain_matches"]
    assert v["recomputed_hash"] == v["report_hash"] == r["report_hash"]

    path = server.config.REPORTS_DIR / f"{rid}.json"
    stored = json.loads(path.read_text())
    stored["score"] = 100
    stored["decision"] = "PASS"
    path.write_text(json.dumps(stored))
    v = client.get(f"/api/verify/{rid}").get_json()
    assert not v["report_untampered"]
    assert not v["on_chain_matches"]  # the chain still holds the original score


def test_unanchored_report_cannot_verify(client):
    assert client.get("/api/verify/" + "0" * 32).status_code == 404
    assert client.get("/api/verify/not-a-report-id").status_code == 404


def test_anchoring_failure_is_retried_at_mint(server, client, analyse, monkeypatch):
    def down(report):
        raise server.ChainUnavailable("node down")

    monkeypatch.setattr(server.chain(), "record", down)
    r = analyse("photo.png")
    assert r["decision"] == "PASS" and r["chain"]["anchored"] is False
    monkeypatch.undo()
    assert mint(client, r["report_id"]).status_code == 200
    assert client.get(f"/api/report/{r['report_id']}").get_json()["chain"]["anchored"] is True
