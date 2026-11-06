"""One Windows-owned worker serializes downloads, previews and full renders."""

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fabu.domain import JobSnapshot
from fabu.media.preview import build_preview, normalize_windows
from fabu.media.service import MediaService, load_job_plan
from fabu.store import JobStore
from fabu.worker import AlreadyRunning, Worker


class MediaController:
    def __init__(
        self,
        store: JobStore,
        service_factory: Callable[[threading.Event], MediaService],
        preview_root: Path,
        *,
        startup_cleanup: Callable[[], None] | None = None,
    ):
        self.startup_cleanup = startup_cleanup
        self.publication_queue = None
        self.store, self.service_factory, self.preview_root = store, service_factory, preview_root
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._cancel = threading.Event()
        self._thread = None
        self._state = {
            "running": False,
            "job_id": None,
            "operation_id": None,
            "kind": None,
            "percent": None,
            "error_code": None,
        }

    def status(self):
        with self._lock:
            return dict(self._state)

    def _set(self, **values):
        with self._lock:
            self._state.update(values)

    def wake(self):
        self._wake.set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="fabu-media-worker", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def stop(self):
        with self._lock:
            self._stop.set()
            self._cancel.set()
        self.wake()
        if self._thread:
            self._thread.join(30)
            if self._thread.is_alive():
                self._set(error_code="shutdown_pending")

    def cancel_job(self, job_id):
        with self._lock:
            if self._state["job_id"] == job_id:
                self._cancel.set()
        self.wake()

    def job_busy(self, job_id):
        if self.status()["job_id"] == job_id:
            return True
        with self.store.connection() as db:
            return (
                db.execute(
                    "SELECT 1 FROM media_operations WHERE job_id=? "
                    "AND state IN ('QUEUED','RUNNING') UNION ALL "
                    "SELECT 1 FROM publication_commands WHERE job_id=? "
                    "AND state IN ('QUEUED','RUNNING')",
                    (job_id, job_id),
                ).fetchone()
                is not None
            )

    def enqueue_preview(self, job: JobSnapshot, windows: list[tuple[int, int]] | None):
        if not self.status()["running"]:
            raise ValueError("Worker is not running")
        plan = load_job_plan(self.store, job.id)
        if windows is not None:
            normalize_windows(plan, windows)
        identifier, now = str(uuid4()), datetime.now(UTC).isoformat()
        with self.store.transaction() as db:
            current = self.store._snapshot(
                db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
            )
            if (
                current.revision != job.revision
                or current.state not in {"PAUSED", "READY"}
                or current.content_frozen
                or current.owner_worker_id
            ):
                raise ValueError("Task changed before preview enqueue")
            if db.execute(
                "SELECT 1 FROM media_operations WHERE job_id=? AND state IN ('QUEUED','RUNNING') "
                "UNION ALL SELECT 1 FROM publication_commands WHERE job_id=? "
                "AND state IN ('QUEUED','RUNNING')",
                (job.id, job.id),
            ).fetchone():
                raise ValueError("Task already has a preview queued")
            if (
                db.execute("SELECT COUNT(*) FROM media_operations WHERE state='QUEUED'").fetchone()[
                    0
                ]
                >= 64
            ):
                raise ValueError("Preview queue is full")
            db.execute(
                "INSERT INTO media_operations VALUES (?,?,?,?,'QUEUED',NULL,NULL,?,?)",
                (
                    identifier,
                    job.id,
                    job.content_revision,
                    json.dumps({"windows": windows, "expected_revision": job.revision}),
                    now,
                    now,
                ),
            )
        self.wake()
        return {
            "id": identifier,
            "job_id": job.id,
            "state": "QUEUED",
            "result": None,
            "error_code": None,
        }

    def _claim_operation(self):
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM media_operations WHERE state='QUEUED' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE media_operations SET state='RUNNING',updated_at=? WHERE id=?",
                    (datetime.now(UTC).isoformat(), row["id"]),
                )
                return dict(row)
        return None

    def _operation_result(self, identifier, state, *, result=None, error=None):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE media_operations SET state=?,result_json=?,error_code=?,updated_at=? "
                "WHERE id=?",
                (
                    state,
                    json.dumps(result) if result else None,
                    error,
                    datetime.now(UTC).isoformat(),
                    identifier,
                ),
            )

    def _preview(self, operation, service):
        self._set(
            job_id=operation["job_id"], operation_id=operation["id"], kind="preview", percent=None
        )
        try:
            job = self.store.get(operation["job_id"])
            request = json.loads(operation["request_json"])
            if (
                job.revision != request["expected_revision"]
                or job.content_revision != operation["content_revision"]
                or job.state not in {"PAUSED", "READY"}
                or job.content_frozen
            ):
                raise ValueError("Task changed since preview request")
            manifest = build_preview(
                service.get_plan(job.id),
                request["windows"],
                workspace=self.preview_root,
                tools=service.tools,
                cancel=self._cancel,
            )
            if self._cancel.is_set() or self.store.get(job.id).revision != job.revision:
                raise ValueError("Task changed or preview was canceled")
            service.approvals.record_preview(manifest, job.spec.preset_version_id)
            self._operation_result(operation["id"], "SUCCEEDED", result={"preview_id": manifest.id})
        except Exception:
            self._operation_result(
                operation["id"],
                "FAILED",
                error="preview_canceled" if self._cancel.is_set() else "preview_failed",
            )
        finally:
            self._set(job_id=None, operation_id=None, kind=None, percent=None)

    def _process(self, service, job):
        self._set(job_id=job.id, operation_id=None, kind="processing", percent=None)
        try:
            service.process_job(
                job, cancel=self._cancel, on_progress=lambda value: self._set(percent=value)
            )
        finally:
            self._set(job_id=None, operation_id=None, kind=None, percent=None)

    def _run(self):
        try:
            with Worker(self.store) as worker:
                if self.startup_cleanup:
                    self.startup_cleanup()
                # A restarted process never pretends an interrupted preview was completed.
                with self.store.transaction() as db:
                    db.execute(
                        "UPDATE media_operations SET state='FAILED',"
                        "error_code='preview_interrupted',updated_at=? "
                        "WHERE state IN ('QUEUED','RUNNING')",
                        (datetime.now(UTC).isoformat(),),
                    )
                self._set(running=True, error_code=None)
                self._ready.set()
                while not self._stop.is_set():
                    with self._lock:
                        if self._stop.is_set():
                            break
                        self._cancel = threading.Event()
                    if self.publication_queue and self.publication_queue.process_next(
                        worker, self._cancel, self._set
                    ):
                        continue
                    try:
                        service = self.service_factory(self._cancel)
                    except (ValueError, OSError):
                        self._set(error_code="tools_not_configured")
                        self._wake.wait(1)
                        self._wake.clear()
                        continue
                    self._set(error_code=None)
                    operation = self._claim_operation()
                    if operation:
                        self._preview(operation, service)
                    elif not worker.run_once(lambda job, bound=service: self._process(bound, job)):
                        self._wake.wait(0.5)
                        self._wake.clear()
        except AlreadyRunning:
            self._set(error_code="worker_already_running")
        except Exception:
            self._set(error_code="worker_failed")
        finally:
            self._set(running=False, job_id=None, operation_id=None, kind=None, percent=None)
            self._ready.set()
