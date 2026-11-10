"""Public explorer + governance API (Phase 6), all from the indexer's tables (no RPC calls).

GET  /api/explorer/stats                       per chain: verdicts by decision, reviews, mints, disputes,
                                               revocations, locked bonds, recent activity
GET  /api/explorer/policies                    on-chain policy history (PolicySet), pending timelocked
                                               changes, and whether this server's policy is announced
GET  /api/explorer/governance                  Safe + timelock addresses, role history, Safe proposals
POST /api/governance/<net>/<safeTxHash>/signatures   an owner's EIP-712 SafeTx signature (verified)
GET  /api/token/<net>/<contract>/<id>/timeline token provenance timeline
GET  /api/policy/<sha256>.json                 this server's policy files by hash (exact bytes)
"""

import hashlib
from decimal import Decimal

from flask import Blueprint, Response, abort, jsonify, request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from web3 import Web3

import config
import db
from chain import ipfs, networks
from db import repo
from db.models import ChainEvent, Dispute, IndexerCursor, SafeProposal, Token
from governance import safe as gsafe

bp = Blueprint("explorer", __name__)
DECISION = {1: "PASS", 2: "REVIEW", 3: "FAIL"}
ROLE_NAMES = {
    "0x" + "00" * 32: "DEFAULT_ADMIN_ROLE",
    **{
        "0x" + Web3.keccak(text=n).hex().removeprefix("0x"): n
        for n in ("VERIFIER_ROLE", "REVIEWER_ROLE", "PAUSER_ROLE", "COLLECTION_ROLE")
    },
}
ACTIVITY = (
    "VerdictSubmitted",
    "ReviewResolved",
    "ImageMinted",
    "DisputeOpened",
    "DisputeResolved",
    "DisputeReasoned",
    "ImageRevoked",
    "PolicySet",
    "CallScheduled",
    "ExecutionSuccess",
    "RoleGranted",
    "RoleRevoked",
    "Paused",
    "Unpaused",
)


def _error(msg, code=400):
    return jsonify({"success": False, "error": msg}), code


def _net(name):
    try:
        return networks.resolve(name)
    except networks.UnknownNetwork:
        abort(404)


def _ev(e: ChainEvent) -> dict:
    return {
        "event": e.event,
        "block": e.block,
        "time": e.block_time,
        "tx": e.tx,
        "contract": e.contract,
        "args": e.args,
    }


def _counts(s, chain_id: int, event: str, key: str | None = None) -> dict:
    q = select(func.count()).where(ChainEvent.chain_id == chain_id, ChainEvent.event == event)
    if key is None:
        return s.scalar(q)
    col = ChainEvent.args[key].astext
    rows = s.execute(
        select(col, func.count()).where(ChainEvent.chain_id == chain_id, ChainEvent.event == event).group_by(col)
    )
    return {k: n for k, n in rows}


@bp.get("/api/explorer/stats")
def stats():
    out = {}
    with db.session() as s:
        cursors = {c.chain_id: c for c in s.scalars(select(IndexerCursor))}
        for name, net in networks.available().items():
            cid = net.chain_id
            verdicts = {DECISION.get(int(k), k): v for k, v in _counts(s, cid, "VerdictSubmitted", "decision").items()}
            reviews = _counts(s, cid, "ReviewResolved", "approved")
            disputes = dict(
                s.execute(
                    select(Dispute.status, func.count()).where(Dispute.chain_id == cid).group_by(Dispute.status)
                ).all()
            )
            locked = s.scalar(
                select(func.coalesce(func.sum(Dispute.bond), 0)).where(
                    Dispute.chain_id == cid, Dispute.status == "open"
                )
            )
            tokens = dict(
                s.execute(select(Token.status, func.count()).where(Token.chain_id == cid).group_by(Token.status)).all()
            )
            recent = s.scalars(
                select(ChainEvent)
                .where(ChainEvent.chain_id == cid, ChainEvent.event.in_(ACTIVITY))
                .order_by(ChainEvent.block.desc(), ChainEvent.log_index.desc())
                .limit(15)
            )
            cur = cursors.get(cid)
            out[name] = {
                **net.public(),
                "label": config.CHAIN_LABELS.get(name, name),
                "indexed_block": cur.block if cur else None,
                "verdicts": {d: verdicts.get(d, 0) for d in ("PASS", "REVIEW", "FAIL")},
                "reviews": {"approved": reviews.get("true", 0), "rejected": reviews.get("false", 0)},
                "voided": _counts(s, cid, "VerdictVoided"),
                "mints": _counts(s, cid, "ImageMinted"),
                "tokens": {k: tokens.get(k, 0) for k in ("active", "flagged", "revoked")},
                "disputes": {k: disputes.get(k, 0) for k in ("open", "upheld", "rejected")},
                "revocations": _counts(s, cid, "ImageRevoked"),
                "bonds_locked_wei": str(locked),
                "recent": [_ev(e) for e in recent],
            }
    return jsonify({"success": True, "networks": out})


def _local_policies() -> list[dict]:
    out = []
    for name in ("policy.json", "policy.permissive.json"):
        p = config.BACKEND_DIR / name
        if p.exists():
            out.append({"file": name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    return out


@bp.get("/api/policy/<digest>.json")
def policy_file(digest):
    for name in ("policy.json", "policy.permissive.json"):
        raw = (config.BACKEND_DIR / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() == digest.lower():
            return Response(raw, mimetype="application/json")
    abort(404)


@bp.get("/api/explorer/policies")
def policies():
    from server import POLICY  # the policy this server's workers sign verdicts with

    out = {}
    with db.session() as s:
        for name, net in networks.available().items():
            cid = net.chain_id
            sets = s.scalars(
                select(ChainEvent)
                .where(ChainEvent.chain_id == cid, ChainEvent.event == "PolicySet")
                .order_by(ChainEvent.block, ChainEvent.log_index)
            )
            history = [
                {
                    "index": e.args["index"],
                    "sha256": e.args["policyHash"].removeprefix("0x"),
                    "version": e.args["version"],
                    "uri": e.args["uri"],
                    "uri_http": ipfs.gateway_url(e.args["uri"]),
                    "block": e.block,
                    "time": e.block_time,
                    "tx": e.tx,
                }
                for e in sets
            ]
            ops = {}
            for e in s.scalars(
                select(ChainEvent)
                .where(
                    ChainEvent.chain_id == cid,
                    ChainEvent.event.in_(("CallScheduled", "CallSalt", "CallExecuted", "Cancelled")),
                )
                .order_by(ChainEvent.block, ChainEvent.log_index)
            ):
                op = ops.setdefault(e.args["id"], {"id": e.args["id"], "status": "pending", "salt": "0x" + "00" * 32})
                if e.event == "CallSalt":
                    op["salt"] = e.args["salt"]
                elif e.event == "CallScheduled":
                    op |= {
                        "target": e.args["target"],
                        "value": str(e.args["value"]),
                        "predecessor": e.args["predecessor"],
                        "data": e.args["data"],
                        "ready_at": (e.block_time or 0) + int(e.args["delay"]),
                        "scheduled_tx": e.tx,
                        "scheduled_at": e.block_time,
                        "decoded": _decode_policy_call(net, e.args["target"], e.args["data"]),
                    }
                elif e.event == "CallExecuted":
                    op |= {"status": "executed", "executed_tx": e.tx}
                else:
                    op |= {"status": "cancelled", "cancelled_tx": e.tx}
            here = POLICY.sha256
            announced = {h["sha256"] for h in history}
            out[name] = {
                "policy_registry": net.policies.address if net.policies else None,
                "timelock": net.timelock.address if net.timelock else None,
                "history": history,
                "current": history[-1] if history else None,
                "operations": list(ops.values()),
                "server_policy": {"version": POLICY.version, "sha256": here, "announced": here in announced},
            }
    return jsonify({"success": True, "local": _local_policies(), "networks": out})


def _decode_policy_call(net, target: str, data: str):
    if net.policies is None or Web3.to_checksum_address(target) != Web3.to_checksum_address(net.policies.address):
        return None
    try:
        c = Web3().eth.contract(address=net.policies.address, abi=net.policies.abi)
        fn, args = c.decode_function_input(data)
    except ValueError:
        return None
    if fn.fn_name != "setPolicy":
        return {"function": fn.fn_name}
    return {
        "function": "setPolicy",
        "sha256": "0x" + bytes(args["policyHash"]).hex()
        if isinstance(args["policyHash"], bytes)
        else args["policyHash"],
        "version": args["version"],
        "uri": args["uri"],
    }


@bp.get("/api/explorer/governance")
def governance():
    out = {}
    with db.session() as s:
        for name, net in networks.available().items():
            cid = net.chain_id
            rows = s.scalars(
                select(ChainEvent)
                .where(
                    ChainEvent.chain_id == cid,
                    ChainEvent.event.in_(
                        (
                            "RoleGranted",
                            "RoleRevoked",
                            "DefaultAdminTransferScheduled",
                            "Paused",
                            "Unpaused",
                            "DisputeBondChanged",
                            "TreasuryChanged",
                            "AddedOwner",
                            "RemovedOwner",
                            "ChangedThreshold",
                        )
                    ),
                )
                .order_by(ChainEvent.block, ChainEvent.log_index)
            )
            history, holders = [], {}
            for e in rows:
                d = _ev(e)
                if "role" in e.args:
                    d["role_name"] = ROLE_NAMES.get(e.args["role"], e.args["role"])
                    who = holders.setdefault(d["role_name"], set())
                    (who.add if e.event == "RoleGranted" else who.discard)(e.args["account"])
                history.append(d)
            proposals = s.scalars(
                select(SafeProposal).where(SafeProposal.chain_id == cid).order_by(SafeProposal.nonce, SafeProposal.id)
            )
            out[name] = {
                **net.public(),
                "label": config.CHAIN_LABELS.get(name, name),
                "governance": net.governance(),
                "role_holders": {k: sorted(v) for k, v in holders.items()},
                "history": history[-100:],
                "proposals": [gsafe.proposal_dict(p, net) for p in proposals],
                "safe_abi": net.safe.abi if net.safe else None,
            }
    return jsonify({"success": True, "networks": out})


@bp.post("/api/governance/<network>/<safe_tx_hash>/signatures")
def add_signature(network, safe_tx_hash):
    """Anyone may relay an owner's signature: it is only stored if it recovers to an owner."""
    net = _net(network)
    sig = str((request.get_json(silent=True) or {}).get("signature", ""))
    with db.session() as s:
        p = s.scalars(
            select(SafeProposal)
            .where(SafeProposal.chain_id == net.chain_id, SafeProposal.safe_tx_hash == safe_tx_hash.lower())
            .with_for_update()
        ).first()
        if p is None:
            abort(404)
        try:
            owner = gsafe.add_signature(net, p, sig)
        except gsafe.GovernanceError as exc:
            return _error(str(exc))
        except ValueError:
            return _error("malformed signature")
        repo.audit(s, owner, "safe.signed", p.safe_tx_hash, chain_id=net.chain_id)
        out = gsafe.proposal_dict(p, net)
    return jsonify({"success": True, "owner": owner, "proposal": out})


# ---- token timeline ----------------------------------------------------------------------------


@bp.get("/api/token/<network>/<contract>/<int:token_id>/timeline")
def timeline(network, contract, token_id):
    net = _net(network)
    if not Web3.is_address(contract):
        abort(404)
    try:
        with db.session() as s:
            t = s.get(Token, (net.chain_id, Web3.to_checksum_address(contract), Decimal(token_id)))
            if t is None:
                return _error("token not found", 404)
            image, report = "0x" + t.image_sha256, "0x" + t.report_hash
            dispute_ids = [
                str(d)
                for (d,) in s.execute(
                    select(Dispute.dispute_id).where(
                        Dispute.chain_id == net.chain_id, Dispute.image_sha256 == t.image_sha256
                    )
                )
            ]
            a = ChainEvent.args
            conds = [
                a["reportHash"].astext == report,
                a["imageHash"].astext == image,
                and_(
                    ChainEvent.event == "Transfer",
                    ChainEvent.contract == t.contract,
                    a["tokenId"].astext == str(token_id),
                ),
            ]
            if dispute_ids:
                conds.append(
                    and_(
                        ChainEvent.event == "DisputeReasoned",
                        a["disputeId"].astext.in_(dispute_ids),
                    )
                )
            rows = s.scalars(
                select(ChainEvent)
                .where(ChainEvent.chain_id == net.chain_id, or_(*conds))
                .order_by(ChainEvent.block, ChainEvent.log_index)
            )
            events = [_ev(e) for e in rows]
            disputes = {
                str(d.dispute_id): {
                    "evidence_uri": d.evidence_uri,
                    "evidence_http": ipfs.gateway_url(d.evidence_uri),
                    "reason_uri": d.reason_uri,
                    "reason_http": ipfs.gateway_url(d.reason_uri) if d.reason_uri else None,
                    "backend_evidence_uri": d.backend_evidence_uri,
                    "status": d.status,
                }
                for d in s.scalars(
                    select(Dispute).where(Dispute.chain_id == net.chain_id, Dispute.image_sha256 == t.image_sha256)
                )
            }
            status = t.status
    except SQLAlchemyError:
        return _error("index unavailable", 503)
    return jsonify({"success": True, "status": status, "events": events, "disputes": disputes})
