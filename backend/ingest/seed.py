"""Import the local protected collection (index/files.json: CryptoPunks seed + WikiArt / museum
artworks + real NFT collections) into reference_items as source "seed", status pending.

After this, every reference (seed, Mint-Gate mints, on-chain NFTs) lives in one pgvector index and
`python -m ingest embed` fingerprints them all the same way. Idempotent (image_uri is unique).
"""

import json

from sqlalchemy.dialects.postgresql import insert

import config
import db
from db.models import ReferenceItem

SEED_PREFIX = "seed/"


def is_collection_member(name: str) -> bool:
    """v1 rule (detection.reference_index): seed CryptoPunks and art/nft_* are generative-collection members."""
    return "/" not in name or name.startswith("art/nft_")


def seed_path(image_uri: str):
    name = image_uri[len(SEED_PREFIX) :]
    if name.startswith("art/"):
        return config.REFERENCE_ART_DIR / name[4:]
    return config.ORIGINALS_DIR / name


def run() -> int:
    names = json.loads(config.INDEX_FILES.read_text())
    names = [n for n in names if not n.startswith("minted/")]
    rows = [
        {
            "source": "seed",
            "image_uri": SEED_PREFIX + n,
            "status": "pending",
            "collection_member": is_collection_member(n),
        }
        for n in names
    ]
    added = 0
    with db.session() as s:
        for i in range(0, len(rows), 2000):
            res = s.execute(
                insert(ReferenceItem).values(rows[i : i + 2000]).on_conflict_do_nothing().returning(ReferenceItem.id)
            )
            added += len(res.all())
    print(f"[seed] {added} new seed references ({len(rows)} in index/files.json)")
    return added
