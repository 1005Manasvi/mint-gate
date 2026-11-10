"""The Safe transactions Mint-Gate governance needs, built from a network's deployment file.

Every action targets one of our own contracts with a plain CALL; nothing is delegatecalled.
"""

import json
from pathlib import Path

from web3 import Web3

import config
from chain.networks import Network
from governance.safe import GovernanceError

ROLES = ("VERIFIER_ROLE", "REVIEWER_ROLE", "PAUSER_ROLE", "COLLECTION_ROLE", "DEFAULT_ADMIN_ROLE")
POLICIES_FILE = config.BACKEND_DIR / "chain" / "policies.json"
ZERO32 = b"\x00" * 32


def _c(w3: Web3, contract):
    return w3.eth.contract(address=Web3.to_checksum_address(contract.address), abi=contract.abi)


def role_id(name: str) -> bytes:
    name = name.upper()
    if name not in ROLES:
        raise GovernanceError(f"unknown role {name}; one of {', '.join(ROLES)}")
    return ZERO32 if name == "DEFAULT_ADMIN_ROLE" else Web3.keccak(text=name)


def registry_call(net: Network, fn: str, *args) -> tuple[str, str]:
    return net.registry.address, _c(Web3(), net.registry).encode_abi(fn, args=list(args))


def schedule_policy(net: Network, policy: dict, salt: bytes | None = None) -> tuple[str, str, dict]:
    """timelock.schedule(policyRegistry.setPolicy(sha256, version, uri), delay). Returns (to, data, op)."""
    if net.timelock is None or net.policies is None:
        raise GovernanceError(f"no timelock / policy registry on {net.name}")
    w3 = Web3()
    inner = _c(w3, net.policies).encode_abi(
        "setPolicy", args=[bytes.fromhex(policy["sha256"]), policy["version"], policy["uri"]]
    )
    salt = salt or Web3.keccak(text=f"policy:{policy['sha256']}:{policy['version']}")
    delay = int(net.raw["governance"]["timelock"].get("minDelay", 3600))
    op = {"target": net.policies.address, "value": 0, "data": inner, "predecessor": "0x" + "00" * 32}
    op["salt"] = "0x" + bytes(salt).hex()
    data = _c(w3, net.timelock).encode_abi(
        "schedule", args=[net.policies.address, 0, bytes.fromhex(inner[2:]), ZERO32, bytes(salt), delay]
    )
    return net.timelock.address, data, op


def build(net: Network, kind: str, args: list[str]) -> tuple[str, str, str]:
    """(to, data, description) for a named action."""
    if kind == "accept-admin":
        to, data = registry_call(net, "acceptDefaultAdminTransfer")
        return to, data, f"Accept the MintGateRegistry admin role on {net.name} (two-step hand-over to the Safe)"
    if kind in ("grant-role", "revoke-role"):
        role, account = args[0], Web3.to_checksum_address(args[1])
        fn = "grantRole" if kind == "grant-role" else "revokeRole"
        to, data = registry_call(net, fn, role_id(role), account)
        return to, data, f"{fn}({role.upper()}, {account}) on the {net.name} registry"
    if kind in ("pause", "unpause"):
        to, data = registry_call(net, kind)
        return to, data, f"{kind.capitalize()} the {net.name} registry (submitVerdict + minting)"
    if kind == "withdraw":
        to, data = registry_call(net, "withdraw")
        return to, data, f"Withdraw forfeited dispute bonds (treasury credits) on {net.name}"
    if kind == "set-bond":
        wei = int(args[0])
        to, data = registry_call(net, "setDisputeBond", wei)
        return to, data, f"Set the dispute bond to {wei} wei on {net.name}"
    if kind == "set-policy":
        policy = load_policy(args[0] if args else None)
        to, data, op = schedule_policy(net, policy)
        return (
            to,
            data,
            f"Schedule policy {policy['version']} (sha256 {policy['sha256'][:16]}..., {policy['uri']}) in the "
            f"timelock; anyone executes it after the delay. Operation: {json.dumps(op)}",
        )
    raise GovernanceError(f"unknown action {kind}")


def load_policy(version: str | None) -> dict:
    """An entry of chain/policies.json (default: the newest)."""
    entries = json.loads(Path(POLICIES_FILE).read_text())["policies"]
    if version is None:
        return entries[-1]
    for e in entries:
        if e["version"] == version:
            return e
    raise GovernanceError(f"policy {version} not in {POLICIES_FILE.name}")
