"""The browser recomputes report hashes (frontend/js/canonical.js). This shared vector pins the
Python side; frontend/tests/canonical.test.mjs checks the JavaScript port against the same file.

Regenerate after changing report_hash.py:  python -m tests.test_canonical_vector   (from backend/)
"""

import json
from pathlib import Path

from detection.report_hash import canonical_hash

VECTOR = Path(__file__).resolve().parent / "fixtures" / "canonical_vector.json"

# Everything JSON.parse + JSON.stringify would get wrong: float spellings, big ints, escapes, key order.
REPORT = {
    "report_id": "f" * 32,
    "created_at": 1762128000,
    "image_sha256": "ab" * 32,
    "decision": "PASS",
    "score": 93,
    "policy_version": "3.1.0",
    "policy_sha256": "cd" * 32,
    "models": {"resnet18": "v1 – ü", "ai_detector": "probe 😀", "z": None, "a": [True, False]},
    "checks": {
        "clip": {"value": 1.0, "risk": 0.0, "nearest": 'art/"quoted"\\path\n\t\u0001\u007f'},
        "phash": {"value": 12, "risk": 1e-05, "big": 2**70, "neg": -0.5, "exp": 1.5e300},
        "orb": {"value": 0.30000000000000004, "list": [1, 2.5, -3, 1e16]},
    },
    "reasons": ["not attested: ignored by the hash"],
    "artist": "0x00000000000000000000000000000000000000A1",
}


def published_text(report: dict) -> str:
    """How chain/metadata.py pins the public report (pin_file of these bytes)."""
    return json.dumps(report, sort_keys=True, indent=1)


def test_vector_matches_python():
    v = json.loads(VECTOR.read_text(encoding="utf-8"))
    assert v["report_text"] == published_text(REPORT)
    assert v["hash"] == canonical_hash(REPORT) == canonical_hash(json.loads(v["report_text"]))


if __name__ == "__main__":
    VECTOR.write_text(
        json.dumps({"report_text": published_text(REPORT), "hash": canonical_hash(REPORT)}, indent=2) + "\n",
        encoding="utf-8",
    )
    print("wrote", VECTOR)
