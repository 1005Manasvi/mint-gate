"""Start a worker:  python -m jobs.worker analysis   |   python -m jobs.worker chain

SimpleWorker runs jobs in this process (no fork): the models stay loaded between jobs, it works
on Windows, and CUDA is never forked. Run one analysis worker per GPU (4 GB: exactly one here).

Crash recovery: a job whose worker dies is "abandoned"; once its heartbeat expires (job timeout
+ 60 s) the next maintenance pass (every 60 s) schedules its retry and the scheduler re-queues it,
so a crashed analysis is re-run within about 4 minutes. RQ's built-in scheduler is a child process that survives a hard-killed worker on Windows and
then blocks every other scheduler with its lock, so it runs here as a daemon thread instead.
"""

import argparse
import logging
import threading
import time

from rq import SimpleWorker
from rq.scheduler import RQScheduler

from jobs import queue as jq

log = logging.getLogger("mintgate.worker")


def start_scheduler_thread(queue_names, connection, interval=5) -> threading.Thread:
    """Move due scheduled jobs (retries with back-off) onto their queues; dies with this process."""
    sched = RQScheduler(queue_names, connection=connection, interval=interval)
    wanted = set(queue_names)

    def loop():
        while True:
            try:
                if sched.acquired_locks != wanted:  # another live worker may hold some locks
                    sched.acquire_locks()
                if sched.acquired_locks:
                    sched.heartbeat()
                    sched.enqueue_scheduled_jobs()
            except Exception:  # Redis hiccup: keep the thread alive
                log.exception("scheduler pass failed")
            time.sleep(interval)

    t = threading.Thread(target=loop, name="rq-scheduler", daemon=True)
    t.start()
    return t


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("queues", nargs="*", default=[jq.ANALYSIS, jq.CHAIN], help="queues to serve, in priority order")
    ap.add_argument("--burst", action="store_true", help="exit when the queues are empty")
    ap.add_argument("--preload", action="store_true", help="load the models before the first job")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.preload and jq.ANALYSIS in args.queues:
        from jobs import tasks

        tasks.pipeline()
    start_scheduler_thread(args.queues, jq.connection())
    queues = [jq.queue(q) for q in args.queues]
    # worker_ttl 75 s: an idle worker wakes every 60 s, so maintenance (abandoned jobs) really runs every 60 s
    worker = SimpleWorker(queues, connection=jq.connection(), maintenance_interval=60, worker_ttl=75)
    worker.work(burst=args.burst)


if __name__ == "__main__":
    main()
