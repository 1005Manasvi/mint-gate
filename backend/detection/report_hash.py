"""Canonical report hash (goes on-chain as `reportHash`).

Kept free of model imports so the API process can verify reports without loading torch.
"""

import hashlib
import json

ATTESTED_FIELDS = (
    "report_id",
    "created_at",
    "image_sha256",
    "decision",
    "score",
    "policy_version",
    "policy_sha256",
    "models",
    "checks",
)


def canonical_hash(report: dict) -> str:
    """SHA-256 over the attested subset of the report (this goes on-chain)."""
    body = {k: report[k] for k in ATTESTED_FIELDS}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()
