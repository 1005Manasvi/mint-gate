"""Dispute API (Phase 6).

    GET  /api/disputes[?network=&status=]          disputes (public: everything is on chain / IPFS)
    GET  /api/disputes/<net>/<id>                  one dispute: token, reporter + backend evidence, reason
    GET  /api/disputes/file/<name>                 evidence / reason documents (local fallback without IPFS)
    GET  /api/disputes/original/<evidence id>      a reporter's original file
    POST /api/disputes/evidence                    reporter (SIWE session): pin evidence -> openDispute args
    GET  /api/auth/roles                           the session wallet's on-chain roles per network
    POST /api/disputes/<net>/<id>/reason           reviewer (session + REVIEWER_ROLE on that chain): pin the
                                                   written reason -> DisputeResolver.resolveDispute args
    POST /api/disputes/<net>/<id>/backend-evidence reviewer: (re)queue the backend's evidence job

The API never sends a transaction: the reporter's wallet pays the bond, the reviewer's wallet
resolves (so every decision is signed by an accountable reviewer key).
"""

import hashlib
import logging
import re
from decimal import Decimal

from flask import Blueprint, abort, g, jsonify, request, send_from_directory
from redis.exceptions import RedisError
from sqlalchemy import select
from web3 import Web3

import auth
import config
import db
from chain import ipfs, networks, registry
from db import repo
from db.models import ChainEvent, Dispute, DisputeEvidence, Report, Token
from disputes.evidence import EvidenceError, doc_bytes, pin_document, reason_document, reporter_document
from jobs import queue as jq

log = logging.getLogger("mintgate.disputes")
bp = Blueprint("disputes", __name__)
FILE_NAME = re.compile(r"^mintgate-[a-z0-9-]+\.(json|png|jpg|jpeg|webp|gif)$")
ROLE_CACHE_S = 60
ORIGINALS = "disputes/originals"
IMAGE_EXT = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif"}


def _error(msg, code=400):
    return jsonify({"success": False, "error": msg}), code


def _net(name):
    try:
        return networks.resolve(name)
    except networks.UnknownNetwork:
        abort(404)


def is_reviewer(net, wallet: str) -> bool:
    """REVIEWER_ROLE on that chain's registry, read live (cached 60 s: the Safe can revoke it)."""
    key = f"mintgate:role:{net.chain_id}:REVIEWER_ROLE:{wallet}"
    try:
        cached = jq.connection().get(key)
        if cached is not None:
            return cached == b"1"
    except RedisError:
        pass
    ok = registry.client(net, jq.connection()).has_role("REVIEWER_ROLE", wallet)
    try:
        jq.connection().set(key, b"1" if ok else b"0", ex=ROLE_CACHE_S)
    except RedisError:
        pass
    return ok


def _times(s, chain_id: int, txs: list[str]) -> dict:
    if not txs:
        return {}
    rows = s.execute(
        select(ChainEvent.tx, ChainEvent.block_time).where(ChainEvent.chain_id == chain_id, ChainEvent.tx.in_(txs))
    )
    return {tx: t for tx, t in rows}


def _token_of(s, d: Dispute) -> Token | None:
    return s.scalars(select(Token).where(Token.chain_id == d.chain_id, Token.image_sha256 == d.image_sha256)).first()


def dispute_summary(s, d: Dispute, names: dict, times: dict | None = None) -> dict:
    t = _token_of(s, d)
    times = times if times is not None else _times(s, d.chain_id, [x for x in (d.opened_tx, d.resolved_tx) if x])
    return {
        "network": names.get(d.chain_id),
        "chain_id": d.chain_id,
        "registry": d.registry_address,
        "dispute_id": str(d.dispute_id),
        "image_sha256": d.image_sha256,
        "disputant": d.disputant,
        "bond": str(d.bond),
        "status": d.status,
        "evidence_uri": d.evidence_uri,
        "evidence_http": ipfs.gateway_url(d.evidence_uri),
        "opened_tx": d.opened_tx,
        "opened_at": times.get(d.opened_tx),
        "resolved_tx": d.resolved_tx,
        "resolved_at": times.get(d.resolved_tx) if d.resolved_tx else None,
        "reviewer": d.reviewer,
        "reason_uri": d.reason_uri,
        "reason_http": ipfs.gateway_url(d.reason_uri) if d.reason_uri else None,
        "reason_hash": d.reason_hash,
        "has_backend_evidence": d.backend_evidence is not None,
        "token": {"contract": t.contract, "token_id": str(t.token_id), "status": t.status} if t else None,
    }


def _names():
    return {n.chain_id: n.name for n in networks.available().values()}


@bp.get("/api/disputes")
def list_disputes():
    q = select(Dispute).order_by(Dispute.opened_block.desc()).limit(200)
    if request.args.get("network"):
        q = q.where(Dispute.chain_id == _net(request.args["network"]).chain_id)
    if request.args.get("status") in ("open", "upheld", "rejected"):
        q = q.where(Dispute.status == request.args["status"])
    names = _names()
    with db.session() as s:
        rows = list(s.scalars(q))
        out = [dispute_summary(s, d, names) for d in rows]
    return jsonify({"success": True, "disputes": out})


def _get_dispute(s, net, dispute_id: int) -> Dispute:
    d = s.scalars(
        select(Dispute).where(
            Dispute.chain_id == net.chain_id,
            Dispute.registry_address == Web3.to_checksum_address(net.registry.address),
            Dispute.dispute_id == Decimal(dispute_id),
        )
    ).first()
    if d is None:
        abort(404)
    return d


@bp.get("/api/disputes/<network>/<int:dispute_id>")
def get_dispute(network, dispute_id):
    net = _net(network)
    with db.session() as s:
        d = _get_dispute(s, net, dispute_id)
        out = dispute_summary(s, d, _names())
        out["reporter_evidence"] = d.evidence.doc if d.evidence else None
        if d.evidence and d.evidence.original_file:
            out["original_url"] = f"/api/disputes/original/{d.evidence.id}"
        out["backend_evidence"] = d.backend_evidence
        out["backend_evidence_uri"] = d.backend_evidence_uri
        t = _token_of(s, d)
        rep = s.scalars(select(Report).where(Report.report_hash == t.report_hash)).first() if t else None
        if rep is not None:
            out["report"] = {
                "report_id": rep.id,
                "decision": rep.decision,
                "score": rep.score,
                "policy_version": rep.policy_version,
                "reasons": rep.body.get("reasons"),
                "image_url": f"/api/upload/{rep.id}.png",
                "created_at": int(rep.created_at.timestamp()),
                "artist": rep.submission.artist.wallet if rep.submission.artist else None,
            }
    out["resolver"] = net.resolver.address if net.resolver else None
    return jsonify({"success": True, **out})


@bp.get("/api/disputes/file/<name>")
def dispute_file(name):
    if not FILE_NAME.match(name):
        abort(404)
    return send_from_directory(config.STORAGE_DIR / "disputes", name)


@bp.get("/api/disputes/original/<int:evidence_id>")
def original_file(evidence_id):
    with db.session() as s:
        ev = s.get(DisputeEvidence, evidence_id)
        if ev is None or not ev.original_file:
            abort(404)
        rel = ev.original_file
    path = config.STORAGE_DIR / rel
    return send_from_directory(path.parent, path.name)


# ---- reporter --------------------------------------------------------------------------------


def _store_original(f) -> dict | None:
    """Keep the reporter's original (exact bytes) and pin it; returns its description."""
    if f is None or not f.filename:
        return None
    raw = f.read(config.MAX_EVIDENCE_BYTES + 1)
    if len(raw) > config.MAX_EVIDENCE_BYTES:
        raise EvidenceError("original file larger than 10 MB")
    from detection.preprocessing import InvalidImage, prepare

    try:
        img = prepare(raw)
    except InvalidImage as exc:
        raise EvidenceError(f"original is not a usable image: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    ext = IMAGE_EXT.get(img.source_format, "bin")
    name = f"mintgate-original-{digest[:32]}.{ext}"
    folder = config.STORAGE_DIR / ORIGINALS
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(raw)
    uri = (
        "ipfs://" + ipfs.pin_file(raw, name, f"image/{ext}")
        if ipfs.enabled()
        else f"{config.PUBLIC_BASE_URL}/api/disputes/file/{name}"
    )
    if not ipfs.enabled():  # served by dispute_file from storage/disputes
        (config.STORAGE_DIR / "disputes" / name).write_bytes(raw)
    return {"sha256": digest, "image_sha256": img.sha256, "bytes": len(raw), "uri": uri, "_path": f"{ORIGINALS}/{name}"}


@bp.post("/api/disputes/evidence")
@auth.require_session()
def submit_evidence():
    """Pin the reporter's evidence; the reporter's wallet then calls openDispute with the result."""
    form = request.form
    net = _net(form.get("network") or config.DEFAULT_NETWORK)
    contract, token_id = form.get("contract", ""), form.get("token_id", "")
    if not Web3.is_address(contract) or not token_id.isdigit():
        return _error("contract and token_id are required")
    with db.session() as s:
        t = s.get(Token, (net.chain_id, Web3.to_checksum_address(contract), Decimal(token_id)))
        if t is None:
            return _error("token not indexed by Mint-Gate", 404)
        if t.status == "revoked":
            return _error("this token is already revoked", 409)
        if t.status == "flagged":
            return _error("this token already has an open dispute", 409)
        s.expunge(t)
    try:
        original = _store_original(request.files.get("original"))
        doc = reporter_document(
            net=net,
            token=t,
            disputant=g.wallet,
            statement=form.get("statement", ""),
            urls=form.get("urls", ""),
            first_published=form.get("first_published", ""),
            prior_claims=form.get("prior_claims", ""),
            original={k: v for k, v in original.items() if k != "_path"} if original else None,
        )
    except EvidenceError as exc:
        return _error(str(exc))
    except ipfs.IPFSError as exc:
        log.error("ipfs: %s", exc)
        return _error("could not pin the evidence to IPFS - try again", 502)
    raw = doc_bytes(doc)
    digest = hashlib.sha256(raw).hexdigest()
    name = f"mintgate-dispute-evidence-{digest[:32]}.json"
    (config.STORAGE_DIR / "disputes").mkdir(exist_ok=True)
    (config.STORAGE_DIR / "disputes" / name).write_bytes(raw)
    try:
        uri = pin_document(raw, name, f"{config.PUBLIC_BASE_URL}/api/disputes/file/{name}")
    except ipfs.IPFSError as exc:
        log.error("ipfs: %s", exc)
        return _error("could not pin the evidence to IPFS - try again", 502)
    with db.session() as s:
        ev = DisputeEvidence(
            chain_id=net.chain_id,
            registry_address=Web3.to_checksum_address(net.registry.address),
            contract=t.contract,
            token_id=t.token_id,
            image_sha256=t.image_sha256,
            disputant=g.wallet,
            evidence_uri=uri,
            evidence_sha256=digest,
            doc=doc,
            original_file=original["_path"] if original else None,
        )
        existing = s.scalars(select(DisputeEvidence).where(DisputeEvidence.evidence_uri == uri)).first()
        if existing is None:  # identical document pinned twice -> same CID
            s.add(ev)
        repo.audit(s, g.wallet, "dispute.evidence_pinned", f"{net.chain_id}:{t.contract}:{t.token_id}", uri=uri)
    return jsonify(
        {
            "success": True,
            **net.public(),
            "call": "openDispute",
            "image_hash": "0x" + t.image_sha256,
            "evidence_uri": uri,
            "evidence_sha256": digest,
            "bond": str(net.raw.get("disputeBond", "0")),
        }
    )


# ---- reviewer ------------------------------------------------------------------------------------


@bp.get("/api/auth/roles")
@auth.require_session(csrf=False)
def roles():
    out = {}
    for name, net in networks.available().items():
        try:
            out[name] = {"reviewer": is_reviewer(net, g.wallet)}
        except Exception as exc:  # chain unreachable: unknown, not "no"
            log.warning("roles on %s: %s", name, exc)
            out[name] = {"reviewer": None}
    return jsonify({"success": True, "wallet": g.wallet, "roles": out})


def _require_reviewer(net):
    try:
        ok = is_reviewer(net, g.wallet)
    except Exception as exc:
        log.warning("role check on %s: %s", net.name, exc)
        return _error("blockchain temporarily unavailable", 503)
    if not ok:
        return _error(f"{g.wallet} does not hold REVIEWER_ROLE on {net.name}", 403)
    return None


@bp.post("/api/disputes/<network>/<int:dispute_id>/reason")
@auth.require_session()
def pin_reason(network, dispute_id):
    net = _net(network)
    if (denied := _require_reviewer(net)) is not None:
        return denied
    if net.resolver is None:
        return _error(f"no DisputeResolver deployed on {net.name}", 503)
    body = request.get_json(silent=True) or {}
    reason = str(body.get("reason", "")).strip()
    if len(reason) < config.MIN_REASON_CHARS:
        return _error(f"write a reason of at least {config.MIN_REASON_CHARS} characters")
    if not isinstance(body.get("upheld"), bool):
        return _error("upheld must be true or false")
    upheld = body["upheld"]
    with db.session() as s:
        d = _get_dispute(s, net, dispute_id)
        if d.status != "open":
            return _error(f"dispute is {d.status}", 409)
        doc = reason_document(net=net, dispute=d, reviewer=g.wallet, upheld=upheld, reason=reason)
    raw = doc_bytes(doc)
    digest = hashlib.sha256(raw).hexdigest()
    name = f"mintgate-dispute-{net.chain_id}-{dispute_id}-reason-{digest[:16]}.json"
    (config.STORAGE_DIR / "disputes").mkdir(exist_ok=True)
    (config.STORAGE_DIR / "disputes" / name).write_bytes(raw)
    try:
        uri = pin_document(raw, name, f"{config.PUBLIC_BASE_URL}/api/disputes/file/{name}")
    except ipfs.IPFSError as exc:
        log.error("ipfs: %s", exc)
        return _error("could not pin the reason to IPFS - try again", 502)
    with db.session() as s:
        repo.audit(s, g.wallet, "dispute.reason_pinned", f"{net.chain_id}:{dispute_id}", upheld=upheld, uri=uri)
    return jsonify(
        {
            "success": True,
            **net.public(),
            "call": "resolveDispute",
            "resolver": net.resolver.address,
            "args": [str(dispute_id), upheld, "0x" + digest, uri],
            "reason_uri": uri,
            "reason_hash": digest,
        }
    )


@bp.post("/api/disputes/<network>/<int:dispute_id>/backend-evidence")
@auth.require_session()
def requeue_backend_evidence(network, dispute_id):
    net = _net(network)
    if (denied := _require_reviewer(net)) is not None:
        return denied
    with db.session() as s:
        d = _get_dispute(s, net, dispute_id)
        if d.backend_evidence is not None:
            return jsonify({"success": True, "queued": False, "uri": d.backend_evidence_uri})
    try:
        job = jq.enqueue_analysis_task("dispute_evidence", net.name, dispute_id)
    except RedisError:
        return _error("job queue temporarily unavailable", 503)
    return jsonify({"success": True, "queued": True, "job_id": job.id})
