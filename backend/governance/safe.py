"""Safe v1.4.1 transactions: EIP-712 SafeTx hash, owner signatures, proposals, execution.

A proposal is a SafeTx stored in `safe_proposals` until enough owners signed it. Owners sign
the EIP-712 typed data in their own wallet (governance page, gasless) or with `python -m governance
sign`; the API only stores a signature after recovering it to a current owner of that Safe.
Anyone may then execute: `execTransaction` with the signatures in ascending owner order.
The hash is checked against the Safe's own `getTransactionHash` in tests/test_governance.py.
"""

from dataclasses import dataclass
from decimal import Decimal

from eth_account import Account
from eth_account.messages import encode_typed_data
from sqlalchemy import func, select
from web3 import Web3

from chain.networks import Network
from db.models import SafeProposal

ZERO = "0x" + "00" * 20
SAFE_TX_TYPES = {
    "EIP712Domain": [
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "SafeTx": [
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "data", "type": "bytes"},
        {"name": "operation", "type": "uint8"},
        {"name": "safeTxGas", "type": "uint256"},
        {"name": "baseGas", "type": "uint256"},
        {"name": "gasPrice", "type": "uint256"},
        {"name": "gasToken", "type": "address"},
        {"name": "refundReceiver", "type": "address"},
        {"name": "nonce", "type": "uint256"},
    ],
}


class GovernanceError(ValueError):
    """Rejected proposal / signature; the message is safe to show."""


@dataclass(frozen=True)
class SafeTx:
    to: str
    data: str  # 0x hex
    nonce: int
    value: int = 0
    operation: int = 0  # 0 = CALL (DELEGATECALL is never proposed)

    def message(self) -> dict:
        return {
            "to": Web3.to_checksum_address(self.to),
            "value": self.value,
            "data": bytes.fromhex(self.data.removeprefix("0x")),
            "operation": self.operation,
            "safeTxGas": 0,
            "baseGas": 0,
            "gasPrice": 0,
            "gasToken": ZERO,
            "refundReceiver": ZERO,
            "nonce": self.nonce,
        }

    def typed_data(self, chain_id: int, safe: str) -> dict:
        return {
            "types": SAFE_TX_TYPES,
            "primaryType": "SafeTx",
            "domain": {"chainId": chain_id, "verifyingContract": Web3.to_checksum_address(safe)},
            "message": self.message(),
        }

    def signable(self, chain_id: int, safe: str):
        return encode_typed_data(full_message=self.typed_data(chain_id, safe))

    def hash(self, chain_id: int, safe: str) -> str:
        m = self.signable(chain_id, safe)
        return "0x" + Web3.keccak(b"\x19" + m.version + m.header + m.body).hex().removeprefix("0x")

    def exec_args(self, signatures: bytes) -> tuple:
        m = self.message()
        return (
            m["to"],
            m["value"],
            m["data"],
            m["operation"],
            0,
            0,
            0,
            ZERO,
            ZERO,
            signatures,
        )

    @classmethod
    def of(cls, p: SafeProposal) -> "SafeTx":
        return cls(to=p.to, data=p.data, nonce=int(p.nonce), value=int(p.value), operation=p.operation)


def sign(tx: SafeTx, chain_id: int, safe: str, key: str) -> str:
    sig = Account.sign_message(tx.signable(chain_id, safe), private_key=key).signature
    return "0x" + bytes(sig).hex()


def recover(tx: SafeTx, chain_id: int, safe: str, signature: str) -> str:
    raw = bytes.fromhex(signature.removeprefix("0x"))
    if len(raw) != 65 or raw[64] not in (27, 28):
        raise GovernanceError("expected a 65-byte EIP-712 signature (v = 27 / 28)")
    return Account.recover_message(tx.signable(chain_id, safe), signature=raw)


def pack(signatures: dict[str, str]) -> bytes:
    """Safe wants the signatures concatenated in ascending owner order."""
    ordered = sorted(signatures.items(), key=lambda kv: int(kv[0], 16))
    return b"".join(bytes.fromhex(sig.removeprefix("0x")) for _, sig in ordered)


# ---- proposals --------------------------------------------------------------------------------


def require_safe(net: Network):
    if net.safe is None:
        raise GovernanceError(f"no governance Safe deployed on {net.name}")
    return net.governance()


def owners(net: Network) -> list[str]:
    return [Web3.to_checksum_address(o) for o in (require_safe(net)["owners"] or [])]


def next_nonce(s, net: Network, onchain_nonce: int) -> int:
    """After the Safe's current nonce and every pending proposal (they execute in nonce order)."""
    pending = s.scalar(
        select(func.max(SafeProposal.nonce)).where(
            SafeProposal.chain_id == net.chain_id, SafeProposal.status == "pending"
        )
    )
    return max(onchain_nonce, int(pending) + 1 if pending is not None else 0)


def create(s, net: Network, tx: SafeTx, kind: str, description: str) -> SafeProposal:
    gov = require_safe(net)
    p = SafeProposal(
        chain_id=net.chain_id,
        safe=Web3.to_checksum_address(gov["safe"]),
        safe_tx_hash=tx.hash(net.chain_id, gov["safe"]),
        to=Web3.to_checksum_address(tx.to),
        value=Decimal(tx.value),
        data=tx.data,
        operation=tx.operation,
        nonce=Decimal(tx.nonce),
        kind=kind,
        description=description[:2000],
        signatures={},
        status="pending",
    )
    s.add(p)
    s.flush()
    return p


def add_signature(net: Network, p: SafeProposal, signature: str) -> str:
    """Verify and store an owner's signature; returns the owner. Idempotent per owner."""
    if p.status != "pending":
        raise GovernanceError(f"proposal is {p.status}")
    owner = recover(SafeTx.of(p), p.chain_id, p.safe, signature)
    if owner not in owners(net):
        raise GovernanceError(f"{owner} is not an owner of the Safe")
    p.signatures = {**(p.signatures or {}), owner: signature}
    return owner


def proposal_dict(p: SafeProposal, net: Network | None = None) -> dict:
    gov = net.governance() if net else None
    return {
        "id": p.id,
        "chain_id": p.chain_id,
        "network": net.name if net else None,
        "safe": p.safe,
        "safe_tx_hash": p.safe_tx_hash,
        "kind": p.kind,
        "description": p.description,
        "tx": {"to": p.to, "value": str(p.value), "data": p.data, "operation": p.operation, "nonce": str(p.nonce)},
        "typed_data": _jsonable(SafeTx.of(p).typed_data(p.chain_id, p.safe)),
        "signers": sorted(p.signatures or {}),
        "signatures": dict(p.signatures or {}),
        "threshold": gov["threshold"] if gov else None,
        "ready": bool(gov) and len(p.signatures or {}) >= (gov["threshold"] or 99),
        "status": p.status,
        "executed_tx": p.executed_tx,
        "created_at": int(p.created_at.timestamp()) if p.created_at else None,
    }


def _jsonable(d):
    if isinstance(d, bytes):
        return "0x" + d.hex()
    if isinstance(d, dict):
        return {k: _jsonable(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_jsonable(v) for v in d]
    return d


def mark_executed(s, chain_id: int, safe: str, safe_tx_hash: str, tx: str) -> int:
    """ExecutionSuccess seen on chain: the proposal is done; others with the same nonce are replaced."""
    p = s.scalars(
        select(SafeProposal).where(SafeProposal.chain_id == chain_id, SafeProposal.safe_tx_hash == safe_tx_hash)
    ).first()
    if p is None:
        return 0
    p.status, p.executed_tx = "executed", tx
    for other in s.scalars(
        select(SafeProposal).where(
            SafeProposal.chain_id == chain_id,
            SafeProposal.safe == Web3.to_checksum_address(safe),
            SafeProposal.status == "pending",
            SafeProposal.nonce <= p.nonce,
        )
    ):
        other.status = "replaced"
    return 1
