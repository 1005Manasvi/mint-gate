"""On-chain NFT database (Phase 4).

  python -m ingest fetch  [--chain eth] [--only 0x..,0x..] [--threads 24] [--cap 6000] [--parallel 3]
  python -m ingest seed                        # local protected collection -> reference_items
  python -m ingest embed  [--follow] [--rebuild-nn]   # fingerprint pending items (GPU), keep 128 px thumbs
  python -m ingest sync   [--chain eth] [--interval 60] [--once]   # new mints -> pending items
  python -m ingest stats                       # per-collection counts + trait duplicates

All steps are resumable; long runs: log to backend/storage/logs/.
"""

import argparse

from ingest.collections import CAP


def main():
    ap = argparse.ArgumentParser(prog="python -m ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--chain", default="eth")
    f.add_argument("--only", default="")
    f.add_argument("--threads", type=int, default=24)
    f.add_argument("--cap", type=int, default=CAP)
    f.add_argument("--parallel", type=int, default=3)
    e = sub.add_parser("embed")
    e.add_argument("--follow", action="store_true")
    e.add_argument("--interval", type=int, default=60)
    e.add_argument("--rebuild-nn", action="store_true", help="exact nearest-neighbour pass over the whole index")
    y = sub.add_parser("sync")
    y.add_argument("--chain", default="eth")
    y.add_argument("--interval", type=int, default=60)
    y.add_argument("--once", action="store_true")
    sub.add_parser("stats")
    sub.add_parser("seed", help="import the local protected collection (index/files.json)")
    a = ap.parse_args()

    if a.cmd == "fetch":
        from ingest import fetch

        fetch.run(a.chain, [x for x in a.only.split(",") if x], a.threads, a.cap, a.parallel)
    elif a.cmd == "embed":
        from ingest import embed

        embed.run(follow=a.follow, interval=a.interval, rebuild=a.rebuild_nn)
    elif a.cmd == "sync":
        from ingest import sync

        sync.run(a.chain, interval=a.interval, once=a.once)
    elif a.cmd == "seed":
        from ingest import seed

        seed.run()
    else:
        from ingest import stats

        stats.run()


if __name__ == "__main__":
    main()
