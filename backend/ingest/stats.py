"""Per-collection index statistics: tokens by status + trait-duplicate groups."""

import json

from sqlalchemy import func, select

import config
import db
from db import repo
from db.models import NftCollection, ReferenceItem


def collect() -> dict:
    with db.session() as s:
        counts = dict(
            s.execute(
                select(ReferenceItem.status, func.count())
                .where(ReferenceItem.source == "alchemy")
                .group_by(ReferenceItem.status)
            ).all()
        )
        cols = []
        for c in s.scalars(select(NftCollection).order_by(NftCollection.id)):
            dups = repo.trait_duplicates(s, c.id)
            cols.append(
                {
                    "chain_id": c.chain_id,
                    "contract": c.contract,
                    "name": c.name,
                    "ingested": c.ingested,
                    "backfill_done": c.backfill_done,
                    "sync_block": c.sync_block,
                    "trait_duplicate_groups": len(dups),
                    "tokens_in_trait_duplicates": sum(len(t) for _, t in dups),
                    "example_duplicates": [[str(t) for t in toks[:5]] for _, toks in dups[:3]],
                }
            )
    return {"items_by_status": counts, "collections": cols}


def run():
    out = collect()
    for c in out["collections"]:
        print(
            f"{c['name'] or c['contract']:40s} {c['ingested']:6d} tokens  done={c['backfill_done']!s:5s} "
            f"trait-dup groups={c['trait_duplicate_groups']}"
        )
    print("items by status:", out["items_by_status"])
    (config.MODEL_DIR / "benchmarks").mkdir(exist_ok=True)
    (config.MODEL_DIR / "benchmarks" / "onchain_index_stats.json").write_text(json.dumps(out, indent=2))
