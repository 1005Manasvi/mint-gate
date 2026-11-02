"""web3.py client for MintGateRegistry (contracts v2).

The backend sends only two kinds of transaction: `submitVerdict` for REVIEW / FAIL verdicts
(PASS verdicts are submitted by the artist with `submitAndMint`) and `resolveReview`. Every
process that sends (chain workers, possibly several) takes a Redis lock per sender address
around nonce selection + broadcast, so concurrent senders never reuse a nonce.
"""

import threading
from contextlib import nullcontext

from web3 import Web3

import config
from chain.eip712 import Verdict
from chain.networks import Network

DECISION_NAME = {
    0: "NONE",
    1: "PASS",
    2: "REVIEW",
    3: "FAIL",
    4: "REVIEW_APPROVED",
    5: "REVIEW_REJECTED",
    6: "VOIDED",
}
LOCAL_CHAINS = {31337, 1337}


class ChainUnavailable(RuntimeError):
    pass


def b32(hex_digest: str) -> bytes:
    raw = bytes.fromhex(hex_digest.removeprefix("0x"))
    if len(raw) != 32:
        raise ValueError("expected a 32-byte hash")
    return raw


def tx_hash_hex(h) -> str:
    return "0x" + bytes(h).hex()


class RegistryClient:
    def __init__(self, net: Network, redis=None):
        self.net = net
        self.redis = redis
        self.w3 = Web3(Web3.HTTPProvider(net.rpc_url, request_kwargs={"timeout": 20}))
        if not self.w3.is_connected():
            raise ChainUnavailable(f"no node at {net.rpc_url} ({net.name})")
        if self.w3.eth.chain_id != net.chain_id:
            raise ChainUnavailable(f"{net.name}: node chainId {self.w3.eth.chain_id} != {net.chain_id}")
        self.registry = self.w3.eth.contract(
            address=Web3.to_checksum_address(net.registry.address), abi=net.registry.abi
        )
        key = net.signer_key()
        self.account = self.w3.eth.account.from_key(key) if key else None
        self._local_lock = threading.Lock()

    # ---- reads ---------------------------------------------------------------------------

    def verdict_of(self, report_hash: str) -> dict:
        image, policy, artist, score, decision, issued, signer, expires = self.registry.functions.verdictOf(
            b32(report_hash)
        ).call()
        return {
            "image_sha256": image.hex(),
            "policy_sha256": policy.hex(),
            "artist": artist,
            "score": score,
            "decision": DECISION_NAME[decision],
            "issued_at": issued,
            "signer": signer,
            "expires_at": expires,
        }

    # ---- writes --------------------------------------------------------------------------

    def _lock(self):
        if self.redis is None:
            return nullcontext()
        name = f"mintgate:txlock:{self.net.chain_id}:{self.account.address}"
        return self.redis.lock(name, timeout=120, blocking_timeout=120)

    def _fees(self) -> dict:
        if self.net.chain_id in LOCAL_CHAINS:
            return {}
        # Cap the tip: node defaults (often 1 gwei) cost more than the gas itself on testnets.
        tip = Web3.to_wei(config.network_setting("PRIORITY_FEE_GWEI", self.net.name, "0.01"), "gwei")
        base = self.w3.eth.get_block("latest")["baseFeePerGas"]
        return {"maxPriorityFeePerGas": tip, "maxFeePerGas": 2 * base + tip}

    def _send(self, fn) -> dict:
        if self.account is None:
            raise ChainUnavailable(f"no verifier key for {self.net.name}")
        with self._local_lock, self._lock():
            tx = fn.build_transaction(
                {
                    "from": self.account.address,
                    "nonce": self.w3.eth.get_transaction_count(self.account.address, "pending"),
                    "chainId": self.net.chain_id,
                    **self._fees(),
                }
            )
            tx_hash = self.w3.eth.send_raw_transaction(self.account.sign_transaction(tx).raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        if receipt.status != 1:
            raise RuntimeError(f"transaction reverted: {tx_hash_hex(tx_hash)}")
        return {
            "tx_hash": tx_hash_hex(receipt.transactionHash),
            "block": receipt.blockNumber,
            "gas_used": receipt.gasUsed,
        }

    def submit_verdict(self, verdict: Verdict, signature: str) -> dict:
        return self._send(self.registry.functions.submitVerdict(verdict.as_tuple(), bytes.fromhex(signature[2:])))

    def resolve_review(self, report_hash: str, approved: bool) -> dict:
        return self._send(self.registry.functions.resolveReview(b32(report_hash), approved))


_clients: dict[str, RegistryClient] = {}
_clients_lock = threading.Lock()


def client(net: Network, redis=None) -> RegistryClient:
    """Cached per network; reconnects when the previous attempt failed."""
    with _clients_lock:
        c = _clients.get(net.name)
        if c is None:
            c = _clients[net.name] = RegistryClient(net, redis)
        return c
