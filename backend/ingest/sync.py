"""Live sync: newly minted tokens of tracked collections become searchable within minutes.

Every `interval` seconds: scan `Transfer(from=0x0)` (ERC-721) and `TransferSingle(from=0x0)`
(ERC-1155) logs of all tracked collections, block window by block window (Alchemy free tier:
10 blocks per eth_getLogs), up to head - CONFIRMATIONS. Each new token -> getNFTMetadata ->
thumbnail into the spool -> `reference_items` row (pending); `python -m ingest embed --follow`
fingerprints it. A collection's `sync_block` advances in the same transaction as its new rows.
Mint-Gate's own mints need nothing here: the Phase 2 indexer already adds them to reference_items.
"""

import time
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3

import db
from db import repo
from ingest import fetch
from ingest.alchemy import Alchemy, AlchemyError
from ingest.collections import CHAINS

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TRANSFER_SINGLE = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
ZERO_TOPIC = "0x" + "0" * 64
WINDOW = 10  # blocks per eth_getLogs (free tier limit, inclusive range)
CONFIRMATIONS = 3


def log(*a):
    print(*a, flush=True)


def minted_tokens(logs) -> list[tuple[str, str]]:
    """(contract, decimal token id) for every mint log."""
    out = []
    for lg in logs:
        topics = lg["topics"]
        addr = Web3.to_checksum_address(lg["address"])
        if topics[0] == TRANSFER and len(topics) == 4 and topics[1] == ZERO_TOPIC:
            out.append((addr, str(int(topics[3], 16))))
        elif topics[0] == TRANSFER_SINGLE and len(topics) == 4 and topics[2] == ZERO_TOPIC:
            data = bytes.fromhex(lg["data"][2:])
            out.append((addr, str(int.from_bytes(data[:32], "big"))))
    return out


def get_logs(alchemy, addresses, frm, to):
    params = {"fromBlock": hex(frm), "toBlock": hex(to), "address": addresses}
    logs = alchemy.rpc("eth_getLogs", [{**params, "topics": [TRANSFER, ZERO_TOPIC]}])
    logs += alchemy.rpc("eth_getLogs", [{**params, "topics": [TRANSFER_SINGLE, None, ZERO_TOPIC]}])
    return logs


def ingest_tokens(alchemy, chain_id, tokens, pool) -> int:
    """Metadata + thumbnail for each (contract, token_id); returns rows added."""

    def one(ct):
        contract, tid = ct
        try:
            item = fetch.parse_nft(chain_id, contract, alchemy.nft_metadata(contract, tid))
        except AlchemyError as e:
            log(f"[sync] {contract} #{tid}: metadata failed ({e})")
            return contract, None
        if item is None:  # metadata not indexed by Alchemy yet: the token is picked up by a later backfill
            return contract, None
        ok = fetch.download(item["thumb_url"], fetch.spool_path(chain_id, contract, item["token_id"]))
        item["status"] = "pending" if ok else "failed"
        return contract, item

    added = 0
    by_contract: dict[str, list] = {}
    for contract, item in pool.map(one, tokens):
        if item:
            by_contract.setdefault(contract, []).append(item)
    with db.session() as s:
        for contract, items in by_contract.items():
            coll = repo.get_collection(s, chain_id, contract, lock=True)
            n = repo.add_nft_items(s, coll, items)
            coll.ingested += n
            added += n
    return added


def sync_once(alchemy, chain_id, pool) -> int:
    with db.session() as s:
        cols = {c.contract: c.sync_block for c in repo.tracked_collections(s, chain_id)}
    if not cols:
        return 0
    head = int(alchemy.rpc("eth_blockNumber", []), 16) - CONFIRMATIONS
    start = min((b for b in cols.values() if b is not None), default=None)
    if start is None or any(b is None for b in cols.values()):  # first run: follow from now on
        with db.session() as s:
            for c in repo.tracked_collections(s, chain_id):
                if c.sync_block is None:
                    c.sync_block = head
        start = min(b if b is not None else head for b in cols.values())
    added, frm = 0, start + 1
    while frm <= head:
        to = min(head, frm + WINDOW - 1)
        addrs = [a for a, b in cols.items() if b is None or b < to]
        tokens = minted_tokens(get_logs(alchemy, addrs, frm, to)) if addrs else []
        if tokens:
            n = ingest_tokens(alchemy, chain_id, tokens, pool)
            added += n
            log(f"[sync] blocks {frm}-{to}: {len(tokens)} mints, {n} new tokens queued for fingerprinting")
        with db.session() as s:
            for c in repo.tracked_collections(s, chain_id):
                if c.sync_block is None or c.sync_block < to:
                    c.sync_block = to
        frm = to + 1
    return added


def run(chain="eth", interval=60, once=False):
    spec = CHAINS[chain]
    alchemy = Alchemy(spec["alchemy"])
    with ThreadPoolExecutor(8) as pool:
        while True:
            t = time.time()
            try:
                n = sync_once(alchemy, spec["chain_id"], pool)
                log(f"[sync] {chain}: pass done, {n} new tokens ({time.time() - t:.1f}s)")
            except AlchemyError as e:
                log(f"[sync] {chain}: {e}; retrying next interval")
            if once:
                return
            time.sleep(max(1.0, interval - (time.time() - t)))
