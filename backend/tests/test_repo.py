"""Repository layer + schema against the test Postgres (see conftest.py `database`)."""

import json
import time

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from chain.eip712 import Verdict
from db import repo
from db.models import ChainEvent
from tests.conftest import ARTIST

REPORT = {
    "report_id": "a" * 32,
    "created_at": 1_800_000_000,
    "filename": "x.png",
    "image_sha256": "bb" * 32,
    "decision": "PASS",
    "score": 91,
    "policy_version": "3.1.0",
    "policy_sha256": "cc" * 32,
    "models": {"clip": "openai/clip-vit-base-patch32"},
    "checks": {"clip": {"value": 0.123456789, "status": "PASS", "risk": 0.1}},
    "reasons": [],
    "report_hash": "dd" * 32,
}


@pytest.fixture
def report(clean_db):
    with clean_db.session() as s:
        sub = repo.create_submission(s, REPORT["report_id"], ARTIST.lower(), "x.png")
        repo.save_report(s, sub, dict(REPORT), None)
    return REPORT["report_id"]


def test_models_match_migrations(database):
    """Autogenerate finds nothing to change: the ORM and the Alembic schema agree."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from db.models import Base

    with database.engine().connect() as c:
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []


def test_artist_upsert_is_idempotent(clean_db):
    with clean_db.session() as s:
        a = repo.get_or_create_artist(s, ARTIST.lower())
        b = repo.get_or_create_artist(s, ARTIST)
        assert a.id == b.id and a.wallet == ARTIST  # stored checksummed


def test_report_roundtrip_keeps_the_hash(report, clean_db):
    from detection.report_hash import canonical_hash

    with clean_db.session() as s:
        d = repo.report_dict(repo.get_report(s, report))
    assert d["status"] == "PASS" and d["artist"] == ARTIST
    assert canonical_hash(d) == canonical_hash(REPORT)  # JSONB round trip changes no attested value
    assert d["attestations"] == []


def test_submission_and_report_constraints(report, clean_db):
    with pytest.raises(IntegrityError), clean_db.session() as s:  # one report per submission
        repo.save_report(s, repo.get_submission(s, report), REPORT | {"report_id": "b" * 32}, None)
    with pytest.raises(IntegrityError), clean_db.session() as s:  # report hashes are unique
        sub = repo.create_submission(s, "c" * 32, ARTIST, "y.png")
        repo.save_report(s, sub, REPORT | {"report_id": "c" * 32}, None)


def test_audit_log_is_append_only(clean_db):
    with clean_db.session() as s:
        repo.audit(s, "api", "test.action", "t1", n=1)
    for stmt in ("UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(DBAPIError, match="append-only"), clean_db.session() as s:
            s.execute(text(stmt))
    with clean_db.session() as s:
        assert s.execute(text("SELECT actor, meta FROM audit_log")).one() == ("api", {"n": 1})


def test_attestation_reuse_and_expiry(report, clean_db):
    registry = "0x" + "11" * 20
    now = int(time.time())

    def verdict(nonce, expires_in):
        base = Verdict.from_report(REPORT, ARTIST, now=now - 10).__dict__
        return Verdict(**(base | {"nonce": nonce, "expires_at": now + expires_in}))

    with clean_db.session() as s:
        repo.add_attestation(s, report, 31337, registry, verdict(1, 60), "0x" + "00" * 65)  # expires too soon
        assert repo.usable_attestation(s, report, 31337, registry) is None
        good = repo.add_attestation(s, report, 31337, registry, verdict(2, 3600), "0x" + "00" * 65)
        assert repo.usable_attestation(s, report, 31337, registry).id == good.id
        assert repo.usable_attestation(s, report, 84532, registry) is None  # other chain
        with pytest.raises(IntegrityError), s.begin_nested():  # a nonce is never stored twice
            repo.add_attestation(s, report, 31337, registry, verdict(2, 3600), "0x" + "00" * 65)

        assert repo.mark_verdict_submitted(s, 31337, registry, REPORT["report_hash"], "0x" + "ab" * 32) == 2
        spent = repo.usable_attestation(s, report, 31337, registry, min_remaining_s=10**6)
        assert spent.submitted_tx == "0x" + "ab" * 32  # submitted: usable regardless of expiry


def test_chain_events_are_unique(clean_db):
    ev = dict(
        chain_id=1, block=5, block_hash="0x00", tx="0x" + "aa" * 32, log_index=0, contract="0x0", event="X", args={}
    )
    with clean_db.session() as s:
        s.add(ChainEvent(**ev))
    with pytest.raises(IntegrityError), clean_db.session() as s:
        s.add(ChainEvent(**ev))


def test_copy_index_on_pgvector(clean_db):
    """Fingerprints round-trip through bit(256) / halfvec; PDQ, SSCD and fused KNN search work."""
    from detection.copy_engine import Fingerprint
    from detection.copy_index import PgCopyIndex
    from ingest.embed import load_fingerprint, store_fingerprint, update_nn

    def unit(i, j=None):
        v = np.zeros(512, np.float32)
        v[i] = 1.0
        if j is not None:
            v[j] = 1.0
        return v / np.linalg.norm(v)

    def fpr(i, pdq_byte, j=None):  # SSCD only (no DINOv2): copy_vec = sscd
        return Fingerprint(bytes([pdq_byte]) + b"\0" * 31, 90, None, unit(i, j))

    with clean_db.session() as s:
        for uri in ("minted/a.png", "minted/b.png", "minted/c.png"):
            repo.add_reference(s, "mintgate", uri)
        repo.add_reference(s, "mintgate", "minted/a.png")  # duplicate URI: ignored
    with clean_db.session() as s:
        rows = repo.references_after(s, 0, sources=("mintgate",))
        assert [r.image_uri for r in rows] == ["minted/a.png", "minted/b.png", "minted/c.png"]
        assert {r.status for r in rows} == {"pending"}
        store_fingerprint(s, rows[0].id, fpr(0, 0xFF), 1.0)
        store_fingerprint(s, rows[1].id, fpr(1, 0x00), 1.0)
        store_fingerprint(s, rows[2].id, fpr(0, 0x0F, j=2), 1.0)  # close to a
        update_nn(s, [r.id for r in rows])
    idx = PgCopyIndex(fused=False)
    near = idx.exact(np.frombuffer(bytes([0xFE]) + b"\0" * 31, np.uint8)[None], 4)
    assert [(r.name, d) for r, d in near] == [("minted/a.png", 1)]
    q = unit(0)[:512]
    assert [r.name for r in idx.knn_sscd(q, 2)] == ["minted/a.png", "minted/c.png"]
    assert [r.name for r in idx.knn(q, 1)] == ["minted/a.png"]
    with clean_db.session() as s:
        a, b, c = repo.references_after(s, 0)
        assert a.status == "ready" and np.isclose(a.nn_sscd, 1 / np.sqrt(2), atol=1e-3)  # its neighbour is c
        assert np.isclose(b.nn_sscd, 0.0, atol=1e-3)
        back = load_fingerprint(a)
        assert back.pdq == bytes([0xFF]) + b"\0" * 31 and np.allclose(back.sscd, q, atol=1e-3)


def _legacy(rid, status, mint=None, review=None):
    return {
        **REPORT,
        "report_id": rid,
        "report_hash": rid[:1] * 64,
        "status": status,
        "chain": {"anchored": True, "tx_hash": "0x" + "01" * 32, "block": 3},
        "review": review,
        "mint": mint,
        "evidence": {"copy_move_heatmap": "iVBOR", "orb_matches": None},
    }


def test_migrate_json(clean_db, tmp_path):
    from db.migrate_json import migrate

    minted = {"recipient": ARTIST.lower(), "token_id": 4, "token_uri": "ipfs://x", "tx_hash": "0x" + "02" * 32}
    files = {
        "1" * 32: _legacy("1" * 32, "MINTED", mint=minted),
        "2" * 32: _legacy("2" * 32, "REVIEW_REJECTED", review={"approved": False, "note": "AI"}),
        "3" * 32: _legacy("3" * 32, "FAIL"),
    }
    for rid, r in files.items():
        (tmp_path / f"{rid}.json").write_text(json.dumps(r))

    assert migrate(tmp_path, dry_run=True) == {"imported": 3, "skipped": 0, "minted": 1}
    with clean_db.session() as s:
        assert s.execute(text("SELECT count(*) FROM reports")).scalar() == 0  # dry run wrote nothing

    assert migrate(tmp_path) == {"imported": 3, "skipped": 0, "minted": 1}
    assert migrate(tmp_path) == {"imported": 0, "skipped": 3, "minted": 0}  # idempotent
    with clean_db.session() as s:
        m = repo.report_dict(repo.get_report(s, "1" * 32))
        assert m["status"] == "MINTED" and m["artist"] == ARTIST
        assert m["legacy_v1"]["mint"]["token_id"] == 4 and m["legacy_v1"]["chain"]["block"] == 3
        r = repo.report_dict(repo.get_report(s, "2" * 32))
        assert r["status"] == "REVIEW_REJECTED" and r["review"]["approved"] is False and r["artist"] is None
        assert [x.image_uri for x in repo.references_after(s, 0)] == [f"minted/{'1' * 32}.png"]
        assert s.execute(text("SELECT count(*) FROM audit_log WHERE action = 'legacy.imported'")).scalar() == 3
