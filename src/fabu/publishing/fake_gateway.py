"""Deterministic local-only publisher. There is no real transport to fall back to."""

from collections import deque
from datetime import timedelta
from threading import Event

from fabu.publishing.capabilities import CapabilityReport, ScheduleWindow
from fabu.publishing.gateway import (
    ReconcileResult,
    SubmissionReceipt,
    UploadReceipt,
    assemble_prepared,
    resource_metadata,
    verify_prepared,
)


class FakePublisher:
    execution_scope = "simulation"

    def __init__(
        self, authority, *, outcomes=None, delay_seconds: float = 0, cancel: Event | None = None
    ):
        if not 0 <= delay_seconds <= 30:
            raise ValueError("Simulation delay must be between zero and thirty seconds")
        self.delay_seconds = delay_seconds
        self.cancel = cancel or Event()
        self.authority = authority
        self.outcomes = deque(outcomes or [])
        self.calls = []
        self.receipts = {}
        self.journal = None

    def preflight(self, snapshot):
        return CapabilityReport(
            checked_at=self.authority.clock(),
            submission_strategy="per_target",
            schedule_windows={
                t.account.account_id: ScheduleWindow(
                    earliest_utc=self.authority.clock(),
                    latest_utc=self.authority.clock() + timedelta(days=30),
                )
                for t in snapshot.targets
            },
            account_status="SUPPORTED",
            upload="SUPPORTED",
            immediate="SUPPORTED",
            native_schedule="SUPPORTED",
            quota="SUPPORTED",
            file_limits="SUPPORTED",
            hevc="SUPPORTED",
            source_declarations="SUPPORTED",
            evidence_refs=["simulation-only"],
            mode_evidence={
                t.account.account_id: ["immediate", "native_schedule"] for t in snapshot.targets
            },
        )

    def prepare(self, snapshot, permit):
        self.authority.validate(permit, snapshot, real=False)
        resources = []
        for kind, digest in [("video", snapshot.output.sha256), ("cover", snapshot.cover.sha256)]:
            cached = self.journal.get(snapshot.digest, kind) if self.journal else None
            if cached is not None:
                resources.append(cached)
                continue
            self.authority.consume(permit, snapshot, "upload")
            if self.journal:
                self.journal.begin(snapshot.digest, kind)
            receipt = UploadReceipt(
                resource=kind,
                sha256=digest,
                key=f"cloud-publish/simulation/{digest}",
                metadata=resource_metadata(snapshot, kind),
                raw={"simulation": True},
            )
            if self.journal:
                self.journal.complete(snapshot.digest, receipt)
            resources.append(receipt)
        self.calls.append(("prepare", snapshot.digest))
        return assemble_prepared(snapshot, tuple(resources), "simulation-client", self.authority)

    def submit(self, prepared, permit, target_id):
        verify_prepared(prepared, self.authority)
        self.authority.validate(permit, prepared.snapshot, real=False)
        self.authority.consume(permit, prepared.snapshot, "submit", target_id)
        self.calls.append(("submit", target_id))
        default_state = "SCHEDULED" if prepared.snapshot.mode == "native_schedule" else "SUBMITTED"
        state = self.outcomes.popleft() if self.outcomes else default_state
        if self.cancel.wait(self.delay_seconds):
            state = "UNKNOWN"
        receipt = SubmissionReceipt(
            target_id=target_id,
            state=state,
            provider_task_id="simulation-" + target_id,
            platform_content_id="simulated-content-" + target_id
            if state in {"SUBMITTED", "SCHEDULED", "PUBLISHED"}
            else None,
            scheduled_at_utc=prepared.snapshot.scheduled_at_utc if state == "SCHEDULED" else None,
            actual_channel="simulation",
            reason="simulation_only",
            snapshot_digest=prepared.snapshot.digest,
            video_sha256=prepared.snapshot.output.sha256,
            cover_sha256=prepared.snapshot.cover.sha256,
            platform=next(
                t.account.platform
                for t in prepared.snapshot.targets
                if t.account.account_id == target_id
            ),
            evidence_origin="simulation",
            definitely_not_submitted=state == "FAILED",
        )
        self.receipts[target_id] = receipt
        return receipt

    def reconcile(self, attempt):
        self.calls.append(("reconcile", attempt.target_id))
        receipt = self.receipts.get(attempt.target_id)
        if not receipt or receipt.state == "PROVIDER_ACCEPTED":
            return ReconcileResult(
                target_id=attempt.target_id, state="UNKNOWN", reason="simulation_unknown"
            )
        return ReconcileResult(**receipt.model_dump(exclude={"actual_channel"}))
