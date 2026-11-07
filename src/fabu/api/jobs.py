"""Job API uses validated IDs/specifications; real writes remain locked until P05/P06."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from fabu.domain import JobSpec
from fabu.media.source import parse_youtube_url
from fabu.store import FrozenContent, InvalidTransition, StaleRevision

router = APIRouter(prefix="/api/jobs")


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


@router.get("")
def list_jobs(request: Request):
    return request.app.state.store.list_jobs()


def _validate_spec(spec: JobSpec, request: Request):
    if spec.publication_profile_id:
        try:
            request.app.state.publication_profiles.validate_spec(spec)
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                409, "Workflow profile is invalid or live publishing is disabled"
            ) from exc
    elif spec.publish_mode not in {"local", "simulation"}:
        raise HTTPException(409, "Real publishing is not verified or enabled")
    try:
        parse_youtube_url(spec.source_url)
    except ValueError as exc:
        raise HTTPException(422, "Select a single HTTPS YouTube video URL") from exc
    try:
        request.app.state.presets.get_version(spec.preset_version_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, "Select an existing preset version") from exc


@router.post("", status_code=201)
def create_job(spec: JobSpec, request: Request):
    _validate_spec(spec, request)
    job = request.app.state.store.create(spec)
    request.app.state.media_controller.wake()
    return job


@router.get("/{job_id}")
def get_job(job_id: str, request: Request):
    try:
        return request.app.state.store.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Task not found") from exc


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, command: CancelRequest, request: Request):
    try:
        current = request.app.state.store.get(job_id)
        controller = request.app.state.media_controller
        if current.owner_worker_id and (not controller or controller.status()["job_id"] != job_id):
            raise HTTPException(409, "Task is owned by another worker; reload before canceling")
        result = request.app.state.store.transition(
            job_id, command.expected_revision, "CANCELED", "user_canceled"
        )
        if controller:
            controller.cancel_job(job_id)
        return result
    except KeyError as exc:
        raise HTTPException(404, "Task not found") from exc
    except (FrozenContent, InvalidTransition, StaleRevision) as exc:
        raise HTTPException(
            409, "Task changed or has submitted content; reload and reconcile"
        ) from exc


class RevisionRequest(CancelRequest):
    spec: JobSpec


@router.post("/{job_id}/revise")
def revise_job(job_id: str, command: RevisionRequest, request: Request):
    _validate_spec(command.spec, request)
    try:
        controller = request.app.state.media_controller
        if controller.job_busy(job_id):
            raise InvalidTransition("Pause active work before editing")
        job = request.app.state.store.revise(job_id, command.expected_revision, command.spec)
        controller.wake()
        return job
    except KeyError as exc:
        raise HTTPException(404, "Task not found") from exc
    except (FrozenContent, InvalidTransition, StaleRevision) as exc:
        raise HTTPException(409, "任务已变化、正在处理或已有提交；不能替换当前内容") from exc
