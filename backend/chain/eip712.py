"""EIP-712 verdict signing for MintGateRegistry (contracts v2).

The backend no longer sends transactions for PASS verdicts: it signs a typed `Verdict` and
hands it to the artist, who submits it and mints from their own wallet
(`MintGateNFTV2.submitAndMint`). The registry recovers the signer and checks that it holds
VERIFIER_ROLE. Must match `VERDICT_TYPEHASH` in `Blockchain/contracts/v2/MintGateRegistry.sol`.
"""

import secrets
import time
from dataclasses import dataclass

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

DOMAIN_NAME = "MintGateRegistry"
DOMAIN_VERSION = "1"
DECISION_CODE = {"PASS": 1, "REVIEW": 2, "FAIL": 3}
MAX_VALIDITY = 30 * 24 * 3600  # MintGateRegistry.MAX_VALIDITY
DEFAULT_VALIDITY = 24 * 3600

VERDICT_TYPES = {
    "Verdict": [
        {"name": "imageHash", "type": "bytes32"},
        {"name": "reportHash", "type": "bytes32"},
        {"name": "policyHash", "type": "bytes32"},
        {"name": "score", "type": "uint8"},
        {"name": "decision", "type": "uint8"},
        {"name": "artist", "type": "address"},
        {"name": "issuedAt", "type": "uint64"},
        {"name": "expiresAt", "type": "uint64"},
        {"name": "nonce", "type": "uint256"},
    ]
}


def _b32(value) -> bytes:
    raw = value if isinstance(value, bytes) else bytes.fromhex(value.removeprefix("0x"))
    if len(raw) != 32:
        raise ValueError("expected a 32-byte hash")
    return raw


@dataclass(frozen=True)
class Verdict:
    image_hash: bytes
    report_hash: bytes
    policy_hash: bytes
    score: int
    decision: int
    artist: str
    issued_at: int
    expires_at: int
    nonce: int

    def __post_init__(self):
        for name in ("image_hash", "report_hash", "policy_hash"):
            object.__setattr__(self, name, _b32(getattr(self, name)))
        object.__setattr__(self, "artist", Web3.to_checksum_address(self.artist))
        if not 0 <= self.score <= 100:
            raise ValueError("score must be 0-100")
        if self.decision not in DECISION_CODE.values():
            raise ValueError("decision must be PASS, REVIEW or FAIL")
        if not 0 < self.expires_at - self.issued_at <= MAX_VALIDITY:
            raise ValueError(f"validity window must be 1 s to {MAX_VALIDITY} s")
        if not 0 <= self.nonce < 2**256:
            raise ValueError("nonce must fit in uint256")

    @classmethod
    def from_report(cls, report: dict, artist: str, validity: int = DEFAULT_VALIDITY, now: int | None = None):
        """Build the verdict for a pipeline report (`report_hash`, `image_sha256`, ...)."""
        issued = int(time.time()) if now is None else now
        return cls(
            image_hash=_b32(report["image_sha256"]),
            report_hash=_b32(report["report_hash"]),
            policy_hash=_b32(report["policy_sha256"]),
            score=int(report["score"]),
            decision=DECISION_CODE[report["decision"]],
            artist=artist,
            issued_at=issued,
            expires_at=issued + validity,
            nonce=secrets.randbits(256),
        )

    @classmethod
    def from_json(cls, d: dict):
        """Inverse of `to_json` (attestations are stored in that form)."""
        return cls(
            image_hash=d["imageHash"],
            report_hash=d["reportHash"],
            policy_hash=d["policyHash"],
            score=int(d["score"]),
            decision=int(d["decision"]),
            artist=d["artist"],
            issued_at=int(d["issuedAt"]),
            expires_at=int(d["expiresAt"]),
            nonce=int(d["nonce"]),
        )

    def message(self) -> dict:
        """The EIP-712 message (field names as in Solidity)."""
        return {
            "imageHash": self.image_hash,
            "reportHash": self.report_hash,
            "policyHash": self.policy_hash,
            "score": self.score,
            "decision": self.decision,
            "artist": self.artist,
            "issuedAt": self.issued_at,
            "expiresAt": self.expires_at,
            "nonce": self.nonce,
        }

    def as_tuple(self) -> tuple:
        """Argument for `submitVerdict` / `submitAndMint` via web3.py."""
        return tuple(self.message().values())

    def to_json(self) -> dict:
        """JSON-safe form for the API / frontend (hex bytes, decimal strings for uint64/256)."""
        return {
            "imageHash": "0x" + self.image_hash.hex(),
            "reportHash": "0x" + self.report_hash.hex(),
            "policyHash": "0x" + self.policy_hash.hex(),
            "score": self.score,
            "decision": self.decision,
            "artist": self.artist,
            "issuedAt": str(self.issued_at),
            "expiresAt": str(self.expires_at),
            "nonce": str(self.nonce),
        }


def domain(chain_id: int, registry: str) -> dict:
    return {
        "name": DOMAIN_NAME,
        "version": DOMAIN_VERSION,
        "chainId": chain_id,
        "verifyingContract": Web3.to_checksum_address(registry),
    }


def _signable(verdict: Verdict, chain_id: int, registry: str):
    return encode_typed_data(
        domain_data=domain(chain_id, registry), message_types=VERDICT_TYPES, message_data=verdict.message()
    )


def verdict_digest(verdict: Verdict, chain_id: int, registry: str) -> bytes:
    """EIP-712 digest; equals `MintGateRegistry.hashVerdict(verdict)`."""
    m = _signable(verdict, chain_id, registry)  # EIP-191 version 0x01: header = domain separator
    return Web3.keccak(b"\x19" + m.version + m.header + m.body)


def sign_verdict(verdict: Verdict, private_key: str, chain_id: int, registry: str) -> str:
    """65-byte r||s||v signature (low-s, v = 27/28) as 0x-hex."""
    signed = Account.sign_message(_signable(verdict, chain_id, registry), private_key=private_key)
    return "0x" + bytes(signed.signature).hex()


def recover_signer(verdict: Verdict, signature: str, chain_id: int, registry: str) -> str:
    return Account.recover_message(_signable(verdict, chain_id, registry), signature=signature)
