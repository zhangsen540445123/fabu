"""ID-bound media reads and explicit preview commands; no path-based file-serving endpoint."""

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from fabu.assets import assert_no_reparse, sha256_file
from fabu.media.service import load_job_plan, require_verified_job_output
from fabu.store import FrozenContent, InvalidTransition, StaleRevision

router = APIRouter(prefix="/api")


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    expected_revision: int = Field(ge=1)
    windows: list[tuple[int, int]] | None = Field(default=None, max_length=128)


class EmptyCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResumeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


@router.get("/media/status")
def status(request: Request):
    controller = request.app.state.media_controller
    return {
        **(
            controller.status()
            if controller
            else {"running": False, "error_code": "worker_not_started"}
        ),
        "publishing_enabled": False,
    }


@router.get("/media/hardware")
def hardware_status(request: Request):
    from fabu.media.runtime import MediaTools

    settings = request.app.state.settings
    try:
        tools = MediaTools(
            ffmpeg_path=settings.tools_dir / "ffmpeg.exe",
            ffprobe_path=settings.tools_dir / "ffprobe.exe",
        )
        return request.app.state.hardware.cached_report(tools)
    except (OSError, ValueError):
        return {"status": "unchecked", "stale": True, "report": None}


@router.post("/previews", status_code=202)
def create_preview(command: PreviewRequest, request: Request):
    try:
        job = request.app.state.store.get(command.job_id)
        if (
            job.revision != command.expected_revision
            or job.content_frozen
            or job.state not in {"PAUSED", "READY"}
        ):
            raise ValueError("Task is not waiting for a preview")
        load_job_plan(request.app.state.store, job.id)
    except KeyError as exc:
        raise HTTPException(409, "请先让任务完成下载与处理计划检查") from exc
    except ValueError as exc:
        raise HTTPException(409, "任务已变化或尚未就绪，请刷新后重试") from exc
    controller = request.app.state.media_controller
    if not controller or not controller.status()["running"]:
        raise HTTPException(503, "本地处理引擎未启动")
    try:
        return controller.enqueue_preview(job, command.windows)
    except ValueError as exc:
        raise HTTPException(409, "预览任务重复、窗口越界或任务状态已变化") from exc


@router.get("/previews/operations/{operation_id}")
def get_operation(operation_id: str, request: Request):
    with request.app.state.store.connection() as db:
        row = db.execute("SELECT * FROM media_operations WHERE id=?", (operation_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Operation not found")
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "state": row["state"],
        "error_code": row["error_code"],
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
    }


@router.get("/jobs/{job_id}/previews")
def job_previews(job_id: str, request: Request):
    try:
        job = request.app.state.store.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Task not found") from exc
    with request.app.state.store.connection() as db:
        rows = db.execute(
            "SELECT id FROM media_operations WHERE job_id=? AND content_revision=? "
            "ORDER BY created_at DESC",
            (job.id, job.content_revision),
        ).fetchall()
    return [get_operation(row[0], request) for row in rows]


@router.get("/previews/{preview_id}")
def get_preview(preview_id: str, request: Request):
    try:
        manifest = request.app.state.approvals.get_preview(preview_id)
    except (ValueError, KeyError) as exc:
        raise HTTPException(404, "Preview not found") from exc
    with request.app.state.store.connection() as db:
        approved = (
            db.execute(
                "SELECT 1 FROM preview_approvals WHERE preview_id=?", (preview_id,)
            ).fetchone()
            is not None
        )
    return {
        "id": manifest.id,
        "created_at": manifest.created_at,
        "plan_digest": manifest.plan_digest,
        "output_size": manifest.plan.output_size,
        "output_profile": manifest.plan.output_profile,
        "approved": approved,
        "clips": [
            {
                "index": i,
                "start_ms": round(clip.window.start_frame * 1000 / 30),
                "end_ms": round(clip.window.end_frame * 1000 / 30),
                "proxy_label": clip.proxy.label,
                "proxy_url": f"/api/previews/{preview_id}/clips/{i}/proxy",
                "official_url": f"/api/previews/{preview_id}/clips/{i}/official",
            }
            for i, clip in enumerate(manifest.clips)
        ],
    }


@router.post("/previews/{preview_id}/approve")
def approve(preview_id: str, command: EmptyCommand, request: Request):
    try:
        return request.app.state.approvals.approve_preview(preview_id)
    except KeyError as exc:
        raise HTTPException(404, "Preview not found") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(409, "预览已过期或素材发生变化，请重新生成并检查") from exc


def _media_file(path: Path, digest: str, size: int, root: Path, *, filename: str | None = None):
    assert_no_reparse(path)
    if (
        not path.resolve().is_relative_to(root.resolve())
        or not path.is_file()
        or path.stat().st_size != size
        or sha256_file(path) != digest
    ):
        raise ValueError("Media file is missing or changed")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=filename,
        content_disposition_type="attachment" if filename else "inline",
    )


@router.get("/previews/{preview_id}/clips/{index}/{variant}")
def preview_file(
    preview_id: str, index: int, variant: Literal["proxy", "official"], request: Request
):
    try:
        manifest = request.app.state.approvals.get_preview(preview_id)
        if index < 0 or index >= len(manifest.clips):
            raise ValueError("Invalid clip")
        item = getattr(manifest.clips[index], variant)
        return _media_file(
            item.path,
            item.sha256,
            item.size_bytes,
            request.app.state.settings.data_dir,
            filename=f"fabu-official-sample-{index + 1}.mp4" if variant == "official" else None,
        )
    except (KeyError, ValueError, OSError) as exc:
        raise HTTPException(404, "Verified preview file not found") from exc


@router.get("/jobs/{job_id}/plan")
def get_plan(job_id: str, request: Request):
    try:
        job = request.app.state.store.get(job_id)
        plan = load_job_plan(request.app.state.store, job_id)
        preset = request.app.state.presets.get_version(job.spec.preset_version_id)
        return {
            "digest": plan.digest,
            "output_size": plan.output_size,
            "duration_ms": plan.expected_duration_ms,
            "preset": preset,
            "composition": plan.composition_summary,
            "execution": plan.execution,
            "actual_encoder": plan.execution.encoder if plan.execution else "libx265",
            "timings": (job.output_manifest or {}).get("timings", {}),
            "source_manifest": job.source_manifest,
        }
    except (KeyError, ValueError) as exc:
        raise HTTPException(404, "Task has no compiled media plan") from exc


@router.get("/jobs/{job_id}/output")
def output(job_id: str, request: Request):
    try:
        job = request.app.state.store.get(job_id)
        manifest = require_verified_job_output(
            request.app.state.store, request.app.state.presets, request.app.state.approvals, job
        )
        return _media_file(
            manifest.path,
            manifest.sha256,
            manifest.size_bytes,
            request.app.state.settings.data_dir,
            filename="fabu-verified-output.mp4",
        )
    except KeyError as exc:
        raise HTTPException(404, "Task not found") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(409, "成片未就绪或完整性/处理计划检查失败") from exc


@router.post("/jobs/{job_id}/resume")
def resume(job_id: str, command: ResumeCommand, request: Request):
    try:
        controller = request.app.state.media_controller
        if controller and controller.job_busy(job_id):
            raise ValueError("Task already has queued media work")
        job = request.app.state.store.transition(job_id, command.expected_revision, "NEW", None)
        if controller:
            controller.wake()
        return job
    except KeyError as exc:
        raise HTTPException(404, "Task not found") from exc
    except (FrozenContent, InvalidTransition, StaleRevision, ValueError) as exc:
        raise HTTPException(409, "任务已变化、正在预览或需要核对发布结果") from exc
