"""Render to a non-public candidate. Only the independent validator can approve it."""

import json
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from fabu.assets import assert_no_reparse, sha256_file
from fabu.media.baseline import build_render_command
from fabu.media.models import MediaPlan, verify_plan_digest
from fabu.media.probe import scan_frame_geometry
from fabu.media.resources import materialize_resources
from fabu.media.runtime import MediaTools


class RenderFailed(ValueError):
    pass


def read_progress(path: Path, expected_ms: int) -> float:
    if not path.is_file() or expected_ms <= 0:
        return 0.0
    # Bound monitoring cost; FFmpeg progress file grows during a long encode.
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 4096))
        text = stream.read(4096).decode("utf-8", "replace")
    values = [
        line.split("=", 1)[1] for line in text.splitlines() if line.startswith("out_time_us=")
    ]
    try:
        return max(0.0, min(99.0, int(values[-1]) / expected_ms / 10)) if values else 0.0
    except ValueError:
        return 0.0


def render(
    plan: MediaPlan,
    workspace: Path,
    cancel: threading.Event,
    *,
    tools: MediaTools,
    on_progress: Callable[[float], None] | None = None,
    min_free_bytes: int = 256 * 1024 * 1024,
) -> Path:
    verify_plan_digest(plan)
    if plan.tool_identity:
        tools.validate_plan_tools(plan)
    if cancel.is_set():
        raise RenderFailed("Rendering canceled before start")
    if not workspace.is_absolute():
        raise RenderFailed("Rendering workspace must be absolute")
    assert_no_reparse(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(workspace).free < max(min_free_bytes, plan.source.path.stat().st_size * 2):
        raise RenderFailed("Insufficient free space for safe rendering")
    assert_no_reparse(plan.source.path)
    if sha256_file(plan.source.path) != plan.source_hash:
        raise RenderFailed("The source changed after probing; recompile before rendering")
    for asset in plan.asset_refs.values():
        assert_no_reparse(asset.local_path)
        if sha256_file(asset.local_path) != plan.asset_hashes.get(asset.id):
            raise RenderFailed("Auxiliary asset changed after compilation")
    scan_frame_geometry(plan.source, lambda args: tools.probe(args, cancel))
    for info in plan.video_inputs.values():
        scan_frame_geometry(info, lambda args: tools.probe(args, cancel))
    if cancel.is_set():
        raise RenderFailed("Rendering canceled during source verification")
    identifier = str(uuid4())
    candidate = workspace / f"candidate-{identifier}.mp4"
    progress = workspace / f"progress-{identifier}.txt"
    record = workspace / f"render-{identifier}.json"
    resource_dir = workspace / f"resources-{identifier}"
    graph_path = None
    if plan.video_graph:
        graph_path = materialize_resources(plan, resource_dir)
    command = build_render_command(plan, candidate, progress, graph_path=graph_path)
    done = threading.Event()
    monitor_error = []

    def monitor():
        while not done.wait(0.25):
            try:
                if shutil.disk_usage(workspace).free < min_free_bytes:
                    monitor_error.append("Insufficient disk space while rendering")
                    cancel.set()
                    return
                if on_progress:
                    on_progress(read_progress(progress, plan.expected_duration_ms))
            except Exception:
                monitor_error.append("Rendering monitor failed; task paused")
                cancel.set()
                return

    observer = threading.Thread(target=monitor, name="fabu-render-monitor", daemon=True)
    observer.start()
    try:
        record.write_text(
            json.dumps({"state": "PROCESSING", "plan_digest": plan.digest}), encoding="utf-8"
        )
        if graph_path:
            result = tools.ffmpeg(command.args, cancel, cwd=resource_dir)
        else:
            result = tools.ffmpeg(command.args, cancel)
        if monitor_error:
            raise RenderFailed(monitor_error[0])
        if result.exit_code or result.canceled or result.timed_out or result.output_limit_exceeded:
            raise RenderFailed("Rendering failed or was canceled; candidate is not approved")
        if not candidate.is_file() or not candidate.stat().st_size:
            raise RenderFailed("Renderer did not produce a nonempty candidate")
        for asset in plan.asset_refs.values():
            if sha256_file(asset.local_path) != plan.asset_hashes.get(asset.id):
                raise RenderFailed("Auxiliary asset changed during rendering")
        if sha256_file(plan.source.path) != plan.source_hash:
            raise RenderFailed("The source changed during rendering; candidate is not approved")
        record.write_text(
            json.dumps(
                {"state": "UNVERIFIED", "plan_digest": plan.digest, "candidate": candidate.name}
            ),
            encoding="utf-8",
        )
        return candidate
    except BaseException:
        record.write_text(
            json.dumps({"state": "FAILED", "plan_digest": plan.digest}), encoding="utf-8"
        )
        raise
    finally:
        done.set()
        observer.join(5)
