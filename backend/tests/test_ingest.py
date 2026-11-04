"""On-chain NFT database: trait hashing, Alchemy parsing, resumable backfill, live sync (fake Alchemy)."""

import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from ingest import fetch, sync

BAYC = "0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D"


def nft(tid, traits=(("Fur", "Robot"), ("Hat", "None"))):
    return {
        "tokenId": str(tid),
        "image": {"thumbnailUrl": f"https://cdn.example/{tid}", "originalUrl": f"ipfs://Qm{tid}"},
        "raw": {"metadata": {"attributes": [{"trait_type": t, "value": v} for t, v in traits]}},
    }


class FakeAlchemy:
    """Two pages of 3 tokens; tokens 1 and 4 share a trait set; logs for the live sync."""

    def __init__(self):
        self.pages = {
            None: {"nfts": [nft(1), nft(2, (("Fur", "Gold"),)), nft(3, ())], "pageKey": "p2"},
            "p2": {"nfts": [nft(4, (("hat", "none"), ("fur", "robot "))), nft(5, (("Fur", "Blue"),))], "pageKey": None},
        }
        self.calls = []
        self.head = 1000
        self.logs = []

    def contract_metadata(self, address):
        return {"name": "BoredApeYachtClub", "tokenType": "ERC721", "totalSupply": "10000"}

    def nfts_for_contract(self, address, page_key=None):
        self.calls.append(page_key)
        return self.pages[page_key]

    def nft_metadata(self, address, token_id):
        return nft(token_id, (("Fur", "New"),))

    def rpc(self, method, params):
        if method == "eth_blockNumber":
            return hex(self.head)
        q = params[0]
        frm, to = int(q["fromBlock"], 16), int(q["toBlock"], 16)
        assert to - frm + 1 <= sync.WINDOW
        return [lg for lg in self.logs if frm <= lg["block"] <= to and lg["topics"][0] == q["topics"][0]]


@pytest.fixture
def fake_net(monkeypatch, tmp_path):
    png = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(png, format="PNG")
    monkeypatch.setattr(fetch, "SPOOL_DIR", tmp_path / "spool")

    def download(url, dest, retries=4):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(png.getvalue())
        return "/3" not in url  # token 3's thumbnail is broken

    monkeypatch.setattr(fetch, "download", download)
    return FakeAlchemy()


def test_trait_hash_is_canonical():
    a = [{"trait_type": "Fur", "value": "Robot"}, {"trait_type": "Hat", "value": "None"}]
    b = [{"trait_type": "fur", "value": "robot "}, {"trait_type": "Eyes", "value": ""}]
    assert fetch.trait_hash(a) == fetch.trait_hash(b)  # case, whitespace, empty / "none" ignored
    assert fetch.trait_hash([{"trait_type": "Fur", "value": "Gold"}]) != fetch.trait_hash(a)
    assert fetch.trait_hash(None) is None and fetch.trait_hash([]) is None and fetch.trait_hash("x") is None


def test_parse_nft_skips_tokens_without_image():
    assert fetch.parse_nft(1, BAYC, {"tokenId": "7", "image": {}}) is None
    item = fetch.parse_nft(1, BAYC, nft(7))
    assert item["image_uri"] == f"nft/1/{BAYC.lower()}/7.jpg" and item["source_url"] == "ipfs://Qm7"


def test_minted_tokens_parses_erc721_and_erc1155():
    to = "0x" + "0" * 24 + "ab" * 20
    logs = [
        {"address": BAYC.lower(), "topics": [sync.TRANSFER, sync.ZERO_TOPIC, to, hex(42)], "data": "0x"},
        {"address": BAYC.lower(), "topics": [sync.TRANSFER, to, to, hex(43)], "data": "0x"},  # not a mint
        {
            "address": BAYC.lower(),
            "topics": [sync.TRANSFER_SINGLE, to, sync.ZERO_TOPIC, to],
            "data": "0x" + (7).to_bytes(32, "big").hex() + (1).to_bytes(32, "big").hex(),
        },
    ]
    assert sync.minted_tokens(logs) == [(BAYC, "42"), (BAYC, "7")]


def test_backfill_is_resumable_and_finds_trait_duplicates(clean_db, fake_net):
    from db import repo

    with ThreadPoolExecutor(2) as pool:
        fetch.backfill_collection(fake_net, 1, BAYC, pool, cap=100)
        fetch.backfill_collection(fake_net, 1, BAYC, pool, cap=100)  # already done: no API call
    assert fake_net.calls == [None, "p2"]
    with clean_db.session() as s:
        coll = repo.get_collection(s, 1, BAYC)
        assert coll.ingested == 5 and coll.backfill_done and coll.name == "BoredApeYachtClub"
        rows = repo.nft_items_by_status(s, "pending")
        assert [int(r.token_id) for r in rows] == [1, 2, 4, 5]
        assert [int(r.token_id) for r in repo.nft_items_by_status(s, "failed")] == [3]
        assert [[int(t) for t in toks] for _, toks in repo.trait_duplicates(s, coll.id)] == [[1, 4]]


def test_backfill_stops_at_the_cap_and_resumes_when_raised(clean_db, fake_net):
    from db import repo

    with ThreadPoolExecutor(2) as pool:
        fetch.backfill_collection(fake_net, 1, BAYC, pool, cap=2)
        with clean_db.session() as s:
            coll = repo.get_collection(s, 1, BAYC)
            assert coll.ingested == 3 and coll.backfill_done and coll.page_key == "p2"  # one page past the cap
        fetch.backfill_collection(fake_net, 1, BAYC, pool, cap=100)
    with clean_db.session() as s:
        assert repo.get_collection(s, 1, BAYC).ingested == 5
    assert fake_net.calls == [None, "p2"]


def test_live_sync_queues_new_mints(clean_db, fake_net):
    from db import repo

    with ThreadPoolExecutor(2) as pool:
        fetch.backfill_collection(fake_net, 1, BAYC, pool, cap=100)
        assert sync.sync_once(fake_net, 1, pool) == 0  # first pass: start following at head - confirmations
        with clean_db.session() as s:
            assert repo.get_collection(s, 1, BAYC).sync_block == 1000 - sync.CONFIRMATIONS
        fake_net.logs = [
            {
                "block": 1001,
                "address": BAYC.lower(),
                "topics": [sync.TRANSFER, sync.ZERO_TOPIC, "0x0", hex(9)],
                "data": "0x",
            },
            {
                "block": 1015,
                "address": BAYC.lower(),
                "topics": [sync.TRANSFER, sync.ZERO_TOPIC, "0x0", hex(10)],
                "data": "0x",
            },
            {
                "block": 1030,
                "address": BAYC.lower(),
                "topics": [sync.TRANSFER, sync.ZERO_TOPIC, "0x0", hex(11)],
                "data": "0x",
            },
        ]
        fake_net.head = 1025  # 1030 is not confirmed yet
        assert sync.sync_once(fake_net, 1, pool) == 2
        fake_net.head = 1040
        assert sync.sync_once(fake_net, 1, pool) == 1
    with clean_db.session() as s:
        coll = repo.get_collection(s, 1, BAYC)
        assert coll.sync_block == 1040 - sync.CONFIRMATIONS and coll.ingested == 8
        assert {int(r.token_id) for r in repo.nft_items_by_status(s, "pending")} >= {9, 10, 11}
