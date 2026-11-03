"""Repository functions: every SQL statement the API, workers and indexer run lives here.

All functions take an open `Session` (see `db.session()`); the caller owns the transaction.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from web3 import Web3

from chain.eip712 import Verdict
from db.models import Artist, Attestation, AuditLog, Dispute, ReferenceItem, Report, Submission, Token

PENDING = {"queued", "processing"}
FAILED = {"invalid", "error"}
MINTABLE = {"PASS", "REVIEW_APPROVED"}


def to_signed64(u: int) -> int:
    """uint64 pHash -> BIGINT."""
    return u - (1 << 64) if u >= 1 << 63 else u


def to_unsigned64(i: int) -> int:
    return i + (1 << 64) if i < 0 else i


# ---- artists / submissions ------------------------------------------------------------------


def get_or_create_artist(s, wallet: str) -> Artist:
    wallet = Web3.to_checksum_address(wallet)
    s.execute(insert(Artist).values(wallet=wallet).on_conflict_do_nothing(index_elements=["wallet"]))
    return s.scalars(select(Artist).where(Artist.wallet == wallet)).one()


def create_submission(
    s, sid: str, wallet: str | None, filename: str, status: str = "queued", claim: dict | None = None
) -> Submission:
    """`claim` = auth.verify_claim() output: the wallet's signature over the uploaded file's SHA-256."""
    artist = get_or_create_artist(s, wallet) if wallet else None
    claim = claim or {}
    sub = Submission(
        id=sid,
        artist_id=artist.id if artist else None,
        filename=filename[:200],
        status=status,
        file_sha256=claim.get("file_sha256"),
        claim_message=claim.get("message"),
        claim_signature=claim.get("signature"),
    )
    s.add(sub)
    s.flush()
    return sub


def get_submission(s, sid: str, lock: bool = False) -> Submission | None:
    q = select(Submission).where(Submission.id == sid)
    if lock:
        q = q.with_for_update(of=Submission)
    return s.scalars(q).first()


def set_status(s, sid: str, status: str, error: str | None = None):
    s.execute(update(Submission).where(Submission.id == sid).values(status=status, error=error))


# ---- reports ----------------------------------------------------------------------------------


def save_report(s, sub: Submission, report: dict, evidence_uri: str | None) -> Report:
    body = {k: v for k, v in report.items() if k not in ("checks", "evidence")}
    rep = Report(
        id=report["report_id"],
        submission_id=sub.id,
        report_hash=report["report_hash"],
        decision=report["decision"],
        score=int(report["score"]),
        policy_version=report["policy_version"],
        checks=report["checks"],
        body=body,
        evidence_uri=evidence_uri,
    )
    s.add(rep)
    sub.image_sha256 = report["image_sha256"]
    sub.status = report.get("status", report["decision"])
    s.flush()
    return rep


def get_report(s, rid: str, lock: bool = False) -> Report | None:
    """Report ids equal submission ids; `lock` takes the submission row (it holds the mutable status)."""
    if lock and get_submission(s, rid, lock=True) is None:
        return None
    return s.scalars(select(Report).where(Report.id == rid)).first()


def get_report_by_hash(s, report_hash: str) -> Report | None:
    return s.scalars(select(Report).where(Report.report_hash == report_hash.removeprefix("0x"))).first()


def report_dict(rep: Report) -> dict:
    """The report as the API returns it (evidence is loaded separately from its file)."""
    sub = rep.submission
    out = dict(rep.body)
    out["checks"] = rep.checks
    out["status"] = sub.status
    out["artist"] = sub.artist.wallet if sub.artist else None
    out["review"] = rep.review
    out["publication"] = rep.publication
    out["attestations"] = [attestation_summary(a) for a in rep.attestations]
    out["claim"] = claim_of(sub)
    return out


def claim_of(sub: Submission) -> dict | None:
    if not sub.claim_signature:
        return None
    return {"file_sha256": sub.file_sha256, "message": sub.claim_message, "signature": sub.claim_signature}


def list_reports(s, status: str | None = None, limit: int = 200) -> list[Report]:
    q = select(Report).join(Submission).order_by(Report.created_at.desc()).limit(limit)
    if status:
        q = q.where(Submission.status == status)
    return list(s.scalars(q))


def artworks_of(s, wallet: str, limit: int = 100) -> list[dict]:
    """A wallet's submissions (newest first) with their verdict, minted tokens and disputes."""
    wallet = Web3.to_checksum_address(wallet)
    subs = list(
        s.scalars(
            select(Submission)
            .join(Artist)
            .where(Artist.wallet == wallet)
            .order_by(Submission.created_at.desc())
            .limit(limit)
        )
    )
    images = {sub.image_sha256 for sub in subs if sub.image_sha256}
    tokens, disputes = {}, {}
    if images:
        for t in s.scalars(select(Token).where(Token.image_sha256.in_(images)).order_by(Token.minted_block)):
            tokens.setdefault(t.image_sha256, []).append(t)
        for d in s.scalars(select(Dispute).where(Dispute.image_sha256.in_(images)).order_by(Dispute.opened_block)):
            disputes.setdefault(d.image_sha256, []).append(d)
    out = []
    for sub in subs:
        rep = sub.report
        out.append(
            {
                "submission_id": sub.id,
                "filename": sub.filename,
                "created_at": int(sub.created_at.timestamp()),
                "status": sub.status,
                "error": sub.error,
                "signed": sub.claim_signature is not None,
                "image_sha256": sub.image_sha256,
                "decision": rep.decision if rep else None,
                "score": rep.score if rep else None,
                "report_hash": rep.report_hash if rep else None,
                "tokens": [
                    {
                        "chain_id": t.chain_id,
                        "contract": t.contract,
                        "token_id": str(t.token_id),
                        "owner": t.owner,
                        "status": t.status,
                        "minted_tx": t.minted_tx,
                    }
                    for t in tokens.get(sub.image_sha256, [])
                ],
                "disputes": [
                    {
                        "chain_id": d.chain_id,
                        "dispute_id": str(d.dispute_id),
                        "status": d.status,
                        "opened_tx": d.opened_tx,
                        "resolved_tx": d.resolved_tx,
                    }
                    for d in disputes.get(sub.image_sha256, [])
                ],
            }
        )
    return out


# ---- attestations -------------------------------------------------------------------------------


def attestation_summary(a: Attestation) -> dict:
    return {
        "chain_id": a.chain_id,
        "registry": a.registry_address,
        "decision": a.verdict["decision"],
        "expires_at": int(a.expires_at.timestamp()),
        "submitted_tx": a.submitted_tx,
    }


def usable_attestation(s, report_id: str, chain_id: int, registry: str, min_remaining_s: int = 600):
    """Latest attestation for this report + chain that is on chain or still has time to be submitted."""
    q = (
        select(Attestation)
        .where(Attestation.report_id == report_id, Attestation.chain_id == chain_id)
        .where(Attestation.registry_address == Web3.to_checksum_address(registry))
        .order_by(Attestation.id.desc())
    )
    soon = datetime.now(UTC) + timedelta(seconds=min_remaining_s)
    for a in s.scalars(q):
        if a.submitted_tx or a.expires_at > soon:
            return a
    return None


def add_attestation(s, report_id: str, chain_id: int, registry: str, verdict: Verdict, signature: str) -> Attestation:
    a = Attestation(
        report_id=report_id,
        chain_id=chain_id,
        registry_address=Web3.to_checksum_address(registry),
        artist=verdict.artist,
        verdict=verdict.to_json(),
        signature=signature,
        nonce=Decimal(verdict.nonce),
        expires_at=datetime.fromtimestamp(verdict.expires_at, UTC),
    )
    s.add(a)
    s.flush()
    return a


def mark_verdict_submitted(s, chain_id: int, registry: str, report_hash: str, tx: str) -> int:
    """VerdictSubmitted seen on chain: every unsent attestation of that report on that registry is now
    spent (the registry accepts one verdict per report)."""
    res = s.execute(
        update(Attestation)
        .where(Attestation.chain_id == chain_id, Attestation.registry_address == Web3.to_checksum_address(registry))
        .where(Attestation.verdict["reportHash"].astext == "0x" + report_hash.removeprefix("0x"))
        .where(Attestation.submitted_tx.is_(None))
        .values(submitted_tx=tx)
    )
    return res.rowcount


# ---- audit log ---------------------------------------------------------------------------------------


def audit(s, actor: str, action: str, target: str | None = None, **meta):
    s.add(AuditLog(actor=actor[:64], action=action, target=target, meta=meta))


# ---- reference items ------------------------------------------------------------------------------------


def add_reference(
    s, source: str, image_uri: str, chain_id=None, contract=None, token_id=None, phash=None, embedding=None
):
    s.execute(
        insert(ReferenceItem)
        .values(
            source=source,
            image_uri=image_uri,
            chain_id=chain_id,
            contract=contract,
            token_id=None if token_id is None else Decimal(token_id),
            phash=None if phash is None else to_signed64(phash),
            embedding=embedding,
        )
        .on_conflict_do_nothing(index_elements=["image_uri"])
    )


def references_after(s, after_id: int, limit: int = 1000) -> list[ReferenceItem]:
    q = select(ReferenceItem).where(ReferenceItem.id > after_id).order_by(ReferenceItem.id).limit(limit)
    return list(s.scalars(q))


def set_fingerprint(s, item_id: int, phash: int, embedding):
    s.execute(
        update(ReferenceItem)
        .where(ReferenceItem.id == item_id, ReferenceItem.embedding.is_(None))
        .values(phash=to_signed64(phash), embedding=embedding)
    )


def count_references(s) -> int:
    return s.scalar(select(func.count()).select_from(ReferenceItem))
