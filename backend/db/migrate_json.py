"""One-off: import v1 JSON reports (storage/reports/*.json) into Postgres.

    cd backend; ..\\venv\\Scripts\\python.exe -m db.migrate_json [--dry-run]

Idempotent (existing report ids are skipped). The v1 chain data (anchoring tx, review tx,
MintGateNFT v1 mint) is kept under `legacy_v1` in the report body; the evidence images move to
storage/evidence/. Reports minted on v1 become `reference_items` (source "mintgate-v1"), so the
workers keep protecting them against re-uploads; their fingerprints are computed by the first
analysis worker that sees them.
"""

import argparse
import json

import config
import db
from db import repo

LEGACY_KEYS = ("chain", "review", "mint")


def migrate(reports_dir=None, dry_run=False) -> dict:
    reports_dir = reports_dir or config.REPORTS_DIR
    stats = {"imported": 0, "skipped": 0, "minted": 0}
    for path in sorted(reports_dir.glob("*.json")):
        r = json.loads(path.read_text())
        rid = r["report_id"]
        with db.session() as s:
            if repo.get_submission(s, rid) is not None:
                stats["skipped"] += 1
                continue
            mint = r.get("mint") or {}
            report = {k: v for k, v in r.items() if k not in LEGACY_KEYS + ("status",)}
            report["legacy_v1"] = {k: r.get(k) for k in LEGACY_KEYS}
            evidence = report.pop("evidence", None)
            evidence_uri = None
            if evidence and not dry_run:
                (config.EVIDENCE_DIR / f"{rid}.json").write_text(json.dumps(evidence))
                evidence_uri = f"evidence/{rid}.json"
            sub = repo.create_submission(s, rid, mint.get("recipient"), r.get("filename", ""), status=r["status"])
            rep = repo.save_report(s, sub, report | {"status": r["status"]}, evidence_uri)
            if r.get("review"):
                rep.review = {"approved": bool(r["review"].get("approved")), "note": r["review"].get("note", "")}
            if mint:
                repo.add_reference(s, "mintgate-v1", f"minted/{rid}.png")
                stats["minted"] += 1
            repo.audit(s, "migration", "legacy.imported", rid, source=path.name)
            stats["imported"] += 1
            if dry_run:
                s.rollback()
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="validate every file, write nothing")
    args = ap.parse_args(argv)
    print(migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
