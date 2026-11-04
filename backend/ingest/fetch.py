"""Backfill: tracked collections -> token rows + cached thumbnails (resumable per collection).

For each collection: Alchemy `getNFTsForContract` pages (100 tokens, metadata included) ->
Alchemy's CDN thumbnail (250 px, already cached by Alchemy: no IPFS round trips) is downloaded
into the spool (storage/nft/spool/), and a `reference_items` row (status "pending") stores the
token id, the original image URL, its attributes and trait hash. The Alchemy `pageKey` is saved
in `nft_collections` in the same transaction, so a restart continues with the next page.
`ingest.embed` later fingerprints the spool and keeps only a 128 px thumbnail.
"""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import requests
from web3 import Web3

import config
import db
from db import repo
from ingest.alchemy import Alchemy, AlchemyError
from ingest.collections import CAP, CHAINS

SPOOL_DIR = config.STORAGE_DIR / "nft" / "spool"
MAX_THUMB_BYTES = 4 * 1024 * 1024
_http = requests.Session()
_http.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=64))


def log(*a):
    print(*a, flush=True)


def image_uri(chain_id: int, contract: str, token_id: str) -> str:
    return f"nft/{chain_id}/{contract.lower()}/{token_id}.jpg"


def spool_path(chain_id: int, contract: str, token_id: str):
    return SPOOL_DIR / str(chain_id) / contract.lower() / f"{token_id}.img"


def canonical_traits(attributes) -> list | None:
    """Sorted [trait_type, value] pairs, lower-cased; empty / 'none' values dropped."""
    if not isinstance(attributes, list):
        return None
    out = set()
    for a in attributes:
        if not isinstance(a, dict):
            continue
        t, v = str(a.get("trait_type", "")).strip().lower(), str(a.get("value", "")).strip().lower()
        if v and v not in ("none", "null", "n/a"):
            out.add((t, v))
    return [list(x) for x in sorted(out)] or None


def trait_hash(attributes) -> str | None:
    traits = canonical_traits(attributes)
    return hashlib.sha256(json.dumps(traits, separators=(",", ":")).encode()).hexdigest() if traits else None


def parse_nft(chain_id: int, contract: str, nft: dict) -> dict | None:
    img = nft.get("image") or {}
    url = img.get("thumbnailUrl") or img.get("pngUrl") or img.get("cachedUrl")
    if not url:
        return None
    meta = (nft.get("raw") or {}).get("metadata") or {}
    attrs = meta.get("attributes") if isinstance(meta, dict) else None
    tid = str(nft["tokenId"])
    return {
        "token_id": tid,
        "thumb_url": url,
        "source_url": img.get("originalUrl") or (meta.get("image") if isinstance(meta, dict) else None),
        "attributes": attrs if isinstance(attrs, list) else None,
        "trait_hash": trait_hash(attrs),
        "image_uri": image_uri(chain_id, contract, tid),
    }


def download(url: str, dest, retries=4) -> bool:
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            r = _http.get(url, timeout=60)
            if r.status_code == 200 and 0 < len(r.content) <= MAX_THUMB_BYTES:
                tmp = dest.with_suffix(".part")
                tmp.write_bytes(r.content)
                tmp.replace(dest)
                return True
            if r.status_code in (400, 403, 404, 410, 415):
                return False
        except requests.RequestException:
            pass
        time.sleep(2**attempt)
    return False


def backfill_collection(alchemy: Alchemy, chain_id: int, contract: str, pool: ThreadPoolExecutor, cap=CAP):
    contract = Web3.to_checksum_address(contract)
    with db.session() as s:
        coll = repo.get_collection(s, chain_id, contract)
        if coll is None:
            m = alchemy.contract_metadata(contract)
            supply = m.get("totalSupply")
            coll = repo.upsert_collection(
                s,
                chain_id,
                contract,
                cap,
                name=m.get("name") or (m.get("openSeaMetadata") or {}).get("collectionName"),
                token_type=m.get("tokenType"),
                total_supply=int(supply) if supply and str(supply).isdigit() else None,
            )
        if coll.cap != cap:  # cap raised (or lowered) since the last run
            coll.cap = cap
            coll.backfill_done = coll.ingested >= cap or (coll.ingested > 0 and not coll.page_key)
        page_key, done, ingested = coll.page_key, coll.backfill_done, coll.ingested
        name = coll.name
    t = time.time()
    while not done and ingested < cap:
        try:
            page = alchemy.nfts_for_contract(contract, page_key)
        except AlchemyError as e:
            log(f"[fetch] {name}: {e}; skipping collection for now")
            return
        items = [x for x in (parse_nft(chain_id, contract, n) for n in page.get("nfts", [])) if x]
        ok = list(pool.map(lambda it: download(it["thumb_url"], spool_path(chain_id, contract, it["token_id"])), items))
        for it, good in zip(items, ok):
            it["status"] = "pending" if good else "failed"
        next_key = page.get("pageKey")
        with db.session() as s:
            coll = repo.get_collection(s, chain_id, contract, lock=True)
            added = repo.add_nft_items(s, coll, items)
            coll.ingested += added
            coll.page_key = next_key
            coll.backfill_done = not next_key or coll.ingested >= cap
            coll.updated_at = datetime.now(UTC)
            page_key, done, ingested = coll.page_key, coll.backfill_done, coll.ingested
        rate = ingested / max(1e-6, time.time() - t)
        log(f"[fetch] {name}: {ingested} tokens ({sum(ok)}/{len(items)} thumbs this page, {rate:.1f}/s)")
    log(f"[fetch] {name}: backfill complete ({ingested} tokens)")


def run(chain: str = "eth", only: list[str] | None = None, threads: int = 24, cap: int = CAP, parallel: int = 3):
    """`parallel` collections at a time share one pool of `threads` thumbnail downloads."""
    spec = CHAINS[chain]
    alchemy = Alchemy(spec["alchemy"])
    cols = [a for a, _ in spec["collections"] if not only or a.lower() in {o.lower() for o in only}]
    with ThreadPoolExecutor(threads) as pool, ThreadPoolExecutor(parallel) as outer:
        jobs = [outer.submit(backfill_collection, alchemy, spec["chain_id"], a, pool, cap) for a in cols]
        for j in jobs:
            j.result()
