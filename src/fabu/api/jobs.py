"""Job API uses validated IDs/specifications; real writes remain locked until P05/P06."""

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from fabu.domain import JobSpec
from fabu.store import FrozenContent, InvalidTransition, StaleRevision

router = APIRouter(prefix="/api/jobs")


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


@router.get("")
def list_jobs(request: Request):
    return request.app.state.store.list_jobs()


@router.post("", status_code=201)
def create_job(spec: JobSpec, request: Request):
    if spec.publish_mode not in {"local", "simulation"}:
        raise HTTPException(409, "Real publishing is not verified or enabled")
    try:
        url = urlparse(spec.source_url)
        port = url.port
    except ValueError as exc:
        raise HTTPException(422, "Invalid YouTube URL") from exc
    if (
        url.scheme != "https"
        or url.hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
        or url.username
        or url.password
        or port
    ):
        raise HTTPException(422, "Select a single HTTPS YouTube video URL")
    try:
        request.app.state.presets.get_version(spec.preset_version_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, "Select an existing preset version") from exc
    return request.app.state.store.create(spec)


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
        if current.owner_worker_id:
            # Cancelling an actively executing process requires M03's cancellation token service.
            raise HTTPException(409, "Active task cancellation is not yet available")
        return request.app.state.store.transition(
            job_id, command.expected_revision, "CANCELED", "user_canceled"
        )
    except KeyError as exc:
        raise HTTPException(404, "Task not found") from exc
    except (FrozenContent, InvalidTransition, StaleRevision) as exc:
        raise HTTPException(
            409, "Task changed or has submitted content; reload and reconcile"
        ) from exc
