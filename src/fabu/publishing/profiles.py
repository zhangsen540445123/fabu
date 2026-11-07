"""Immutable reusable publishing profiles and automatic media-to-publication handoff."""

import json
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from fabu.domain import JobSpec
from fabu.media.models import OutputManifest
from fabu.media.service import require_verified_job_output
from fabu.media.source import parse_youtube_url
from fabu.publishing.models import PublicationConfig
from fabu.publishing.service import Scope, busy, catalogue, load_config
from fabu.publishing.templates import build_publish_snapshot
from fabu.publishing.topics import select_topic_group


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=100)
    scope: Scope


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_url: str = Field(min_length=1, max_length=4096)
    title: str = Field(min_length=1, max_length=1000)
    preset_version_id: str | None = Field(default=None, min_length=1, max_length=100)
    profile_id: str | None = Field(default=None, min_length=1, max_length=100)
    mode: Literal["immediate", "native_schedule"] = "immediate"
    scheduled_at_utc: datetime | None = None


class PublicationProfiles:
    def __init__(self, app):
        self.app = app
        self.store = app.state.store

    def get(self, identifier):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM publication_profiles WHERE id=?", (identifier,)
            ).fetchone()
        if row is None:
            raise ValueError("Publication profile does not exist")
        return dict(row)

    def public(self, row):
        config = PublicationConfig.model_validate_json(row["config_json"])
        return {
            "id": row["id"],
            "name": row["name"],
            "scope": row["scope"],
            "accounts": [a.account.model_dump(mode="json") for a in config.accounts],
            "topic_override": config.topic_override,
            "can_run": True,
            "execution": "simulation"
            if row["scope"] == "simulation"
            else ("live" if self.app.state.live_policy.enabled() else "preflight_only"),
        }

    def list(self):
        with self.store.connection() as db:
            rows = db.execute("SELECT * FROM publication_profiles ORDER BY rowid DESC").fetchall()
        return [self.public(dict(row)) for row in rows]

    def save(self, command):
        if not command.name.strip():
            raise ValueError("Profile name cannot be blank")
        job = self.store.get(command.job_id)
        if job.revision != command.expected_revision or job.content_frozen:
            raise ValueError("Task changed or content frozen")
        config = load_config(self.store, job, command.scope)
        identifier = str(uuid4())
        now = datetime.now(UTC).isoformat()
        with self.store.transaction() as db:
            current = self.store._snapshot(
                db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
            )
            if current != job:
                raise ValueError("Task changed while saving profile")
            db.execute(
                "INSERT INTO publication_profiles VALUES (?,?,?,?,?)",
                (identifier, command.name.strip(), command.scope, config.model_dump_json(), now),
            )
        return self.public(self.get(identifier))

    def _current_config(self, row):
        config = PublicationConfig.model_validate_json(row["config_json"])
        observed = {a.account_id: (a, c) for a, c in catalogue(self.store, row["scope"])}
        settings = []
        for setting in config.accounts:
            item = observed.get(setting.account.account_id)
            if not item or not item[0].active or item[0].platform != setting.account.platform:
                raise ValueError("Profile account needs review")
            settings.append(setting.model_copy(update={"account": item[0], "category": item[1]}))
        return config.model_copy(update={"accounts": tuple(settings)})

    def validate_spec(self, spec):
        row = self.get(spec.publication_profile_id)
        config = self._current_config(row)
        if spec.publish_mode not in {"immediate", "native_schedule"} or spec.target_ids != [
            a.account.account_id for a in config.accounts
        ]:
            raise ValueError("Workflow targets/mode differ from immutable profile")
        if spec.scheduled_at_utc and spec.scheduled_at_utc <= datetime.now(UTC):
            raise ValueError("Scheduled time expired")
        if select_topic_group(spec.title, config.topic_override).status != "selected":
            raise ValueError("Select an unambiguous topic/profile")
        return config

    def create(self, command):
        from fabu.publishing.defaults import get_default_preset, set_default_preset

        profile_id = command.profile_id
        if profile_id is None:
            with self.store.connection() as db:
                default = db.execute(
                    "SELECT profile_id FROM publication_defaults WHERE singleton=1"
                ).fetchone()
            if not default:
                raise ValueError("Save default publishing configuration first")
            profile_id = default[0]
        command = command.model_copy(
            update={
                "profile_id": profile_id,
                "preset_version_id": command.preset_version_id or get_default_preset(self.app).id,
            }
        )
        row = self.get(command.profile_id)
        policy = self.app.state.live_policy
        authorize = row["scope"] == "live" and policy.enabled()
        if authorize:
            policy.check_identity()
            policy.refresh_catalogue_if_stale()
            if command.mode != "immediate":
                raise ValueError("Native scheduling is not verified; no immediate fallback")
        config = self._current_config(row)
        spec = JobSpec(
            source_url=command.source_url,
            title=command.title,
            preset_version_id=command.preset_version_id,
            publication_profile_id=command.profile_id,
            publish_mode=command.mode,
            scheduled_at_utc=command.scheduled_at_utc,
            target_ids=[a.account.account_id for a in config.accounts],
        )
        parse_youtube_url(spec.source_url)
        self.app.state.presets.get_version(spec.preset_version_id)
        self.validate_spec(spec)
        hook = (lambda db, job: policy.authorize_job(job, db=db)) if authorize else None
        job = self.store.create(spec, on_create=hook)
        set_default_preset(self.app, spec.preset_version_id)
        self.app.state.media_controller.wake()
        return job

    def recover_interrupted(self):
        now = datetime.now(UTC).isoformat()
        with self.store.transaction() as db:
            rows = db.execute(
                "SELECT job_id,content_revision FROM workflow_handoffs WHERE state='PREPARING'"
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE workflow_handoffs SET state='PAUSED', "
                    "error_code='handoff_interrupted', updated_at=? WHERE job_id=? AND "
                    "content_revision=?",
                    (now, row["job_id"], row["content_revision"]),
                )
                job = self.store._snapshot(
                    db.execute("SELECT * FROM jobs WHERE id=?", (row["job_id"],)).fetchone()
                )
                profile = db.execute(
                    "SELECT scope FROM publication_profiles WHERE id=?",
                    (job.spec.publication_profile_id,),
                ).fetchone()
                if profile and profile["scope"] == "live":
                    # A preflight interruption must not make verified media unavailable.
                    continue
                db.execute(
                    "UPDATE jobs SET state='PAUSED', pause_reason='handoff_interrupted', "
                    "revision=revision+1 WHERE id=? AND content_revision=? AND state='READY'",
                    (row["job_id"], row["content_revision"]),
                )

    def _resume_allowed(self, db, job):
        if (
            job.state not in {"PAUSED", "READY"}
            or job.owner_worker_id
            or job.content_frozen
            or not job.spec.publication_profile_id
            or not job.output_manifest
            or busy(db, job.id)
        ):
            return False
        marker = db.execute(
            "SELECT state,error_code FROM workflow_handoffs WHERE job_id=? AND content_revision=?",
            (job.id, job.content_revision),
        ).fetchone()
        profile = db.execute(
            "SELECT scope FROM publication_profiles WHERE id=?", (job.spec.publication_profile_id,)
        ).fetchone()
        if not marker or marker["state"] != "PAUSED" or not profile:
            return False
        retryable = {"handoff_interrupted", "workflow_preflight_failed"}
        if marker["error_code"] not in retryable:
            return False
        scope = profile["scope"]
        if scope == "simulation" and (job.state != "PAUSED" or job.pause_reason not in retryable):
            return False
        if scope == "live" and job.state != "READY":
            return False
        if job.spec.scheduled_at_utc and job.spec.scheduled_at_utc <= datetime.now(UTC):
            return False
        source = parse_youtube_url(job.spec.source_url)
        # A handoff reset is not permission to retry an upload or a target attempt.
        return not db.execute(
            "SELECT 1 FROM target_attempts WHERE job_id=? "
            "UNION ALL SELECT 1 FROM upload_attempts WHERE scope=? "
            "AND source_video_id=? AND state IN ('SUBMITTING','UNKNOWN') "
            "UNION ALL SELECT 1 FROM publication_source_claims "
            "WHERE scope=? AND video_id=?",
            (job.id, scope, source, scope, source),
        ).fetchone()

    def handoff_status(self, job_id):
        with self.store.connection() as db:
            job = self.store._snapshot(
                db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            )
            row = db.execute(
                "SELECT state,error_code FROM workflow_handoffs WHERE job_id=? "
                "AND content_revision=?",
                (job.id, job.content_revision),
            ).fetchone()
            return {
                "state": row["state"] if row else "NOT_STARTED",
                "error_code": row["error_code"] if row else None,
                "can_resume": self._resume_allowed(db, job),
            }

    def resume_handoff(self, job_id, expected_revision):
        job = self.store.get(job_id)
        self.validate_spec(job.spec)
        with self.store.transaction() as db:
            current = self.store._snapshot(
                db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            )
            if (
                current != job
                or current.revision != expected_revision
                or not self._resume_allowed(db, current)
            ):
                raise ValueError("Handoff requires unchanged content and no uncertain side effects")
            db.execute(
                "DELETE FROM workflow_handoffs WHERE job_id=? AND content_revision=?",
                (job.id, job.content_revision),
            )
            db.execute(
                "UPDATE jobs SET state='READY',pause_reason=NULL,revision=revision+1, "
                "updated_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), job.id),
            )
            self.store._event(db, job.id, {"event": "handoff_resume_requested", "status": "READY"})
        self.app.state.media_controller.wake()
        return self.store.get(job_id)

    def process_ready(self, worker, cancel):
        if not worker.active or worker.store.path != self.store.path:
            raise ValueError("Workflow requires global worker")
        now = datetime.now(UTC).isoformat()
        with self.store.transaction() as db:
            rows = db.execute(
                "SELECT j.* FROM jobs j WHERE j.state='READY' AND j.owner_worker_id IS NULL AND "
                "j.content_frozen=0 AND NOT EXISTS (SELECT 1 FROM workflow_handoffs w WHERE "
                "w.job_id=j.id AND w.content_revision=j.content_revision) ORDER BY j.created_at"
            ).fetchall()
            row = next(
                (r for r in rows if json.loads(r["spec_json"]).get("publication_profile_id")), None
            )
            if row is None:
                return False
            job = self.store._snapshot(row)
            db.execute(
                "INSERT INTO workflow_handoffs VALUES (?,?,'PREPARING',NULL,?)",
                (job.id, job.content_revision, now),
            )
        scope = self.get(job.spec.publication_profile_id)["scope"]
        try:
            if cancel.is_set():
                raise ValueError("Workflow canceled")
            policy = self.app.state.live_policy
            automatic_live = scope == "live" and policy.is_authorized(job.id)
            if automatic_live:
                policy.refresh_catalogue_if_stale()
            config = self.validate_spec(job.spec)
            if scope == "live" and not automatic_live:
                require_verified_job_output(
                    self.store, self.app.state.presets, self.app.state.approvals, job
                )
                state, error = "PAUSED", "live_publish_not_enabled"
            else:
                snapshot = build_publish_snapshot(
                    job,
                    OutputManifest.model_validate(job.output_manifest),
                    config,
                    store=self.store,
                    assets=self.app.state.assets,
                    presets=self.app.state.presets,
                    approvals=self.app.state.approvals,
                    require_preview=not automatic_live,
                )
                coordinator = self.app.state.publication_queue.coordinator_factory(worker)
                run = coordinator.register(snapshot, scope)
                self.app.state.publication_queue.enqueue(run, "submit")
                state, error = "QUEUED", None
        except (ValueError, KeyError, OSError):
            state, error = "PAUSED", "workflow_preflight_failed"
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE jobs SET state='PAUSED', pause_reason=?, revision=revision+1 WHERE "
                    "id=? AND content_revision=? AND state='READY' AND ?='simulation'",
                    (error, job.id, job.content_revision, scope),
                )
        with self.store.transaction() as db:
            db.execute(
                "UPDATE workflow_handoffs SET state=?, error_code=?, updated_at=? WHERE job_id=?"
                " AND content_revision=?",
                (state, error, datetime.now(UTC).isoformat(), job.id, job.content_revision),
            )
        return True
