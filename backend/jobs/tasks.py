"""RQ tasks, run by `python -m jobs.worker <queue>`.

Analysis workers load the models once (first job) and keep them; before every analysis they pull
new `reference_items` rows into their in-memory index, so an artwork minted anywhere (seen by the
indexer) protects against re-uploads in every worker.
"""

import json
import logging

import numpy as np

import config
import db
from chain import networks, registry
from chain.eip712 import Verdict, sign_verdict
from chain.registry import ChainUnavailable
from db import repo
from db.models import Attestation
from detection.preprocessing import InvalidImage, load_path
from jobs import queue as jq

log = logging.getLogger("mintgate.jobs")
MINTED_PREFIX = "minted/"
BACKEND_VERDICT_VALIDITY = 3600  # REVIEW / FAIL verdicts are submitted right away

_pipeline = None
_ref_cursor = 0


def pipeline():
    global _pipeline
    if _pipeline is None:
        from detection.pipeline import MintGatePipeline  # torch + CLIP: loaded once per worker

        _pipeline = MintGatePipeline()
    return _pipeline


def set_pipeline(p):
    """Tests inject a pipeline with fake models."""
    global _pipeline, _ref_cursor
    _pipeline, _ref_cursor = p, 0


def sync_references(p) -> int:
    """Add reference_items rows this worker has not seen to its index; fills missing fingerprints."""
    global _ref_cursor
    pos, hold, added = _ref_cursor, None, 0
    while True:
        with db.session() as s:
            rows = repo.references_after(s, pos)
            for r in rows:
                pos = r.id
                if not r.image_uri.startswith(MINTED_PREFIX):  # Phase 4 sources are searched in pgvector
                    continue
                rid = r.image_uri[len(MINTED_PREFIX) :].removesuffix(".png")
                if r.embedding is None:
                    path = config.UPLOADS_DIR / f"{rid}.png"
                    if not path.exists():  # image not on this host (yet): retry next time
                        hold = r.id if hold is None else hold
                        continue
                    ph, emb = p.fingerprint(load_path(path))
                    repo.set_fingerprint(s, r.id, ph, emb.tolist())
                else:
                    ph, emb = repo.to_unsigned64(r.phash), np.asarray(r.embedding, dtype=np.float32)
                added += p.add_reference(rid, ph, emb)
        if not rows:
            break
    _ref_cursor = pos if hold is None else hold - 1
    return added


def analyse_submission(sid: str) -> dict:
    with db.session() as s:
        sub = repo.get_submission(s, sid, lock=True)
        # "processing" here means the previous worker died mid-job and RQ retries it. Finished,
        # invalid and errored submissions are final (an exception below is not retried).
        if sub is None or sub.status not in repo.PENDING:
            return {"skipped": sub.status if sub else "missing"}
        sub.status = "processing"
        filename = sub.filename
    raw_path = config.INCOMING_DIR / sid
    try:
        p = pipeline()
        sync_references(p)
        report, img = p.analyse(raw_path.read_bytes(), filename, report_id=sid)
    except InvalidImage as exc:
        with db.session() as s:
            repo.set_status(s, sid, "invalid", str(exc))
        raw_path.unlink(missing_ok=True)
        return {"status": "invalid"}
    except Exception:
        with db.session() as s:
            repo.set_status(s, sid, "error", "analysis failed")
        raise

    (config.UPLOADS_DIR / f"{sid}.png").write_bytes(img.png_bytes)
    evidence = report.pop("evidence")
    (config.EVIDENCE_DIR / f"{sid}.json").write_text(json.dumps(evidence))
    with db.session() as s:
        sub = repo.get_submission(s, sid, lock=True)
        repo.save_report(s, sub, report, f"evidence/{sid}.json")
        repo.audit(
            s,
            "worker",
            "report.created",
            sid,
            decision=report["decision"],
            score=report["score"],
            hash=report["report_hash"],
        )
    raw_path.unlink(missing_ok=True)

    decision = report["decision"]
    if decision == "REVIEW" or (decision == "FAIL" and config.ANCHOR_FAILED_VERDICTS):
        if config.DEFAULT_NETWORK in networks.available():
            try:
                jq.enqueue_chain("anchor_verdict", sid, config.DEFAULT_NETWORK)
            except Exception as exc:  # the report is stored; anchoring is retried at review time
                log.warning("could not queue anchoring of %s: %s", sid, exc)
    return {"status": decision, "report_id": sid}


def anchor_verdict(rid: str, network: str) -> dict:
    """Sign and submit a REVIEW / FAIL verdict (the artist submits PASS verdicts when minting)."""
    net = networks.get(network)
    key = net.signer_key()
    if not key:
        raise ChainUnavailable(f"no verifier key for {network}")
    with db.session() as s:
        rep = repo.get_report(s, rid)
        if rep is None or rep.submission.artist is None:
            return {"skipped": "no report or no artist wallet"}
        att = repo.usable_attestation(s, rid, net.chain_id, net.registry.address, min_remaining_s=300)
        if att is None:
            v = Verdict.from_report(repo.report_dict(rep), rep.submission.artist.wallet, BACKEND_VERDICT_VALIDITY)
            sig = sign_verdict(v, key, net.chain_id, net.registry.address)
            att = repo.add_attestation(s, rid, net.chain_id, net.registry.address, v, sig)
        if att.submitted_tx:
            return {"tx_hash": att.submitted_tx}
        att_id, verdict, sig, report_hash = att.id, Verdict.from_json(att.verdict), att.signature, rep.report_hash

    c = registry.client(net, jq.connection())
    if c.verdict_of(report_hash)["decision"] != "NONE":
        return {"already_on_chain": True}  # the indexer records the tx from VerdictSubmitted
    tx = c.submit_verdict(verdict, sig)
    with db.session() as s:
        s.get(Attestation, att_id).submitted_tx = tx["tx_hash"]
        repo.audit(s, "worker", "verdict.submitted", rid, network=network, **tx)
    return tx


def resolve_review(rid: str, approved: bool, network: str) -> dict:
    """Mirror a reviewer's decision on chain (anchoring the REVIEW verdict first if needed)."""
    anchor_verdict(rid, network)
    net = networks.get(network)
    c = registry.client(net, jq.connection())
    with db.session() as s:
        report_hash = repo.get_report(s, rid).report_hash
    onchain = c.verdict_of(report_hash)["decision"]
    if onchain == "REVIEW":
        tx = c.resolve_review(report_hash, approved)
    elif onchain in ("REVIEW_APPROVED", "REVIEW_REJECTED"):
        tx = {"tx_hash": None, "already": onchain}
    else:  # NONE: anchoring not mined yet -> RQ retries later
        raise RuntimeError(f"cannot resolve review of {rid}: on-chain decision is {onchain}")
    with db.session() as s:
        rep = repo.get_report(s, rid, lock=True)
        known = {k: v for k, v in tx.items() if v is not None}  # a re-run must not erase the tx hash
        rep.review = {**(rep.review or {}), "network": network, **known}
        if tx["tx_hash"]:
            repo.audit(s, "worker", "review.resolved_on_chain", rid, approved=approved, network=network, **tx)
    return tx
