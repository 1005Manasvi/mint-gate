"""Chain indexer + chain worker against a real local Hardhat node running the v2 contracts.

Needs Node + compiled contracts (`cd Blockchain; npm ci; npx hardhat compile`) and the test
database; skipped otherwise (see conftest.py).
"""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from chain import networks, registry
from chain.eip712 import Verdict, sign_verdict
from db import repo
from db.models import Attestation, Dispute, Token
from indexer.core import Indexer
from tests.conftest import artifact

pytestmark = pytest.mark.chain
NETWORK = "hardhattest"
DEV_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # account #0
BOND = 10**15


@pytest.fixture
def chain(hardhat_node, clean_db, fake_redis, tmp_path, monkeypatch):
    """Registry + NFT deployed like deploy-v2.js; account #0 holds every role (as on testnets)."""
    w3 = hardhat_node
    admin, artist, other, disputant = w3.eth.accounts[0], w3.eth.accounts[3], w3.eth.accounts[4], w3.eth.accounts[5]
    reg_art, nft_art = artifact("MintGateRegistry"), artifact("MintGateNFTV2")

    def deploy(art, *args):
        tx = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"]).constructor(*args).transact({"from": admin})
        rc = w3.eth.get_transaction_receipt(tx)
        return w3.eth.contract(address=rc.contractAddress, abi=art["abi"]), rc.blockNumber

    reg, reg_block = deploy(reg_art, 3600, admin, admin, admin, admin, admin, BOND)
    nft, nft_block = deploy(nft_art, reg.address)
    reg.functions.grantRole(reg.functions.COLLECTION_ROLE().call(), nft.address).transact({"from": admin})

    dep = {
        "version": 2,
        "network": NETWORK,
        "chainId": w3.eth.chain_id,
        "registry": {"address": reg.address, "deployBlock": reg_block, "abi": reg_art["abi"]},
        "nft": {"address": nft.address, "deployBlock": nft_block, "abi": nft_art["abi"]},
    }
    (tmp_path / f"{NETWORK}.json").write_text(json.dumps(dep))
    monkeypatch.setattr(networks.config, "DEPLOYMENTS_DIR", tmp_path)
    monkeypatch.setenv(f"RPC_URL_{NETWORK.upper()}", w3.provider.endpoint_uri)
    monkeypatch.setenv(f"CONFIRMATIONS_{NETWORK.upper()}", "0")
    monkeypatch.setattr(registry, "_clients", {})
    net = networks.get(NETWORK)
    return SimpleNamespace(
        w3=w3, net=net, reg=reg, nft=nft, admin=admin, artist=artist, other=other, disputant=disputant
    )


def verdict_for(c, image: str, report: str, decision=1, artist=None) -> Verdict:
    now = c.w3.eth.get_block("latest").timestamp
    return Verdict(
        image_hash=image * 32,
        report_hash=report * 32,
        policy_hash="cc" * 32,
        score=90,
        decision=decision,
        artist=artist or c.artist,
        issued_at=now,
        expires_at=now + 3600,
        nonce=int(report, 16),
    )


def submit_and_mint(c, v: Verdict):
    sig = sign_verdict(v, DEV_KEY, c.net.chain_id, c.reg.address)
    c.nft.functions.submitAndMint(v.as_tuple(), bytes.fromhex(sig[2:]), "ipfs://meta", 500).transact({"from": v.artist})
    return sig


def add_report(s, rid: str, v: Verdict, decision="PASS"):
    report = {
        "report_id": rid,
        "created_at": 1,
        "image_sha256": v.image_hash.hex(),
        "decision": decision,
        "score": v.score,
        "policy_version": "3.1.0",
        "policy_sha256": v.policy_hash.hex(),
        "models": {},
        "checks": {},
        "report_hash": v.report_hash.hex(),
    }
    sub = repo.create_submission(s, rid, v.artist, "x.png")
    return repo.save_report(s, sub, report, None)


def state(db):
    with db.session() as s:
        tokens = [
            (int(t.token_id), t.owner, t.image_sha256[:2], t.status)
            for t in s.scalars(select(Token).order_by(Token.token_id))
        ]
        disputes = [(int(d.dispute_id), d.status) for d in s.scalars(select(Dispute).order_by(Dispute.dispute_id))]
        events = s.execute(text("SELECT event FROM chain_events ORDER BY block, log_index")).scalars().all()
        status = s.execute(text("SELECT id, status FROM submissions ORDER BY id")).all()
    return tokens, disputes, events, status


def test_indexer_reproduces_chain_state(chain, clean_db):
    c = chain
    v1, v2, v3 = verdict_for(c, "a1", "b1"), verdict_for(c, "a2", "b2"), verdict_for(c, "a3", "b3")
    with clean_db.session() as s:  # token 1's artwork went through Mint-Gate
        add_report(s, "1" * 32, v1)
        sig1 = sign_verdict(v1, DEV_KEY, c.net.chain_id, c.reg.address)
        repo.add_attestation(s, "1" * 32, c.net.chain_id, c.reg.address, v1, sig1)
    for v in (v1, v2, v3):
        submit_and_mint(c, v)
    c.nft.functions.transferFrom(c.artist, c.other, 1).transact({"from": c.artist})
    reg = c.reg.functions
    for image in ("a2", "a3", "a1"):
        reg.openDispute(bytes.fromhex(image * 32), f"ipfs://evidence-{image}").transact(
            {"from": c.disputant, "value": BOND}
        )
    reg.resolveDispute(1, False).transact({"from": c.admin})  # a2: rejected -> active again
    reg.resolveDispute(2, True).transact({"from": c.admin})  # a3: upheld -> revoked for good

    ix = Indexer(c.net, w3=c.w3, chunk=3)  # small chunks: exercise paging
    assert ix.run_once() > 0
    tokens, disputes, events, status = state(clean_db)
    assert tokens == [(1, c.other, "a1", "flagged"), (2, c.artist, "a2", "active"), (3, c.artist, "a3", "revoked")]
    assert disputes == [(1, "rejected"), (2, "upheld"), (3, "open")]
    assert events.count("VerdictSubmitted") == events.count("ImageMinted") == 3
    assert status == [("1" * 32, "MINTED")]
    with clean_db.session() as s:
        assert s.scalars(select(Attestation)).one().submitted_tx  # marked from VerdictSubmitted
        assert [r.image_uri for r in repo.references_after(s, 0)] == [f"minted/{'1' * 32}.png"]
        cursor = s.execute(text("SELECT block FROM indexer_cursors")).scalar()
    assert cursor == c.w3.eth.block_number
    assert ix.run_once() == 0  # idempotent

    before = state(clean_db)
    ix.reset(c.net.deploy_block)  # python -m indexer --from-deploy
    assert state(clean_db)[0] == []
    ix.run_once()
    assert state(clean_db) == before

    with clean_db.engine().begin() as conn:  # from scratch: a brand-new database
        conn.execute(text("TRUNCATE chain_events, tokens, disputes, indexer_cursors"))
    Indexer(c.net, w3=c.w3).run_once()
    assert state(clean_db) == before


def test_indexer_waits_for_confirmations(chain, clean_db, monkeypatch):
    c = chain
    submit_and_mint(c, verdict_for(c, "a1", "b1"))
    monkeypatch.setenv(f"CONFIRMATIONS_{NETWORK.upper()}", "3")
    net = networks.get(NETWORK)
    Indexer(net, w3=c.w3).run_once()
    assert state(clean_db)[0] == []  # the mint is not 3 blocks deep yet
    for _ in range(3):
        c.w3.provider.make_request("evm_mine", [])
    Indexer(net, w3=c.w3).run_once()
    assert len(state(clean_db)[0]) == 1


def test_indexer_rewinds_after_reorg(chain, clean_db):
    c = chain
    snap = c.w3.provider.make_request("evm_snapshot", [])["result"]
    submit_and_mint(c, verdict_for(c, "a1", "b1"))
    ix = Indexer(c.net, w3=c.w3)
    ix.run_once()
    assert [t[2] for t in state(clean_db)[0]] == ["a1"]

    c.w3.provider.make_request("evm_revert", [snap])  # the mint is reorged out ...
    submit_and_mint(c, verdict_for(c, "a2", "b2"))  # ... and a different block takes its place
    for _ in range(4):
        c.w3.provider.make_request("evm_mine", [])
    ix.run_once()
    tokens, _, events, _ = state(clean_db)
    assert [t[2] for t in tokens] == ["a2"]
    assert events.count("ImageMinted") == 1
    with clean_db.session() as s:
        assert s.execute(text("SELECT count(*) FROM audit_log WHERE action = 'indexer.reorg'")).scalar() == 1


def test_chain_worker_anchors_and_resolves_review(chain, clean_db):
    """REVIEW verdict: the worker submits it, mirrors the approval, and the artist mints with `mint`."""
    from jobs import tasks

    c = chain
    v = verdict_for(c, "a4", "b4", decision=2)
    with clean_db.session() as s:
        add_report(s, "4" * 32, v, decision="REVIEW")
    tx = tasks.anchor_verdict("4" * 32, NETWORK)
    assert tx["tx_hash"] and tasks.anchor_verdict("4" * 32, NETWORK) == {"tx_hash": tx["tx_hash"]}  # idempotent
    client = registry.client(c.net)
    assert client.verdict_of(v.report_hash.hex())["decision"] == "REVIEW"
    assert client.verdict_of(v.report_hash.hex())["artist"] == c.artist

    with clean_db.session() as s:
        repo.get_report(s, "4" * 32).submission.status = "REVIEW_APPROVED"
    assert tasks.resolve_review("4" * 32, True, NETWORK)["tx_hash"]
    assert client.verdict_of(v.report_hash.hex())["decision"] == "REVIEW_APPROVED"

    c.nft.functions.mint(v.image_hash, "ipfs://meta", 0).transact({"from": c.artist})  # artist pays
    Indexer(c.net, w3=c.w3).run_once()
    tokens, _, _, status = state(clean_db)
    assert tokens == [(1, c.artist, "a4", "active")] and status == [("4" * 32, "MINTED")]
