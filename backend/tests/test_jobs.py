"""Job queue behaviour that the API tests do not cover: crash recovery and the scheduler thread."""

import io
import time
from datetime import timedelta

from rq import Queue

from jobs import tasks
from jobs.worker import start_scheduler_thread
from tests.conftest import ARTIST


def test_analysis_left_processing_by_a_dead_worker_is_rerun(server, client, fixture_bytes, run_jobs):
    resp = client.post(
        "/api/analyze",
        data={"image": (io.BytesIO(fixture_bytes("photo.png")), "p.png"), "artist": ARTIST},
        content_type="multipart/form-data",
    )
    jid = resp.get_json()["job_id"]
    with server.db.session() as s:  # the worker set "processing", then died
        server.repo.set_status(s, jid, "processing")
    assert client.get(f"/api/jobs/{jid}").get_json()["status"] == "processing"
    run_jobs()  # RQ's retry of the abandoned job
    assert client.get(f"/api/jobs/{jid}").get_json()["status"] == "done"
    assert tasks.analyse_submission(jid) == {"skipped": "PASS"}  # finished submissions are never re-run


def test_scheduler_thread_requeues_due_retries(fake_redis):
    q = Queue("chain", connection=fake_redis)
    q.enqueue_in(timedelta(seconds=1), "jobs.tasks.anchor_verdict", "0" * 32, "localtest")
    assert q.count == 0
    start_scheduler_thread(["chain"], fake_redis, interval=1)
    for _ in range(50):
        if q.count:
            break
        time.sleep(0.1)
    assert q.count == 1
