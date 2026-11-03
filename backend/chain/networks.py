"""Configured networks: one `deployments/<network>.json` (deploy-v2.js output) per chain.

RPC URL, confirmations and signer key can be overridden per network with environment
variables named after the network: RPC_URL_BASESEPOLIA, CONFIRMATIONS_SEPOLIA,
VERIFIER_PRIVATE_KEY_LOCALHOST, ...
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import config


class UnknownNetwork(KeyError):
    pass


@dataclass(frozen=True)
class Contract:
    address: str
    abi: list
    deploy_block: int


@dataclass(frozen=True)
class Network:
    name: str
    chain_id: int
    rpc_url: str
    confirmations: int
    explorer: str
    registry: Contract
    nft: Contract
    raw: dict = field(repr=False, compare=False, default_factory=dict)

    @classmethod
    def from_file(cls, path: Path, rpc_url: str | None = None, confirmations: int | None = None):
        d = json.loads(Path(path).read_text())
        name = d["network"]
        if confirmations is None:
            confirmations = int(
                config.network_setting("CONFIRMATIONS", name, config.DEFAULT_CONFIRMATIONS.get(name, 5))
            )

        def contract(key):
            c = d[key]
            return Contract(address=c["address"], abi=c["abi"], deploy_block=int(c["deployBlock"]))

        return cls(
            name=name,
            chain_id=int(d["chainId"]),
            rpc_url=rpc_url or config.network_setting("RPC_URL", name, config.DEFAULT_RPC.get(name, "")),
            confirmations=confirmations,
            explorer=config.EXPLORERS.get(name, ""),
            registry=contract("registry"),
            nft=contract("nft"),
            raw=d,
        )

    @property
    def deploy_block(self) -> int:
        return min(self.registry.deploy_block, self.nft.deploy_block)

    def signer_key(self) -> str:
        """Verifier key for this chain. Local Hardhat chains use the public dev account."""
        dev = config.HARDHAT_DEV_KEY if self.chain_id == 31337 else ""
        return config.network_setting("VERIFIER_PRIVATE_KEY", self.name, "") or (dev or config.VERIFIER_PRIVATE_KEY)

    def public(self) -> dict:
        return {
            "network": self.name,
            "chain_id": self.chain_id,
            "registry": self.registry.address,
            "nft": self.nft.address,
            "explorer": self.explorer,
        }

    def public_rpc(self) -> str:
        return config.network_setting("PUBLIC_RPC", self.name, config.DEFAULT_RPC.get(self.name, ""))

    def browser(self) -> dict:
        """What the frontend needs to switch / add the chain in a wallet and to read it."""
        return {
            **self.public(),
            "label": config.CHAIN_LABELS.get(self.name, self.name),
            "rpc_url": self.public_rpc(),
            "confirmations": self.confirmations,
            "native_currency": config.NATIVE_CURRENCY,
        }


def available() -> dict[str, Network]:
    """Every deployment file found (v2 only), keyed by network name."""
    out = {}
    for p in sorted(Path(config.DEPLOYMENTS_DIR).glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except ValueError:
            continue
        if d.get("version") == 2:
            out[d["network"]] = Network.from_file(p)
    return out


def get(name: str) -> Network:
    nets = available()
    if name not in nets:
        raise UnknownNetwork(name)
    return nets[name]


def by_chain_id(chain_id: int) -> Network | None:
    return next((n for n in available().values() if n.chain_id == chain_id), None)


def resolve(ref: str) -> Network:
    """A network by name or decimal chain id (URLs use either)."""
    if ref.isdigit() and (net := by_chain_id(int(ref))):
        return net
    return get(ref)
