"""Governance CLI (run from backend/).

    python -m governance policies [--pin]            hash (+ pin to IPFS) the policy files -> chain/policies.json
    python -m governance propose <net> <action> [args] [--sign]
          actions: accept-admin | grant-role <ROLE> <addr> | revoke-role <ROLE> <addr> | pause | unpause
                   | withdraw | set-bond <wei> | set-policy [<version>]
    python -m governance sign <net> <safeTxHash>      sign as an owner (key: SAFE_OWNER_KEY, else the network's signer key)
    python -m governance exec <net> <safeTxHash>      execTransaction once the threshold is reached (anyone)
    python -m governance timelock-exec <net> <safeTxHash>   execute a scheduled policy change after the delay
    python -m governance list [<net>]

Owners who do not use this CLI sign on the web page /governance with their own wallet.
Keys are read from the environment and never printed.
"""

import argparse
import hashlib
import json
import os
import sys

from sqlalchemy import select
from web3 import Web3

import config
import db
from chain import ipfs, networks
from db import repo
from db.models import SafeProposal
from governance import actions, safe
from governance.safe import GovernanceError, SafeTx

POLICY_FILES = ("policy.permissive.json", "policy.json")  # the last one is current() on chain


def cmd_policies(a):
    out = []
    for name in POLICY_FILES:
        raw = (config.BACKEND_DIR / name).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        version = json.loads(raw)["version"]
        uri = f"{config.PUBLIC_BASE_URL}/api/policy/{digest}.json"
        if a.pin:
            if not ipfs.enabled():
                sys.exit("PINATA_JWT is not set: cannot pin")
            uri = "ipfs://" + ipfs.pin_file(raw, f"mintgate-policy-{version}.json", "application/json")
        out.append({"version": version, "file": name, "sha256": digest, "uri": uri})
        print(f"{version:20s} {digest}  {uri}")
    actions.POLICIES_FILE.write_text(json.dumps({"policies": out}, indent=2) + "\n")
    print(f"written {actions.POLICIES_FILE.relative_to(config.BACKEND_DIR)}")


def _w3(net):
    w3 = Web3(Web3.HTTPProvider(net.rpc_url, request_kwargs={"timeout": 30}))
    if w3.eth.chain_id != net.chain_id:
        sys.exit(f"{net.name}: RPC chain id {w3.eth.chain_id} != {net.chain_id}")
    return w3


def _key(net):
    key = os.environ.get("SAFE_OWNER_KEY") or net.signer_key()
    if not key:
        sys.exit("no key: set SAFE_OWNER_KEY (or the network's signer key)")
    return key


def _safe_contract(w3, net):
    return w3.eth.contract(address=Web3.to_checksum_address(net.safe.address), abi=net.safe.abi)


def _send(w3, net, fn, key):
    acct = w3.eth.account.from_key(key)
    tx = {"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address, "pending"), "chainId": net.chain_id}
    if net.chain_id not in (31337, 1337):
        tip = Web3.to_wei(config.network_setting("PRIORITY_FEE_GWEI", net.name, "0.01"), "gwei")
        base = w3.eth.get_block("latest")["baseFeePerGas"]
        tx |= {"maxPriorityFeePerGas": tip, "maxFeePerGas": 2 * base + tip}
    built = fn.build_transaction(tx)
    h = w3.eth.send_raw_transaction(acct.sign_transaction(built).raw_transaction)
    rc = w3.eth.wait_for_transaction_receipt(h, timeout=300)
    print(f"tx {'0x' + bytes(h).hex()}: status {rc.status}, gas {rc.gasUsed}, block {rc.blockNumber}")
    if rc.status != 1:
        sys.exit("reverted")
    return "0x" + bytes(h).hex()


def _sign(s, net, p: SafeProposal, key):
    sig = safe.sign(SafeTx.of(p), p.chain_id, p.safe, key)
    owner = safe.add_signature(net, p, sig)
    repo.audit(s, "governance-cli", "safe.signed", p.safe_tx_hash, owner=owner, chain_id=p.chain_id)
    print(f"signed by {owner} ({len(p.signatures)}/{net.governance()['threshold']})")


def _proposal(s, net, h) -> SafeProposal:
    p = s.scalars(
        select(SafeProposal).where(SafeProposal.chain_id == net.chain_id, SafeProposal.safe_tx_hash == h.lower())
    ).first()
    if p is None:
        sys.exit(f"no proposal {h} on {net.name}")
    return p


def cmd_propose(a):
    net = networks.get(a.network)
    to, data, description = actions.build(net, a.action, a.args)
    w3 = _w3(net)
    onchain = _safe_contract(w3, net).functions.nonce().call()
    with db.session() as s:
        tx = SafeTx(to=to, data=data, nonce=safe.next_nonce(s, net, onchain))
        p = safe.create(s, net, tx, a.action, description)
        repo.audit(s, "governance-cli", "safe.proposed", p.safe_tx_hash, chain_id=net.chain_id, kind=a.action)
        # the Safe's own hash must equal ours (else no wallet signature would ever verify)
        onchain_hash = _safe_contract(w3, net).functions.getTransactionHash(*tx.exec_args(b"")[:9], tx.nonce).call()
        if "0x" + bytes(onchain_hash).hex() != p.safe_tx_hash:
            raise SystemExit("SafeTx hash mismatch with the Safe contract: not stored")
        print(f"proposal {p.safe_tx_hash} (nonce {tx.nonce}): {description}")
        if a.sign:
            _sign(s, net, p, _key(net))


def cmd_sign(a):
    net = networks.get(a.network)
    with db.session() as s:
        _sign(s, net, _proposal(s, net, a.hash), _key(net))


def cmd_exec(a):
    net = networks.get(a.network)
    w3 = _w3(net)
    with db.session() as s:
        p = _proposal(s, net, a.hash)
        need = net.governance()["threshold"]
        if len(p.signatures) < need:
            sys.exit(f"{len(p.signatures)} of {need} signatures")
        args = SafeTx.of(p).exec_args(safe.pack(p.signatures))
    tx = _send(w3, net, _safe_contract(w3, net).functions.execTransaction(*args), _key(net))
    print(f"executed; the indexer marks the proposal from ExecutionSuccess ({tx})")


def cmd_timelock_exec(a):
    net = networks.get(a.network)
    w3 = _w3(net)
    with db.session() as s:
        p = _proposal(s, net, a.hash)
        data = p.data
    tl = w3.eth.contract(address=net.timelock.address, abi=net.timelock.abi)
    fn, args = tl.decode_function_input(data)
    if fn.fn_name != "schedule":
        sys.exit("not a timelock schedule proposal")
    op = (args["target"], args["value"], args["data"], args["predecessor"], args["salt"])
    opid = tl.functions.hashOperation(*op).call()
    if not tl.functions.isOperationReady(opid).call():
        ts = tl.functions.getTimestamp(opid).call()
        sys.exit(f"operation not ready (timestamp {ts}; 0 = not scheduled, 1 = done)")
    _send(w3, net, tl.functions.execute(*op), _key(net))


def cmd_list(a):
    nets = networks.available()
    with db.session() as s:
        q = select(SafeProposal).order_by(SafeProposal.id)
        for p in s.scalars(q):
            net = next((n for n in nets.values() if n.chain_id == p.chain_id), None)
            if a.network and (net is None or net.name != a.network):
                continue
            d = safe.proposal_dict(p, net)
            print(
                f"{d['network']:12s} {p.safe_tx_hash} nonce {p.nonce} {p.status:9s} "
                f"{len(d['signers'])}/{d['threshold']} {p.kind}: {p.description[:80]}"
            )


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m governance", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("policies")
    p.add_argument("--pin", action="store_true")
    p = sub.add_parser("propose")
    p.add_argument("network")
    p.add_argument("action")
    p.add_argument("args", nargs="*")
    p.add_argument("--sign", action="store_true")
    for name in ("sign", "exec", "timelock-exec"):
        p = sub.add_parser(name)
        p.add_argument("network")
        p.add_argument("hash")
    p = sub.add_parser("list")
    p.add_argument("network", nargs="?")
    a = ap.parse_args(argv)
    try:
        {
            "policies": cmd_policies,
            "propose": cmd_propose,
            "sign": cmd_sign,
            "exec": cmd_exec,
            "timelock-exec": cmd_timelock_exec,
            "list": cmd_list,
        }[a.cmd](a)
    except GovernanceError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
