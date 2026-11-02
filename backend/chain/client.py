"""web3.py client for MintGateNFT.

The backend acts as the VERIFIER (anchors verdicts, mints) and, in the demo,
also as the REVIEWER. The private key comes from VERIFIER_PRIVATE_KEY; when
connected to a local Hardhat chain (chainId 31337) it falls back to Hardhat's
public dev account so the demo works with zero configuration. A real key is
never hard-coded.
"""
import json
import threading

from web3 import Web3

from config import DEPLOYMENT_FILE, EXPLORER_URL, HARDHAT_DEV_KEY, RPC_URL, VERIFIER_PRIVATE_KEY

DECISION_CODE = {"PASS": 1, "REVIEW": 2, "FAIL": 3}
DECISION_NAME = {0: "NONE", 1: "PASS", 2: "REVIEW", 3: "FAIL", 4: "REVIEW_APPROVED", 5: "REVIEW_REJECTED"}


class ChainUnavailable(RuntimeError):
    pass


def b32(hex_digest: str) -> bytes:
    raw = bytes.fromhex(hex_digest.removeprefix("0x"))
    if len(raw) != 32:
        raise ValueError("expected a 32-byte hash")
    return raw


class ChainClient:
    def __init__(self):
        self._lock = threading.Lock()  # serialise nonces
        self.error = None
        try:
            dep = json.loads(DEPLOYMENT_FILE.read_text())
            self.w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 20}))
            if not self.w3.is_connected():
                raise ChainUnavailable(f"no blockchain node at {RPC_URL}")
            self.chain_id = self.w3.eth.chain_id
            if self.chain_id != dep["chainId"]:
                raise ChainUnavailable(f"node chainId {self.chain_id} != deployment {dep['chainId']}")
            key = VERIFIER_PRIVATE_KEY or (HARDHAT_DEV_KEY if self.chain_id == 31337 else "")
            if not key:
                raise ChainUnavailable("VERIFIER_PRIVATE_KEY not set")
            self.account = self.w3.eth.account.from_key(key)
            self.address = Web3.to_checksum_address(dep["address"])
            if not self.w3.eth.get_code(self.address):
                raise ChainUnavailable("contract not deployed on this node (restarted? run deploy again)")
            self.contract = self.w3.eth.contract(address=self.address, abi=dep["abi"])
            self.deploy_block = int(dep.get("deployBlock", 0))
            self.network = dep.get("network", "unknown")
            self.ok = True
        except FileNotFoundError:
            self.ok, self.error = False, "contract not deployed - run Blockchain deploy script"
        except Exception as exc:  # node down, bad config, ...
            self.ok, self.error = False, str(exc)

    def status(self):
        if not self.ok:
            return {"connected": False, "error": self.error}
        return {"connected": True, "chain_id": self.chain_id, "network": self.network,
                "contract": self.address, "explorer": EXPLORER_URL,
                "verifier": self.account.address, "block": self.w3.eth.block_number,
                "verifier_balance_eth": float(Web3.from_wei(self.w3.eth.get_balance(self.account.address), "ether")),
                "total_verifications": self.contract.functions.totalVerifications().call(),
                "total_minted": self.contract.functions.totalMinted().call()}

    def _send(self, fn):
        if not self.ok:
            raise ChainUnavailable(self.error)
        with self._lock:
            tx = fn.build_transaction({
                "from": self.account.address,
                "nonce": self.w3.eth.get_transaction_count(self.account.address, "pending"),
                "chainId": self.chain_id,
            })
            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt.status != 1:
            raise RuntimeError(f"transaction reverted: {tx_hash.hex()}")
        return receipt

    @staticmethod
    def _tx(receipt):
        return {"tx_hash": "0x" + receipt.transactionHash.hex().removeprefix("0x"),
                "block": receipt.blockNumber, "gas_used": receipt.gasUsed}

    def record(self, report):
        fn = self.contract.functions.recordVerification(
            b32(report["report_hash"]), b32(report["image_sha256"]), int(report["score"]),
            DECISION_CODE[report["decision"]], b32(report["policy_sha256"]), report["policy_version"])
        return self._tx(self._send(fn))

    def resolve_review(self, report_hash, approved: bool):
        return self._tx(self._send(self.contract.functions.resolveReview(b32(report_hash), approved)))

    def mint(self, to, report_hash, token_uri):
        receipt = self._send(self.contract.functions.mintVerified(
            Web3.to_checksum_address(to), b32(report_hash), token_uri))
        events = self.contract.events.VerifiedMint().process_receipt(receipt)
        out = self._tx(receipt)
        out["token_id"] = int(events[0]["args"]["tokenId"]) if events else None
        return out

    def verification(self, report_hash):
        v = self.contract.functions.getVerification(b32(report_hash)).call()
        image, policy, score, decision, ts, verifier, token_id, policy_version = v
        return {"image_sha256": image.hex(), "policy_sha256": policy.hex(), "score": score,
                "decision": DECISION_NAME[decision], "timestamp": ts, "verifier": verifier,
                "token_id": token_id, "policy_version": policy_version}

    def minted_tokens(self, chunk=50_000):
        """All VerifiedMint events since deployment (paged: public RPCs cap log ranges)."""
        if not self.ok:
            return []
        event = self.contract.events.VerifiedMint()
        head = self.w3.eth.block_number
        out = []
        start = self.deploy_block
        while start <= head:
            end = min(start + chunk - 1, head)
            for log in event.get_logs(from_block=start, to_block=end):
                a = log["args"]
                out.append({"token_id": int(a["tokenId"]), "report_hash": a["reportHash"].hex(),
                            "token_uri": self.contract.functions.tokenURI(int(a["tokenId"])).call()})
            start = end + 1
        return out

    def token(self, token_id: int):
        f = self.contract.functions
        report_hash = f.reportOfToken(token_id).call()
        if report_hash == b"\x00" * 32:
            return None
        return {"token_id": token_id, "owner": f.ownerOf(token_id).call(),
                "token_uri": f.tokenURI(token_id).call(), "report_hash": report_hash.hex(),
                "verification": self.verification(report_hash.hex())}
