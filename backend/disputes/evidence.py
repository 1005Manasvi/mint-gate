"""Evidence documents of a dispute.

1. Reporter evidence (`reporter_document`): statement, URLs, timestamps, prior on-chain claims and an
   optional original file, pinned to IPFS as exact bytes *before* the reporter's wallet sends
   `openDispute(imageHash, evidenceURI)`. Its SHA-256 is stored, so the pinned bytes can be checked.
2. Backend evidence (`backend_evidence`, analysis worker, after `DisputeOpened` is indexed):
   * copy search of the disputed image against the reference index (SSCD / DINOv2 / PDQ, the same
     engine as uploads), the disputed token itself excluded; whether each match was indexed before
     the disputed mint;
   * the reporter's original (if any) compared with the disputed image (SSCD cosine, PDQ bits);
   * the earliest signed submissions (Phase 3 claims) of the disputed pixels and of the original;
   * the reverse image search result (Phase 5 provenance, if configured).
3. Reviewer reason (`reason_document`): decision + written reason, pinned; its SHA-256 goes on chain
   with `DisputeResolver.resolveDispute`.
"""

import hashlib
import json
import time
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select

import config
from chain import ipfs
from db.models import Dispute, ReferenceItem, Report, Submission, Token

DOC_VERSION = 1
MAX_URLS = 10
MAX_CLAIMS = 10


class EvidenceError(ValueError):
    """Invalid reporter input; the message is safe to show."""


def _clean_lines(value: str, limit: int, max_len: int = 500) -> list[str]:
    out = [ln.strip()[:max_len] for ln in (value or "").splitlines() if ln.strip()]
    if len(out) > limit:
        raise EvidenceError(f"at most {limit} entries")
    return out


def reporter_document(
    *,
    net,
    token: Token,
    disputant: str,
    statement: str,
    urls: str,
    first_published: str,
    prior_claims: str,
    original: dict | None,
) -> dict:
    statement = (statement or "").strip()
    if len(statement) < 20:
        raise EvidenceError("describe the problem in at least 20 characters")
    links = _clean_lines(urls, MAX_URLS)
    for u in links:
        if not u.startswith(("https://", "http://", "ipfs://")):
            raise EvidenceError(f"not a URL: {u[:60]}")
    return {
        "type": "mint-gate/dispute-evidence",
        "version": DOC_VERSION,
        "chain_id": net.chain_id,
        "network": net.name,
        "registry": net.registry.address,
        "token": {"contract": token.contract, "token_id": str(token.token_id)},
        "image_sha256": token.image_sha256,
        "disputant": disputant,
        "statement": statement[:5000],
        "urls": links,
        "first_published": (first_published or "").strip()[:100] or None,
        "prior_claims": _clean_lines(prior_claims, MAX_CLAIMS),
        "original": original,  # {"sha256", "image_sha256", "uri", "bytes"} or None
        "created_at": int(time.time()),
    }


def doc_bytes(doc: dict) -> bytes:
    """The exact bytes that are pinned and hashed (sorted keys: reproducible)."""
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False).encode()


def pin_document(raw: bytes, name: str, local_url: str) -> str:
    """ipfs://CID with Pinata, else a URL served by this API (local development)."""
    if ipfs.enabled():
        return "ipfs://" + ipfs.pin_file(raw, name, "application/json")
    return local_url


def reason_document(*, net, dispute: Dispute, reviewer: str, upheld: bool, reason: str) -> dict:
    return {
        "type": "mint-gate/dispute-resolution",
        "version": DOC_VERSION,
        "chain_id": net.chain_id,
        "network": net.name,
        "registry": net.registry.address,
        "dispute_id": str(dispute.dispute_id),
        "image_sha256": dispute.image_sha256,
        "decision": "upheld" if upheld else "rejected",
        "reviewer": reviewer,
        "reason": reason.strip()[:10000],
        "evidence": {"reporter": dispute.evidence_uri, "backend": dispute.backend_evidence_uri},
        "decided_at": int(time.time()),
    }


# ---- the backend's own evidence (analysis worker) --------------------------------------------


def _pdq_bits(a: bytes, b: bytes) -> int:
    return int(np.unpackbits(np.frombuffer(a, np.uint8) ^ np.frombuffer(b, np.uint8)).sum())


def _submissions(s, image_sha256: str, limit: int = 5) -> list[dict]:
    rows = s.scalars(
        select(Submission).where(Submission.image_sha256 == image_sha256).order_by(Submission.created_at).limit(limit)
    )
    return [
        {
            "submission_id": sub.id,
            "wallet": sub.artist.wallet if sub.artist else None,
            "created_at": int(sub.created_at.timestamp()),
            "signed": sub.claim_signature is not None,
            "claim": {"message": sub.claim_message, "signature": sub.claim_signature} if sub.claim_signature else None,
            "status": sub.status,
        }
        for sub in rows
    ]


def backend_evidence(s, pipeline, d: Dispute) -> dict:
    """Everything the backend can say about a dispute. `pipeline` = the worker's MintGatePipeline."""
    from detection.preprocessing import load_path, prepare
    from detection.provenance import reverse_search

    out = {
        "type": "mint-gate/dispute-backend-evidence",
        "version": DOC_VERSION,
        "chain_id": d.chain_id,
        "dispute_id": str(d.dispute_id),
        "image_sha256": d.image_sha256,
        "generated_at": int(time.time()),
        "copy_engine": getattr(pipeline.copy, "version", None),
    }
    token = s.scalars(select(Token).where(Token.chain_id == d.chain_id, Token.image_sha256 == d.image_sha256)).first()
    rep = s.scalars(select(Report).where(Report.report_hash == token.report_hash)).first() if token else None
    minted_at = None
    if token is not None:
        out["token"] = {"contract": token.contract, "token_id": str(token.token_id), "artist": token.artist}
        mint_ev = token.minted_block
        out["token"]["minted_block"] = mint_ev
    path = config.UPLOADS_DIR / f"{rep.id}.png" if rep else None
    if path is None or not path.exists():
        out["copy_search"] = {"available": False, "why": "the disputed image is not stored by this Mint-Gate instance"}
        img, raw = None, None
    else:
        img = load_path(path)
        raw = path.read_bytes()
        minted_at = rep.created_at
        with pipeline._lock:
            cp = pipeline.copy.analyse(img, pipeline.index)
        own = f"minted/{rep.id}.png"
        matches = []
        for m in cp["matches"]:
            # the pgvector index names on-chain items "chain:contract:token"; `image` is the stored file
            image = m.get("image", m["reference"])
            if image == own or m["reference"] == own:
                continue
            ref = s.scalars(select(ReferenceItem).where(ReferenceItem.image_uri == image)).first()
            entry = {k: m[k] for k in ("reference", "copy_score", "collection_member") if k in m}
            entry["image"] = image
            if ref is not None:
                entry |= {
                    "source": ref.source,
                    "chain_id": ref.chain_id,
                    "contract": ref.contract,
                    "token_id": str(ref.token_id) if ref.token_id is not None else None,
                    "source_url": ref.source_url,
                    "indexed_at": int(ref.created_at.timestamp()),
                    # indexed before the disputed artwork was even uploaded: it existed first
                    "indexed_before_upload": bool(minted_at and ref.created_at < minted_at),
                }
            matches.append(entry)
        out["copy_search"] = {
            "available": True,
            "values": {k: v for k, v in cp["values"].items()},
            "nearest": {k: v.get("nearest") for k, v in cp["details"].items() if isinstance(v, dict)},
            "matches": matches[:5],
        }
    out["earliest_submissions"] = _submissions(s, d.image_sha256)
    if token is not None and out["earliest_submissions"]:
        out["earliest_is_token_artist"] = out["earliest_submissions"][0]["wallet"] == token.artist

    ev = d.evidence
    if ev is not None and ev.original_file:
        opath = config.STORAGE_DIR / ev.original_file
        if opath.exists():
            orig_raw = opath.read_bytes()
            try:
                orig = prepare(orig_raw)
            except Exception as exc:  # unreadable upload: say so, keep the rest
                out["original"] = {"error": f"unreadable original: {type(exc).__name__}"}
            else:
                o = {"image_sha256": orig.sha256, "file_sha256": hashlib.sha256(orig_raw).hexdigest()}
                o["identical_pixels"] = orig.sha256 == d.image_sha256
                if img is not None:
                    with pipeline._lock:
                        fa, fb = pipeline.copy.fingerprint(img), pipeline.copy.fingerprint(orig.rgb)
                    o["sscd_cosine"] = round(float(np.dot(fa.sscd, fb.sscd)), 4)
                    o["pdq_bits"] = _pdq_bits(fa.pdq, fb.pdq)
                o["earliest_submissions"] = _submissions(s, orig.sha256)
                out["original"] = o
    rs = reverse_search(raw) if raw else None
    out["reverse_search"] = rs if rs is not None else {"configured": False}
    out["generated_at_iso"] = datetime.now(UTC).isoformat(timespec="seconds")
    return out
