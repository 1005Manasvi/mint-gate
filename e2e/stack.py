"""The whole Mint-Gate stack for the browser E2E test, in one process (started by Playwright).

    venv python e2e/stack.py        (needs: Postgres + Redis from docker compose, a Hardhat node on
                                     E2E_RPC_PORT, compiled contracts; see e2e/playwright.config.mjs)

* deploys MintGateRegistry + MintGateNFTV2 on the local Hardhat node (account #0 = verifier/admin)
  and turns on 1-second interval mining, so the UI really waits for confirmations;
* database `mintgate_e2e` (created if missing, schema rebuilt by Alembic), Redis db 15 (flushed);
* API server (thread), chain indexer (thread), RQ worker with the *fake* models (main thread:
  RQ installs signal handlers). Nothing touches the dev database, storage or deployments.
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
RPC_PORT = int(os.environ.get("E2E_RPC_PORT", "8547"))
API_PORT = int(os.environ.get("E2E_API_PORT", "5057"))
RPC = f"http://127.0.0.1:{RPC_PORT}"
PG = os.environ.get("E2E_PG", "postgresql+psycopg://mintgate:mintgate@127.0.0.1:5432")
TMP = Path(tempfile.mkdtemp(prefix="mintgate-e2e-"))

os.environ.update(
    {
        "APP_ENV": "test",
        "STORAGE_DIR": str(TMP),
        "DEPLOYMENTS_DIR": str(TMP / "deployments"),
        "DATABASE_URL": f"{PG}/mintgate_e2e",
        "REDIS_URL": os.environ.get("E2E_REDIS", "redis://127.0.0.1:6379/15"),
        "CHAIN_NETWORKS": "localhost",
        "DEFAULT_NETWORK": "localhost",
        "RPC_URL_LOCALHOST": RPC,
        "PUBLIC_RPC_LOCALHOST": RPC,
        "CONFIRMATIONS_LOCALHOST": "2",
        "PINATA_JWT": "",
        "PUBLIC_BASE_URL": f"http://127.0.0.1:{API_PORT}",
        "REVIEWER_KEY": "e2e-reviewer-key-0123456789",
        "VERIFIER_PRIVATE_KEY": "",
    }
)
os.environ.pop("RATELIMIT_STORAGE_URI", None)
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from web3 import Web3  # noqa: E402

BOND = 10**15


def wait_for_node(w3, seconds=120):
    for _ in range(seconds * 2):
        if w3.is_connected():
            return
        time.sleep(0.5)
    raise SystemExit(f"no Hardhat node at {RPC}")


def deploy(w3) -> dict:
    art = BACKEND.parent / "Blockchain" / "artifacts" / "contracts" / "v2"
    reg_art, nft_art = (
        json.loads((art / f"{n}.sol" / f"{n}.json").read_text()) for n in ("MintGateRegistry", "MintGateNFTV2")
    )
    admin = w3.eth.accounts[0]

    def create(a, *args):
        tx = w3.eth.contract(abi=a["abi"], bytecode=a["bytecode"]).constructor(*args).transact({"from": admin})
        rc = w3.eth.wait_for_transaction_receipt(tx)
        return w3.eth.contract(address=rc.contractAddress, abi=a["abi"]), rc.blockNumber

    reg, reg_block = create(reg_art, 3600, admin, admin, admin, admin, admin, BOND)
    nft, nft_block = create(nft_art, reg.address)
    w3.eth.wait_for_transaction_receipt(
        reg.functions.grantRole(reg.functions.COLLECTION_ROLE().call(), nft.address).transact({"from": admin})
    )
    dep = {
        "version": 2,
        "network": "localhost",
        "chainId": w3.eth.chain_id,
        "registry": {"address": reg.address, "deployBlock": reg_block, "abi": reg_art["abi"]},
        "nft": {"address": nft.address, "deployBlock": nft_block, "abi": nft_art["abi"]},
    }
    d = Path(os.environ["DEPLOYMENTS_DIR"])
    d.mkdir(parents=True)
    (d / "localhost.json").write_text(json.dumps(dep))
    return dep


def fresh_database():
    import psycopg
    from alembic import command
    from alembic.config import Config

    with psycopg.connect(PG.replace("postgresql+psycopg", "postgresql") + "/postgres", autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = 'mintgate_e2e'").fetchone():
            c.execute("CREATE DATABASE mintgate_e2e")
    cfg = Config(str(BACKEND / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def serve_api():
    from werkzeug.serving import make_server

    import server

    make_server("127.0.0.1", API_PORT, server.app, threaded=True).serve_forever()


def run_indexer():
    from chain import networks
    from indexer.core import Indexer

    ix = Indexer(networks.get("localhost"))
    while True:
        try:
            ix.run_once()
        except Exception as exc:  # noqa: BLE001 - keep indexing; the test fails on its own if this persists
            print("indexer:", exc, flush=True)
        time.sleep(1)


def main():
    w3 = Web3(Web3.HTTPProvider(RPC))
    wait_for_node(w3)
    dep = deploy(w3)
    w3.provider.make_request("evm_setIntervalMining", [1000])
    fresh_database()

    from rq import SimpleWorker

    from jobs import queue as jq
    from jobs import tasks
    from tests.fakes import make_pipeline

    jq.connection().flushdb()
    tasks.set_pipeline(make_pipeline())
    threading.Thread(target=serve_api, daemon=True).start()
    threading.Thread(target=run_indexer, daemon=True).start()
    print(f"e2e stack ready: http://127.0.0.1:{API_PORT}  nft {dep['nft']['address']}", flush=True)
    SimpleWorker([jq.queue(jq.ANALYSIS), jq.queue(jq.CHAIN)], connection=jq.connection()).work()


if __name__ == "__main__":
    main()
