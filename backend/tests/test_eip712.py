"""EIP-712 verdict signer (chain/eip712.py).

The unit tests compare against a vector produced by ethers.js (an independent implementation).
`test_signature_verifies_on_chain` starts a local Hardhat node, deploys MintGateRegistry and
submits a Python-signed verdict; it needs `npm ci && npx hardhat compile` in Blockchain/.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest
from web3 import Web3

from chain.eip712 import MAX_VALIDITY, Verdict, recover_signer, sign_verdict, verdict_digest

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "Blockchain" / "artifacts" / "contracts" / "v2" / "MintGateRegistry.sol" / "MintGateRegistry.json"

# Hardhat's public dev accounts #1 (verifier) and #3 (artist). Never used outside local chains.
VERIFIER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
VERIFIER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
ARTIST = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
REGISTRY = "0x5FbDB2315678afecb367f032d93F642f64180aa3"  # first deployment of account #0

# Produced with ethers 6 TypedDataEncoder.hash / Wallet.signTypedData for VECTOR below.
ETHERS_DIGEST = "3a7de7b6c1b3ac7948b0f93cb3594f0d7f3d087d1f25f596d3f4c0e279b8c56a"
ETHERS_SIGNATURE = (
    "0x3caf5b549f467ac2576ed7bc244b36d0c4aaef3a7a3ba234e97b1737c93cb307"
    "21b389ac49c31b6435d8e7b00b5b739b0d3326b752534adcb6066c3bbaa552b61c"
)
VECTOR = Verdict(
    image_hash="11" * 32,
    report_hash="22" * 32,
    policy_hash="33" * 32,
    score=87,
    decision=1,
    artist=ARTIST,
    issued_at=1_800_000_000,
    expires_at=1_800_086_400,
    nonce=42,
)


def test_digest_and_signature_match_ethers():
    assert verdict_digest(VECTOR, 31337, REGISTRY).hex() == ETHERS_DIGEST
    # RFC 6979 signatures are deterministic, so the bytes match ethers exactly.
    assert sign_verdict(VECTOR, VERIFIER_KEY, 31337, REGISTRY) == ETHERS_SIGNATURE
    assert recover_signer(VECTOR, ETHERS_SIGNATURE, 31337, REGISTRY) == VERIFIER


def test_domain_binds_chain_and_contract():
    sig = sign_verdict(VECTOR, VERIFIER_KEY, 31337, REGISTRY)
    assert recover_signer(VECTOR, sig, 84532, REGISTRY) != VERIFIER
    assert recover_signer(VECTOR, sig, 31337, ARTIST) != VERIFIER


def test_every_field_is_signed():
    sig = sign_verdict(VECTOR, VERIFIER_KEY, 31337, REGISTRY)
    fields = dict(VECTOR.__dict__)
    changes = {
        "image_hash": "44" * 32,
        "report_hash": "44" * 32,
        "policy_hash": "44" * 32,
        "score": 88,
        "decision": 3,
        "artist": VERIFIER,
        "issued_at": VECTOR.issued_at + 1,
        "expires_at": VECTOR.expires_at + 1,
        "nonce": 43,
    }
    for name, value in changes.items():
        tampered = Verdict(**(fields | {name: value}))
        assert recover_signer(tampered, sig, 31337, REGISTRY) != VERIFIER, name


def test_signature_is_low_s():
    n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    for nonce in range(20):
        v = Verdict(**(VECTOR.__dict__ | {"nonce": nonce}))
        raw = bytes.fromhex(sign_verdict(v, VERIFIER_KEY, 31337, REGISTRY)[2:])
        assert len(raw) == 65 and raw[64] in (27, 28)
        assert int.from_bytes(raw[32:64], "big") <= n // 2


def test_from_report():
    report = {
        "report_hash": "aa" * 32,
        "image_sha256": "bb" * 32,
        "policy_sha256": "cc" * 32,
        "score": 93,
        "decision": "PASS",
    }
    v = Verdict.from_report(report, ARTIST.lower(), now=1_000)
    assert (v.report_hash, v.image_hash, v.policy_hash) == (b"\xaa" * 32, b"\xbb" * 32, b"\xcc" * 32)
    assert (v.score, v.decision, v.artist) == (93, 1, ARTIST)
    assert (v.issued_at, v.expires_at) == (1_000, 1_000 + 86_400)
    assert v.nonce != Verdict.from_report(report, ARTIST, now=1_000).nonce  # random nonces
    assert v.to_json()["nonce"] == str(v.nonce)
    assert Verdict.from_report(report | {"decision": "REVIEW"}, ARTIST).decision == 2


@pytest.mark.parametrize(
    "change",
    [
        {"score": 101},
        {"decision": 0},
        {"decision": 4},
        {"expires_at": VECTOR.issued_at},
        {"expires_at": VECTOR.issued_at + MAX_VALIDITY + 1},
        {"image_hash": "11" * 31},
        {"artist": "0x1234"},
        {"nonce": 2**256},
    ],
)
def test_invalid_verdicts_rejected(change):
    with pytest.raises(ValueError):
        Verdict(**(VECTOR.__dict__ | change))


# ---- on chain -------------------------------------------------------------------------------


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def hardhat_node():
    npx = shutil.which("npx")
    if not npx or not ARTIFACT.exists():
        pytest.skip("needs Node and compiled contracts (cd Blockchain; npm ci; npx hardhat compile)")
    port = _free_port()
    kwargs = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    )
    proc = subprocess.Popen(
        [npx, "hardhat", "node", "--port", str(port)],
        cwd=ROOT / "Blockchain",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
    w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{port}"))
    try:
        for _ in range(120):
            if w3.is_connected():
                break
            time.sleep(0.5)
        else:
            pytest.fail("hardhat node did not start")
        yield w3
    finally:
        if os.name == "nt":  # npx spawns node as a child: kill the whole tree
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)


@pytest.mark.chain
def test_signature_verifies_on_chain(hardhat_node):
    w3 = hardhat_node
    admin, _, relayer = w3.eth.accounts[:3]
    art = json.loads(ARTIFACT.read_text())
    factory = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
    tx = factory.constructor(3600, admin, VERIFIER, admin, admin, admin, 10**15).transact({"from": admin})
    registry = w3.eth.contract(address=w3.eth.get_transaction_receipt(tx).contractAddress, abi=art["abi"])

    now = w3.eth.get_block("latest").timestamp
    v = Verdict(**(VECTOR.__dict__ | {"issued_at": now, "expires_at": now + 3600}))
    chain_id = w3.eth.chain_id
    sig = sign_verdict(v, VERIFIER_KEY, chain_id, registry.address)

    assert registry.functions.hashVerdict(v.as_tuple()).call() == verdict_digest(v, chain_id, registry.address)
    tx = registry.functions.submitVerdict(v.as_tuple(), sig).transact({"from": relayer})  # anyone may submit
    assert w3.eth.get_transaction_receipt(tx).status == 1

    record = registry.functions.verdictOf(v.report_hash).call()
    assert record[2] == ARTIST and record[6] == VERIFIER  # artist, signer
    assert registry.functions.isMintable(v.image_hash, ARTIST).call() == [True, v.report_hash]

    with pytest.raises(Exception, match="NonceAlreadyUsed|revert"):  # replay
        registry.functions.submitVerdict(v.as_tuple(), sig).transact({"from": relayer})
    other = Verdict(**(v.__dict__ | {"nonce": 7, "report_hash": "55" * 32}))
    forged = sign_verdict(other, "0x" + "42" * 32, chain_id, registry.address)
    with pytest.raises(Exception, match="UnauthorizedSigner|revert"):
        registry.functions.submitVerdict(other.as_tuple(), forged).transact({"from": relayer})
