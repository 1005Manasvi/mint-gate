"""Run the chain indexer (from backend/):

    python -m indexer                         follow CHAIN_NETWORKS forever (poll every 15 s)
    python -m indexer --once                  one pass, then exit
    python -m indexer --network sepolia --from-block 9800000 --once
                                              re-index from that block (deletes newer events first)

The cursor lives in Postgres (`indexer_cursors`), so restarts resume where they stopped.
"""

import argparse
import logging
import time

import config
from chain import networks
from indexer.core import Indexer

log = logging.getLogger("mintgate.indexer")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", action="append", help="network name (repeatable); default: CHAIN_NETWORKS")
    ap.add_argument("--from-block", type=int, help="re-index from this block (deletes newer events first)")
    ap.add_argument("--from-deploy", action="store_true", help="re-index everything from the deployment block")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=15.0)
    ap.add_argument("--chunk", type=int, default=2000, help="blocks per eth_getLogs call")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    indexers = [Indexer(networks.get(n), chunk=args.chunk) for n in (args.network or config.CHAIN_NETWORKS)]
    for ix in indexers:
        if args.from_deploy or args.from_block is not None:
            ix.reset(ix.net.deploy_block if args.from_deploy else args.from_block)
    while True:
        for ix in indexers:
            try:
                ix.run_once()
            except Exception as exc:  # flaky RPC: log and retry on the next poll
                if args.once:
                    raise
                log.warning("%s: %s", ix.net.name, exc)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
