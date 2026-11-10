"""Phase 6 governance: Safe proposals + owner signatures (API, DB) and, on a local Hardhat node, the
real Safe v1.4.1 bytecode: our SafeTx hash == the Safe's, 2-of-3 execution of the admin hand-over,
the timelocked policy change, and the indexer's projections of all of it."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from web3 import Web3

import config
from chain import networks, registry
from db.models import SafeProposal
from governance import actions
from governance import safe as gs
from governance.safe import SafeTx
from tests.conftest import OWNER_KEYS, OWNERS, ROOT, TEST_NETWORK, artifact

SAFE_VENDOR = ROOT / "Blockchain" / "vendor" / "safe-1.4.1"


@pytest.fixture
def net():
    return networks.get(TEST_NETWORK)


@pytest.fixture
def proposal(clean_db, net):
    with clean_db.session() as s:
        to, data, desc = actions.build(net, "accept-admin", [])
        p = gs.create(s, net, SafeTx(to=to, data=data, nonce=0), "accept-admin", desc)
        return p.safe_tx_hash


def test_sign_and_recover_roundtrip(net):
    tx = SafeTx(to=net.registry.address, data="0xcefc1429", nonce=3)
    safe_addr = net.safe.address
    sig = gs.sign(tx, net.chain_id, safe_addr, OWNER_KEYS[1])
    assert gs.recover(tx, net.chain_id, safe_addr, sig) == OWNERS[1]
    assert gs.recover(SafeTx(to=tx.to, data=tx.data, nonce=4), net.chain_id, safe_addr, sig) != OWNERS[1]
    with pytest.raises(gs.GovernanceError):
        gs.recover(tx, net.chain_id, safe_addr, "0x" + "00" * 64)
    packed = gs.pack({OWNERS[2]: "0x" + "bb" * 65, OWNERS[0]: "0x" + "aa" * 65})
    first = min(OWNERS[0], OWNERS[2], key=lambda a: int(a, 16))
    assert packed[:65] == bytes.fromhex(("aa" if first == OWNERS[0] else "bb") * 65)


def test_actions_encode_our_contracts(net):
    to, data, _ = actions.build(net, "grant-role", ["reviewer_role", OWNERS[0]])
    assert to == net.registry.address and data.startswith("0x2f2ff15d")
    assert Web3.keccak(text="REVIEWER_ROLE").hex().removeprefix("0x") in data
    with pytest.raises(gs.GovernanceError):
        actions.build(net, "grant-role", ["ROOT", OWNERS[0]])
    with pytest.raises(gs.GovernanceError):
        actions.build(net, "selfdestruct", [])


def test_owner_signatures_via_api(client, net, proposal, clean_db):
    tx_hash = proposal
    with clean_db.session() as s:
        p = s.scalars(select(SafeProposal)).one()
        tx = SafeTx.of(p)
    url = f"/api/governance/{TEST_NETWORK}/{tx_hash}/signatures"
    stranger = gs.sign(tx, net.chain_id, net.safe.address, "0x" + "99" * 32)
    resp = client.post(url, json={"signature": stranger})
    assert resp.status_code == 400 and "not an owner" in resp.get_json()["error"]
    assert client.post(url, json={"signature": "0x1234"}).status_code == 400
    assert client.post(f"/api/governance/{TEST_NETWORK}/0x{'00' * 32}/signatures", json={}).status_code == 404

    for i, key in enumerate(OWNER_KEYS[:2]):
        sig = gs.sign(tx, net.chain_id, net.safe.address, key)
        out = client.post(url, json={"signature": sig}).get_json()
        assert out["success"] and out["owner"] == OWNERS[i]
        assert out["proposal"]["ready"] is (i == 1)
    again = client.post(url, json={"signature": gs.sign(tx, net.chain_id, net.safe.address, OWNER_KEYS[0])})
    assert len(again.get_json()["proposal"]["signers"]) == 2  # idempotent per owner

    gov = client.get("/api/explorer/governance").get_json()["networks"][TEST_NETWORK]
    (p,) = gov["proposals"]
    assert p["kind"] == "accept-admin" and p["threshold"] == 2 and p["ready"]
    assert p["typed_data"]["primaryType"] == "SafeTx" and p["typed_data"]["domain"]["chainId"] == 31337
    assert gov["governance"]["owners"] == OWNERS


def test_execution_marks_proposal_and_replaces_same_nonce(clean_db, net, proposal):
    with clean_db.session() as s:
        to, data, desc = actions.build(net, "pause", [])
        alt = gs.create(s, net, SafeTx(to=to, data=data, nonce=0), "pause", desc)  # competes for nonce 0
        later = gs.create(s, net, SafeTx(to=to, data=data, nonce=1), "pause", desc)
        assert gs.next_nonce(s, net, 0) == 2
        alt_hash, later_id = alt.safe_tx_hash, later.id
    with clean_db.session() as s:
        assert gs.mark_executed(s, net.chain_id, net.safe.address, proposal, "0x" + "ab" * 32) == 1
        assert gs.mark_executed(s, net.chain_id, net.safe.address, "0x" + "00" * 32, "0x") == 0
    with clean_db.session() as s:
        st = {p.safe_tx_hash: p.status for p in s.scalars(select(SafeProposal))}
        assert st[proposal] == "executed" and st[alt_hash] == "replaced"
        assert s.get(SafeProposal, later_id).status == "pending"
    with clean_db.session() as s, pytest.raises(gs.GovernanceError):
        gs.add_signature(net, s.scalars(select(SafeProposal).where(SafeProposal.safe_tx_hash == proposal)).one(), "0x")


def test_policy_file_is_served_by_hash(client):
    raw = (config.BACKEND_DIR / "policy.json").read_bytes()
    resp = client.get(f"/api/policy/{hashlib.sha256(raw).hexdigest()}.json")
    assert resp.status_code == 200 and resp.data == raw
    assert client.get(f"/api/policy/{'0' * 64}.json").status_code == 404


# ---- the real Safe on a Hardhat node --------------------------------------------------------------

HH_KEYS = [  # Hardhat accounts #1-#3 (public test data): the Safe owners here
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
]
DEV_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def _vendor(name):
    return json.loads((SAFE_VENDOR / f"{name}.json").read_text())


@pytest.fixture
def gov_chain(hardhat_node, clean_db, fake_redis, tmp_path, monkeypatch):
    w3 = hardhat_node
    admin = w3.eth.accounts[0]
    owners = [w3.eth.account.from_key(k).address for k in HH_KEYS]

    def deploy(abi, bytecode, *args):
        tx = w3.eth.contract(abi=abi, bytecode=bytecode).constructor(*args).transact({"from": admin})
        rc = w3.eth.get_transaction_receipt(tx)
        return w3.eth.contract(address=rc.contractAddress, abi=abi), rc.blockNumber

    def deploy_art(name, *args):
        a = artifact(name)
        return deploy(a["abi"], a["bytecode"], *args)

    reg, reg_block = deploy_art("MintGateRegistry", 3600, admin, admin, admin, admin, admin, 10**15)
    nft, nft_block = deploy_art("MintGateNFTV2", reg.address)
    reg.functions.grantRole(reg.functions.COLLECTION_ROLE().call(), nft.address).transact({"from": admin})
    single = {
        n: deploy(_vendor(n)["abi"], _vendor(n)["bytecode"])[0]
        for n in ("Safe", "SafeProxyFactory", "CompatibilityFallbackHandler")
    }
    safe_abi = _vendor("Safe")["abi"]
    setup = w3.eth.contract(abi=safe_abi).encode_abi(
        "setup",
        args=[
            owners,
            2,
            "0x" + "00" * 20,
            b"",
            single["CompatibilityFallbackHandler"].address,
            "0x" + "00" * 20,
            0,
            "0x" + "00" * 20,
        ],
    )
    fac = single["SafeProxyFactory"]
    fn = fac.functions.createProxyWithNonce(single["Safe"].address, bytes.fromhex(setup[2:]), 6)
    safe_addr = fn.call({"from": admin})
    fn.transact({"from": admin})
    safe_c = w3.eth.contract(address=safe_addr, abi=safe_abi)

    tl, tl_block = deploy_art("MintGateTimelock", 3600, [safe_addr], ["0x" + "00" * 20])
    pol_hash = hashlib.sha256((config.BACKEND_DIR / "policy.json").read_bytes()).digest()
    pol, pol_block = deploy_art("PolicyRegistry", tl.address, [pol_hash], ["5.1.0"], ["ipfs://policy"])
    res, res_block = deploy_art("DisputeResolver", reg.address)
    reg.functions.grantRole(reg.functions.REVIEWER_ROLE().call(), res.address).transact({"from": admin})
    reg.functions.beginDefaultAdminTransfer(safe_addr).transact({"from": admin})

    def c(contract, block, **extra):
        return {"address": contract.address, "deployBlock": block, "abi": contract.abi, **extra}

    name = "hhgov"
    dep = {
        "version": 2,
        "network": name,
        "chainId": w3.eth.chain_id,
        "disputeBond": str(10**15),
        "registry": c(reg, reg_block),
        "nft": c(nft, nft_block),
        "governance": {
            "safe": {"address": safe_addr, "owners": owners, "threshold": 2, "deployBlock": reg_block, "abi": safe_abi},
            "timelock": c(tl, tl_block, minDelay=3600),
            "policyRegistry": c(pol, pol_block),
            "disputeResolver": c(res, res_block),
        },
    }
    (tmp_path / f"{name}.json").write_text(json.dumps(dep))
    monkeypatch.setattr(networks.config, "DEPLOYMENTS_DIR", tmp_path)
    monkeypatch.setenv(f"RPC_URL_{name.upper()}", w3.provider.endpoint_uri)
    monkeypatch.setenv(f"CONFIRMATIONS_{name.upper()}", "0")
    monkeypatch.setattr(registry, "_clients", {})
    return SimpleNamespace(
        w3=w3,
        net=networks.get(name),
        reg=reg,
        safe=safe_c,
        tl=tl,
        pol=pol,
        res=res,
        admin=admin,
        owners=owners,
        pol_hash=pol_hash,
    )


@pytest.mark.chain
def test_safe_handover_and_timelocked_policy_on_a_node(gov_chain, clean_db, monkeypatch):
    from governance.__main__ import main as cli
    from indexer.core import Indexer

    g = gov_chain
    w3, net = g.w3, g.net

    def exec_proposal(h):
        monkeypatch.setenv("SAFE_OWNER_KEY", HH_KEYS[2])  # any account may execute; owner #3 pays
        cli(["exec", net.name, h])

    # the admin delay has to pass before the Safe can accept
    w3.provider.make_request("evm_increaseTime", [3601])
    w3.provider.make_request("evm_mine", [])
    monkeypatch.setenv("SAFE_OWNER_KEY", HH_KEYS[0])
    cli(["propose", net.name, "accept-admin", "--sign"])  # checks our hash == Safe.getTransactionHash
    with clean_db.session() as s:
        p = s.scalars(select(SafeProposal)).one()
        h = p.safe_tx_hash
        assert list(p.signatures) == [g.owners[0]]
    with pytest.raises(SystemExit):  # 1 of 2
        exec_proposal(h)
    monkeypatch.setenv("SAFE_OWNER_KEY", HH_KEYS[1])
    cli(["sign", net.name, h])
    exec_proposal(h)
    assert g.reg.functions.defaultAdmin().call() == g.safe.address
    assert not g.reg.functions.hasRole(b"\x00" * 32, g.admin).call()

    # policy change: Safe schedules through the timelock, anyone executes after the delay
    new = {"version": "9.9.9", "sha256": "ab" * 32, "uri": "ipfs://new-policy"}
    monkeypatch.setattr(actions, "load_policy", lambda v: new)
    monkeypatch.setenv("SAFE_OWNER_KEY", HH_KEYS[2])
    cli(["propose", net.name, "set-policy", "--sign"])
    with clean_db.session() as s:
        h2 = s.scalars(select(SafeProposal).where(SafeProposal.kind == "set-policy")).one().safe_tx_hash
    monkeypatch.setenv("SAFE_OWNER_KEY", HH_KEYS[0])
    cli(["sign", net.name, h2])
    exec_proposal(h2)
    with pytest.raises(SystemExit):  # not ready before the delay
        cli(["timelock-exec", net.name, h2])
    w3.provider.make_request("evm_increaseTime", [3601])
    w3.provider.make_request("evm_mine", [])
    cli(["timelock-exec", net.name, h2])
    assert g.pol.functions.current().call()[2] == "9.9.9"

    # the indexer sees all of it
    Indexer(net).run_once()
    with clean_db.session() as s:
        assert {p.status for p in s.scalars(select(SafeProposal))} == {"executed"}
    import server

    server.limiter.reset()
    c = server.app.test_client()
    pols = c.get("/api/explorer/policies").get_json()["networks"][net.name]
    assert [x["version"] for x in pols["history"]] == ["5.1.0", "9.9.9"]
    assert pols["history"][0]["sha256"] == g.pol_hash.hex()
    (op,) = pols["operations"]
    assert op["status"] == "executed" and op["decoded"]["version"] == "9.9.9"
    gov = c.get("/api/explorer/governance").get_json()["networks"][net.name]
    assert g.res.address in gov["role_holders"]["REVIEWER_ROLE"]
    assert any(e["event"] == "DefaultAdminTransferScheduled" for e in gov["history"])


@pytest.mark.chain
def test_reasoned_resolution_is_indexed_from_real_logs(gov_chain, clean_db):
    from chain.eip712 import Verdict, sign_verdict
    from db.models import Dispute, Token
    from indexer.core import Indexer

    g = gov_chain
    w3, net = g.w3, g.net
    artist, disputant = w3.eth.accounts[5], w3.eth.accounts[6]
    now = w3.eth.get_block("latest").timestamp
    v = Verdict(
        image_hash="ee" * 32,
        report_hash="ef" * 32,
        policy_hash=g.pol_hash.hex(),
        score=90,
        decision=1,
        artist=artist,
        issued_at=now,
        expires_at=now + 3600,
        nonce=77,
    )
    sig = sign_verdict(v, DEV_KEY, net.chain_id, g.reg.address)
    nft = w3.eth.contract(address=net.nft.address, abi=net.nft.abi)
    nft.functions.submitAndMint(v.as_tuple(), bytes.fromhex(sig[2:]), "ipfs://m", 0).transact({"from": artist})
    g.reg.functions.openDispute(b"\xee" * 32, "ipfs://evidence").transact({"from": disputant, "value": 10**15})
    reason = hashlib.sha256(b"reason").digest()
    g.res.functions.resolveDispute(1, True, reason, "ipfs://reason").transact({"from": g.admin})  # admin = reviewer

    Indexer(net).run_once()
    with clean_db.session() as s:
        d = s.scalars(select(Dispute)).one()
        assert (d.status, d.reviewer, d.reason_uri, d.reason_hash) == ("upheld", g.admin, "ipfs://reason", reason.hex())
        assert s.scalars(select(Token)).one().status == "revoked"
    assert nft.functions.tokenURI(1).call() == nft.functions.REVOKED_URI().call()
