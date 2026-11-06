import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest


def job_spec():
    from fabu.domain import JobSpec

    return JobSpec(
        source_url="https://youtu.be/L6imSiJLd9Q",
        title="剃须仙人的日常",
        preset_version_id="preset-fixture",
        target_ids=["fake-dy", "fake-bi"],
        publish_mode="local",
        scheduled_at_utc=None,
    )


def test_create_and_reopen_job(tmp_path):
    from fabu.store import JobStore

    path = tmp_path / "state.sqlite3"
    store = JobStore(path)
    job = store.create(job_spec())
    assert job.state == "NEW" and job.revision == 1 and job.content_revision == 1
    again = JobStore(path).get(job.id)
    assert again == job
    assert len(store.events(job.id)) == 1
    assert store.list_jobs() == [job]


def test_only_one_worker_claims_job(tmp_path):
    from fabu.store import JobStore

    path = tmp_path / "jobs.sqlite3"
    job = JobStore(path).create(job_spec())
    barrier = threading.Barrier(2)

    def claim(worker):
        barrier.wait()
        return JobStore(path).claim_next(worker)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["one", "two"]))
    assert sum(r is not None for r in results) == 1
    selected = next(r for r in results if r is not None)
    assert selected.id == job.id and selected.state == "INSPECTING"


def test_stale_revision_cannot_overwrite(tmp_path):
    from fabu.store import JobStore, StaleRevision

    store = JobStore(tmp_path / "db.sqlite3")
    job = store.create(job_spec())
    next_job = store.transition(job.id, job.revision, "INSPECTING", None)
    with pytest.raises(StaleRevision):
        store.transition(job.id, job.revision, "FAILED", "stale")
    assert store.get(job.id) == next_job
    assert next_job.content_revision == 1
    assert next_job.revision == 2


def test_invalid_transition_rejected(tmp_path):
    from fabu.store import InvalidTransition, JobStore

    store = JobStore(tmp_path / "db.sqlite3")
    job = store.create(job_spec())
    with pytest.raises(InvalidTransition):
        store.transition(job.id, job.revision, "READY", None)
    assert store.get(job.id).state == "NEW"


def test_crash_does_not_reset_submitting(tmp_path):
    from fabu.domain import TargetAttempt
    from fabu.store import JobStore

    path = tmp_path / "db.sqlite3"
    store = JobStore(path)
    job = store.create(job_spec())
    attempt = TargetAttempt(
        job_id=job.id,
        revision=job.content_revision,
        target_id="fake-dy",
        payload_digest="a" * 64,
        state="SUBMITTING",
    )
    store.record_attempt(attempt)
    assert store.get(job.id).content_frozen
    recovered = JobStore(path)
    ids = recovered.recover_interrupted()
    assert job.id in ids
    assert recovered.attempts(job.id)[0].state == "UNKNOWN"
    assert recovered.get(job.id).state == "PAUSED"
    assert recovered.get(job.id).content_frozen
    assert recovered.claim_next("new-worker") is None


def test_processing_crash_pauses_not_ready(tmp_path):
    from fabu.store import JobStore

    store = JobStore(tmp_path / "db.sqlite3")
    job = store.create(job_spec())
    for state in ("INSPECTING", "DOWNLOADING", "PROCESSING"):
        job = store.transition(job.id, job.revision, state, None)
    assert store.recover_interrupted() == [job.id]
    job = store.get(job.id)
    assert job.state == "PAUSED" and job.pause_reason == "interrupted_processing"
    assert job.output_manifest is None


def test_claim_does_not_steal_old_heartbeat(tmp_path):
    from fabu.store import JobStore

    store = JobStore(tmp_path / "db.sqlite3")
    store.create(job_spec())
    assert store.claim_next("sleeping-worker") is not None
    assert store.claim_next("second-worker") is None


def test_completed_target_states_survive_recovery(tmp_path):
    from fabu.domain import TargetAttempt
    from fabu.store import JobStore

    store = JobStore(tmp_path / "db.sqlite3")
    job = store.create(job_spec())
    for state in ["PROVIDER_ACCEPTED", "SUBMITTED", "SCHEDULED", "PUBLISHED", "UNKNOWN"]:
        store.record_attempt(
            TargetAttempt(
                job_id=job.id, revision=1, target_id="fake-dy", payload_digest="b" * 64, state=state
            )
        )
    store.recover_interrupted()
    assert [a.state for a in store.attempts(job.id)] == [
        "PROVIDER_ACCEPTED",
        "SUBMITTED",
        "SCHEDULED",
        "PUBLISHED",
        "UNKNOWN",
    ]


def test_revision_rejected_after_first_attempt(tmp_path):
    from fabu.domain import TargetAttempt
    from fabu.store import FrozenContent, JobStore

    store = JobStore(tmp_path / "db.sqlite3")
    job = store.create(job_spec())
    store.record_attempt(
        TargetAttempt(
            job_id=job.id,
            revision=1,
            target_id="fake-dy",
            payload_digest="a" * 64,
            state="SUBMITTING",
        )
    )
    with pytest.raises(FrozenContent):
        store.revise(job.id, store.get(job.id).revision, job_spec())


def test_revision_before_submission_clears_derived_outputs(tmp_path):
    from fabu.store import JobStore

    store = JobStore(tmp_path / "db.sqlite3")
    job = store.create(job_spec())
    revised = store.revise(job.id, job.revision, job_spec().model_copy(update={"title": "新版"}))
    assert revised.content_revision == 2 and revised.state == "NEW"
    assert revised.spec.title == "新版"
    assert revised.output_manifest is None and revised.plan_digest is None


def hold_lock(key, ready, stop):
    from fabu.worker import SingleInstance

    with SingleInstance(key):
        ready.set()
        stop.wait(10)


def test_sleep_does_not_spawn_second_owner(tmp_path):
    from fabu.worker import AlreadyRunning, SingleInstance

    key = str(tmp_path / "worker-lock")
    ctx = multiprocessing.get_context("spawn")
    ready, stop = ctx.Event(), ctx.Event()
    child = ctx.Process(target=hold_lock, args=(key, ready, stop))
    child.start()
    try:
        assert ready.wait(5)
        with pytest.raises(AlreadyRunning), SingleInstance(key):
            pass
        assert child.is_alive()
    finally:
        stop.set()
        child.join(5)
        if child.is_alive():
            child.terminate()
            child.join()
    with SingleInstance(key):
        pass


def test_worker_handler_runs_once(tmp_path):
    from fabu.store import JobStore
    from fabu.worker import Worker

    store = JobStore(tmp_path / "db.sqlite3")
    job = store.create(job_spec())
    called = []
    with Worker(store) as worker:
        assert worker.run_once(lambda snapshot: called.append(snapshot.id))
        assert not worker.run_once(lambda snapshot: called.append(snapshot.id))
    assert called == [job.id]


def test_worker_error_is_sanitized_and_pauses(tmp_path):
    from fabu.store import JobStore
    from fabu.worker import Worker

    store = JobStore(tmp_path / "db.sqlite3")
    job = store.create(job_spec())

    def fail(snapshot):
        raise RuntimeError("secret-api-key")

    with Worker(store) as worker:
        assert worker.run_once(fail)
    after = store.get(job.id)
    assert after.state == "PAUSED" and "secret-api-key" not in str(store.events(job.id))


def test_schedule_requires_aware_time():
    from fabu.domain import JobSpec

    with pytest.raises(ValueError):
        JobSpec(
            **{
                **job_spec().model_dump(),
                "publish_mode": "native_schedule",
                "scheduled_at_utc": datetime(2026, 12, 1),
            }
        )
    aware = JobSpec(
        **{
            **job_spec().model_dump(),
            "publish_mode": "native_schedule",
            "scheduled_at_utc": datetime(2026, 12, 1, tzinfo=UTC),
        }
    )
    assert aware.scheduled_at_utc.utcoffset().total_seconds() == 0


def test_sqlite_busy_retries_without_duplicate(tmp_path):
    import sqlite3
    import time
    from concurrent.futures import ThreadPoolExecutor

    from fabu.store import JobStore

    path = tmp_path / "locked.sqlite3"
    store = JobStore(path)
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(store.create, job_spec())
            time.sleep(0.15)
            assert not pending.done()
            blocker.rollback()
            job = pending.result(timeout=5)
    finally:
        blocker.close()
    assert len(store.list_jobs()) == 1
    assert store.get(job.id).state == "NEW"


def test_worker_mutex_covers_user_across_windows_sessions(tmp_path):
    from fabu.worker import SingleInstance

    assert SingleInstance(str(tmp_path)).name.startswith("Global\\FabuWorker-")


def test_active_revision_and_foreign_target_rejected(tmp_path):
    from fabu.domain import TargetAttempt
    from fabu.store import InvalidTransition, JobStore

    store = JobStore(tmp_path / "db.sqlite3")
    store.create(job_spec())
    job = store.claim_next("owner")
    with pytest.raises(InvalidTransition):
        store.revise(job.id, job.revision, job_spec())
    with pytest.raises(ValueError, match="scope"):
        store.record_attempt(
            TargetAttempt(
                job_id=job.id,
                revision=1,
                target_id="unexpected",
                payload_digest="a" * 64,
                state="SUBMITTING",
            )
        )
    assert not store.attempts(job.id)
