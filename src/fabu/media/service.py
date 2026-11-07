"""Preset-driven local rendering with immutable checkpoints and optional quality previews."""

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import uuid4

from fabu.assets import AssetStore, assert_no_reparse, sha256_file
from fabu.domain import JobSnapshot
from fabu.media.approvals import PreviewApprovalStore
from fabu.media.compiler import bind_tool_identity, compile_plan
from fabu.media.models import (
    InputBundle,
    MediaPlan,
    OutputManifest,
    PlanInvalid,
    compatibility_version,
    verify_plan_digest,
)
from fabu.media.probe import probe_media
from fabu.media.renderer import render
from fabu.media.runtime import MediaTools
from fabu.media.source import (
    SourceAuthenticationRequired,
    SourceBrowserSessionRequired,
    SourceManifest,
    parse_youtube_url,
)
from fabu.media.validator import promote_output, validate_metadata, validate_output
from fabu.presets import PresetStore
from fabu.store import JobStore, StaleRevision


class ProcessingPaused(ValueError):
    pass


class MediaService:
    def __init__(
        self,
        store: JobStore,
        presets: PresetStore,
        assets: AssetStore,
        approvals: PreviewApprovalStore,
        tools: MediaTools,
        workspace: Path,
        source_loader: Callable[[JobSnapshot], SourceManifest],
    ):
        if not workspace.is_absolute():
            raise ValueError("Media workspace must be absolute")
        assert_no_reparse(workspace)
        self.store, self.presets, self.assets = store, presets, assets
        self.approvals, self.tools, self.workspace = approvals, tools, workspace
        self.source_loader = source_loader

    def _owned(self, db, job):
        current = self.store._snapshot(
            db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
        )
        if (
            current.revision != job.revision
            or current.content_revision != job.content_revision
            or not job.owner_worker_id
            or current.owner_worker_id != job.owner_worker_id
            or current.content_frozen
        ):
            raise StaleRevision("Media processing no longer owns this task revision")
        return current

    def _source_checkpoint(self, job, source):
        with self.store.transaction() as db:
            self._owned(db, job)
            db.execute(
                "UPDATE jobs SET source_json=?,revision=revision+1,updated_at=? WHERE id=?",
                (source.model_dump_json(), datetime.now(UTC).isoformat(), job.id),
            )
            for asset in [source.source, source.thumbnail]:
                db.execute(
                    "INSERT OR IGNORE INTO asset_refs VALUES (?,?,?)",
                    (asset.id, "job_revision", f"{job.id}:{job.content_revision}"),
                )
        return self.store.get(job.id)

    def _plan_checkpoint(self, job, source, plan):
        now = datetime.now(UTC).isoformat()
        with self.store.transaction() as db:
            self._owned(db, job)
            previous = db.execute(
                "SELECT plan_json FROM processing_snapshots WHERE job_id=? AND content_revision=?",
                (job.id, job.content_revision),
            ).fetchone()
            if previous and MediaPlan.model_validate_json(previous[0]) != plan:
                raise ProcessingPaused("processing_snapshot_changed")
            db.execute(
                "INSERT OR IGNORE INTO processing_snapshots VALUES (?,?,?,?,?,?)",
                (
                    job.id,
                    job.content_revision,
                    job.spec.preset_version_id,
                    source.model_dump_json(),
                    plan.model_dump_json(),
                    now,
                ),
            )
            db.execute(
                "UPDATE jobs SET plan_digest=?,revision=revision+1,updated_at=? WHERE id=?",
                (plan.digest, now, job.id),
            )
            for asset in plan.asset_refs.values():
                db.execute(
                    "INSERT OR IGNORE INTO asset_refs VALUES (?,?,?)",
                    (asset.id, "job_revision", f"{job.id}:{job.content_revision}"),
                )
        return self.store.get(job.id)

    def get_plan(self, job_id: str) -> MediaPlan:
        return load_job_plan(self.store, job_id)

    def _finish(self, job, output):
        with self.store.transaction() as db:
            current = self._owned(db, job)
            if current.state != "PROCESSING" or current.plan_digest != output.plan_digest:
                raise StaleRevision("Task is not processing this media plan")
            db.execute(
                "UPDATE jobs SET output_json=?,state='READY',owner_worker_id=NULL,"
                "revision=revision+1,updated_at=? "
                "WHERE id=?",
                (output.model_dump_json(), datetime.now(UTC).isoformat(), job.id),
            )
            self.store._event(db, job.id, {"event": "media_verified", "status": "READY"})

    def _cache(self, directory, plan):
        manifest = directory / "output.json"
        if not manifest.is_file():
            return None
        try:
            saved = OutputManifest.model_validate_json(manifest.read_text(encoding="utf-8"))
            if saved.path.parent != directory or saved.plan_digest != plan.digest:
                return None
            assert_no_reparse(saved.path)
            if (
                not saved.path.is_file()
                or saved.path.stat().st_size != saved.size_bytes
                or sha256_file(saved.path) != saved.sha256
            ):
                return None
            validate_metadata(saved.media_info, plan)
            return saved
        except (ValueError, OSError):
            return None

    def process_job(
        self,
        job: JobSnapshot,
        *,
        cancel: Event | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> OutputManifest:
        if job.state != "INSPECTING" or not job.owner_worker_id or job.content_frozen:
            raise ValueError("A worker must claim a fresh, unfrozen media task")
        cancel = cancel or Event()
        stage = "source_unavailable"
        try:
            preset = self.presets.get_version(job.spec.preset_version_id)
            job = self.store.transition(job.id, job.revision, "DOWNLOADING", None)
            source = (
                SourceManifest.model_validate(job.source_manifest)
                if job.source_manifest
                else self.source_loader(job)
            )
            if source.video_id != parse_youtube_url(job.spec.source_url):
                raise PlanInvalid("Downloaded source does not match the requested video")
            for ref in [source.source, source.thumbnail]:
                if not self.assets.verify(ref.id) or self.assets.get(ref.id) != ref:
                    raise PlanInvalid(
                        "Downloaded source or original thumbnail failed integrity verification"
                    )
            job = self._source_checkpoint(job, source)
            stage = "pipeline_invalid"
            used = {a for step in preset.steps if step.enabled for a in step.asset_ids}
            refs = {a: self.assets.get(a) for a in used}
            info = probe_media(
                source.source.local_path, lambda args: self.tools.probe(args, cancel)
            )
            auxiliary = {
                key: probe_media(ref.local_path, lambda args: self.tools.probe(args, cancel))
                for key, ref in refs.items()
                if ref.media_type == "video"
            }
            plan = compile_plan(
                InputBundle(source=info, assets=refs, asset_media=auxiliary), preset
            )
            plan = bind_tool_identity(plan, preset, self.tools.identity(force=True))
            job = self._plan_checkpoint(job, source, plan)
            # Selecting this immutable preset authorizes local processing; optional
            # visual review is not a prerequisite and no approval record is fabricated.
            job = self.store.transition(job.id, job.revision, "PROCESSING", None)
            stage = "render_failed"
            directory = self.workspace / job.id / str(job.content_revision)
            assert_no_reparse(directory)
            directory.mkdir(parents=True, exist_ok=True)
            output = self._cache(directory, plan)
            if output is None:
                candidate = render(
                    plan, directory, cancel, tools=self.tools, on_progress=on_progress
                )
                output = promote_output(
                    validate_output(candidate, plan, tools=self.tools, cancel=cancel),
                    directory / f"output-{uuid4()}.mp4",
                )
                temporary = directory / f"manifest-{uuid4()}.tmp"
                temporary.write_text(output.model_dump_json(indent=2), encoding="utf-8")
                os.replace(temporary, directory / "output.json")
            if cancel.is_set():
                raise ProcessingPaused("processing_canceled")
            self._finish(job, output)
            return output
        except Exception as exc:
            current = self.store.get(job.id)
            code = str(exc) if isinstance(exc, ProcessingPaused) else stage
            if stage == "source_unavailable" and isinstance(exc, SourceAuthenticationRequired):
                code = (
                    "source_browser_session_required"
                    if isinstance(exc, SourceBrowserSessionRequired)
                    else "source_authentication_required"
                )
            if (
                current.state in {"INSPECTING", "DOWNLOADING", "PROCESSING"}
                and not current.content_frozen
            ):
                self.store.transition(job.id, current.revision, "PAUSED", code)
            raise ProcessingPaused(code) from exc

    def require_approved_output(self, job: JobSnapshot) -> OutputManifest:
        return require_approved_job_output(self.store, self.presets, self.approvals, job)


def load_job_plan(store: JobStore, job_id: str) -> MediaPlan:
    job = store.get(job_id)
    with store.connection() as db:
        row = db.execute(
            "SELECT plan_json FROM processing_snapshots WHERE job_id=? AND content_revision=?",
            (job.id, job.content_revision),
        ).fetchone()
    if row is None:
        raise KeyError("Task has no compiled media snapshot")
    plan = MediaPlan.model_validate_json(row[0])
    verify_plan_digest(plan)
    return plan


def require_verified_job_output(
    store: JobStore, presets: PresetStore, approvals: PreviewApprovalStore, job: JobSnapshot
) -> OutputManifest:
    current = store.get(job.id)
    if current.revision != job.revision or current.state != "READY" or not current.output_manifest:
        raise PlanInvalid("Task has no current ready media output")
    plan = load_job_plan(store, job.id)
    preset = presets.get_version(current.spec.preset_version_id)
    approvals.validate_plan_binding(preset, plan)
    output = OutputManifest.model_validate(current.output_manifest)
    if output.plan_digest != plan.digest or current.plan_digest != plan.digest:
        raise PlanInvalid("Output is not for this frozen processing snapshot")
    assert_no_reparse(output.path)
    if (
        not output.path.is_file()
        or output.path.stat().st_size != output.size_bytes
        or sha256_file(output.path) != output.sha256
    ):
        raise PlanInvalid("Verified output is missing or changed")
    validate_metadata(output.media_info, plan)
    return output


def require_approved_job_output(
    store: JobStore, presets: PresetStore, approvals: PreviewApprovalStore, job: JobSnapshot
) -> OutputManifest:
    """Publishing retains its separate visual-approval policy; local downloads do not."""
    output = require_verified_job_output(store, presets, approvals, job)
    plan = load_job_plan(store, job.id)
    preset = presets.get_version(job.spec.preset_version_id)
    if not approvals.approval_matches(preset, plan.asset_refs, compatibility_version(plan)):
        raise PlanInvalid("The media preset has no compatible preview approval")
    return output
