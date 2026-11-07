"""Authenticated publication endpoints; all execution gates are decided on the server."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from fabu.media.models import OutputManifest
from fabu.publishing.connection import (
    ConnectionRequest,
    connection_status,
    refresh_accounts,
    save_connection,
)
from fabu.publishing.coordinator import PublicationCoordinator
from fabu.publishing.defaults import DefaultsRequest, form_for_job, get_defaults, save_defaults
from fabu.publishing.profiles import ProfileRequest, WorkflowRequest
from fabu.publishing.service import (
    DraftRequest,
    Scope,
    catalogue,
    latest_run,
    load_config,
    public_run,
    save_draft,
)
from fabu.publishing.templates import build_publish_snapshot
from fabu.publishing.topics import GROUPS

router = APIRouter(prefix="/api")


class PreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)
    scope: Scope = "simulation"


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: Scope = "simulation"


def coordinator(app, worker=None):
    return PublicationCoordinator(
        app.state.store,
        app.state.assets,
        app.state.presets,
        app.state.approvals,
        app.state.publication_authority,
        worker,
        local_policy=getattr(app.state, "live_policy", None),
    )


def conflict(exc):
    # Do not serialize exception bodies: filesystem/CLI errors can contain private paths.
    if isinstance(exc, KeyError):
        return HTTPException(404, "任务或发布快照不存在")
    return HTTPException(
        409, "发布条件不满足：请核对任务状态、账号、来源声明、话题、媒体确认与预约时间"
    )


@router.get("/accounts")
def accounts(request: Request, scope: Scope = "simulation"):
    return {
        "scope": scope,
        "live_enabled": request.app.state.live_policy.enabled(),
        "accounts": [
            {
                **account.model_dump(mode="json"),
                "category_names": [n["yixiaoerName"] for n in category.path] if category else [],
            }
            for account, category in catalogue(request.app.state.store, scope)
        ],
        "topic_groups": [{"name": name, "tags": tags} for name, tags in GROUPS.items()],
    }


@router.put("/jobs/{job_id}/publication")
def configure(job_id: str, command: DraftRequest, request: Request):
    try:
        return save_draft(request.app, job_id, command)
    except (ValueError, KeyError) as exc:
        raise conflict(exc) from exc


@router.post("/publish/preflight")
def preflight(command: PreflightRequest, request: Request):
    app = request.app
    try:
        job = app.state.store.get(command.job_id)
        if job.revision != command.expected_revision or job.state not in {"READY", "PAUSED"}:
            raise ValueError("Task is stale or busy")
        run = latest_run(app.state.store, job.id, command.scope)
        bound = coordinator(app)
        if not run or run["content_revision"] != job.content_revision:
            config = load_config(app.state.store, job, command.scope)
            if job.output_manifest is None:
                raise ValueError("Task has no verified output")
            snapshot = build_publish_snapshot(
                job,
                OutputManifest.model_validate(job.output_manifest),
                config,
                store=app.state.store,
                assets=app.state.assets,
                presets=app.state.presets,
                approvals=app.state.approvals,
                require_preview=not app.state.live_policy.is_authorized(job.id),
            )
            run = bound.register(snapshot, command.scope)
        bound._files_and_approval(bound._snapshot(run))
        return public_run(app.state.store, run, app.state.live_policy)
    except (ValueError, KeyError) as exc:
        raise conflict(exc) from exc


@router.get("/jobs/{job_id}/targets")
def targets(job_id: str, request: Request, scope: Scope = "simulation"):
    try:
        request.app.state.store.get(job_id)
        run = latest_run(request.app.state.store, job_id, scope)
        return (
            public_run(request.app.state.store, run, request.app.state.live_policy)
            if run
            else {
                "scope": scope,
                "targets": [],
                "can_submit": False,
                "can_reconcile": False,
                "state": "NOT_PREPARED",
            }
        )
    except (ValueError, KeyError) as exc:
        raise conflict(exc) from exc


def enqueue(job_id, command, request, action):
    try:
        if action == "submit" and command.scope == "live":
            if not request.app.state.live_policy.is_authorized(job_id):
                raise ValueError("An explicitly authorized new workflow is required")
        run = coordinator(request.app).status(command.snapshot_digest, command.scope)
        if run["job_id"] != job_id:
            raise ValueError("Snapshot is for another task")
        controller = request.app.state.media_controller
        if not controller.status()["running"]:
            raise ValueError("Start the global worker before publication")
        return request.app.state.publication_queue.enqueue(run, action)
    except (ValueError, KeyError) as exc:
        raise conflict(exc) from exc


@router.post("/jobs/{job_id}/submit", status_code=202)
def submit(job_id: str, command: RunRequest, request: Request):
    return enqueue(job_id, command, request, "submit")


@router.post("/jobs/{job_id}/reconcile", status_code=202)
def reconcile(job_id: str, command: RunRequest, request: Request):
    return enqueue(job_id, command, request, "reconcile")


@router.get("/publishing/connection")
def get_connection(request: Request):
    try:
        return connection_status(request.app)
    except (ValueError, OSError) as exc:
        raise conflict(exc) from exc


@router.put("/publishing/connection")
def configure_connection(command: ConnectionRequest, request: Request):
    try:
        return save_connection(request.app, command)
    except (ValueError, OSError) as exc:
        raise conflict(exc) from exc


@router.post("/accounts/refresh")
def refresh_connection_accounts(request: Request):
    try:
        return refresh_accounts(request.app)
    except (ValueError, KeyError, RuntimeError, OSError) as exc:
        raise HTTPException(
            409, "只读同步未完成；请检查官方CLI路径、密钥、客户端ID与网络。发布仍保持禁用。"
        ) from exc


@router.get("/publish/profiles")
def get_profiles(request: Request):
    return request.app.state.publication_profiles.list()


@router.post("/publish/profiles", status_code=201)
def save_profile(command: ProfileRequest, request: Request):
    try:
        return request.app.state.publication_profiles.save(command)
    except (ValueError, KeyError) as exc:
        raise conflict(exc) from exc


@router.post("/workflow/jobs", status_code=201)
def create_workflow(command: WorkflowRequest, request: Request):
    try:
        return request.app.state.publication_profiles.create(command)
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            409, "流程条件不满足：核对默认预设、发布配置、账号、话题、时间及本机执行授权"
        ) from exc


class HandoffResume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


@router.get("/workflow/jobs/{job_id}/handoff")
def handoff_status(job_id: str, request: Request):
    try:
        return request.app.state.publication_profiles.handoff_status(job_id)
    except (ValueError, KeyError) as exc:
        raise conflict(exc) from exc


@router.post("/workflow/jobs/{job_id}/resume")
def resume_handoff(job_id: str, command: HandoffResume, request: Request):
    try:
        return request.app.state.publication_profiles.resume_handoff(
            job_id, command.expected_revision
        )
    except (ValueError, KeyError) as exc:
        raise conflict(exc) from exc


@router.get("/publishing/defaults")
def publishing_defaults(request: Request):
    return get_defaults(request.app)


@router.put("/publishing/defaults")
def configure_defaults(command: DefaultsRequest, request: Request):
    try:
        return save_defaults(request.app, command)
    except (ValueError, KeyError) as exc:
        raise conflict(exc) from exc


@router.get("/jobs/{job_id}/publication")
def publication_form(job_id: str, request: Request, scope: Scope | None = None):
    try:
        return form_for_job(request.app, job_id, scope)
    except (ValueError, KeyError) as exc:
        raise conflict(exc) from exc


@router.get("/publishing/readiness")
def publishing_readiness(request: Request):
    return request.app.state.live_policy.readiness()


class DefaultPresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset_version_id: str = Field(min_length=1, max_length=100)


@router.put("/publishing/default-preset")
def configure_default_preset(command: DefaultPresetRequest, request: Request):
    from fabu.publishing.defaults import set_default_preset

    try:
        return set_default_preset(request.app, command.preset_version_id)
    except (ValueError, KeyError) as exc:
        raise conflict(exc) from exc
