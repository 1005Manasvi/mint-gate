"""Redis + RQ queues.

analysis   GPU/CPU model work (analyse_submission). Workers keep the models loaded.
chain      transactions (anchor_verdict, resolve_review). Retried with back-off; no models.
"""

from redis import Redis
from rq import Queue, Retry

import config

ANALYSIS = "analysis"
CHAIN = "chain"
CHAIN_RETRY = Retry(max=5, interval=[10, 30, 60, 300, 900])
# A worker that dies mid-job (crash, OOM, restart) leaves the job "abandoned"; RQ re-runs it.
ANALYSIS_RETRY = Retry(max=2, interval=[5, 30])

_conn = None


def connection() -> Redis:
    global _conn
    if _conn is None:
        _conn = Redis.from_url(config.REDIS_URL, socket_connect_timeout=2, socket_timeout=10)
    return _conn


def set_connection(conn):
    """Tests use fakeredis."""
    global _conn
    _conn = conn


def queue(name: str) -> Queue:
    return Queue(name, connection=connection(), default_timeout=config.JOB_TIMEOUT_S)


def enqueue_analysis(submission_id: str):
    return queue(ANALYSIS).enqueue(
        "jobs.tasks.analyse_submission",
        submission_id,
        job_id=submission_id,
        retry=ANALYSIS_RETRY,
        result_ttl=24 * 3600,
        failure_ttl=7 * 24 * 3600,
    )


def enqueue_chain(task: str, *args):
    return queue(CHAIN).enqueue(
        f"jobs.tasks.{task}", *args, retry=CHAIN_RETRY, job_timeout=600, failure_ttl=30 * 24 * 3600
    )


def ping() -> bool:
    try:
        return bool(connection().ping())
    except Exception:
        return False
