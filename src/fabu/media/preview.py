"""Preview final-timeline windows of the production plan, with separate AVC viewing proxies."""

import hashlib
import json
import math
import os
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from threading import Event
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fabu.assets import assert_no_reparse, sha256_file
from fabu.media.filters_time import seconds
from fabu.media.models import (
    MediaPlan,
    OutputManifest,
    PlanInvalid,
    RenderStage,
    plan_digest,
    verify_plan_digest,
)
from fabu.media.probe import probe_media
from fabu.media.renderer import render
from fabu.media.runtime import MediaTools
from fabu.media.validator import promote_output, validate_output

PREVIEW_MAX_AGE = timedelta(days=7)


class PreviewWindow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)

    @model_validator(mode="after")
    def positive(self):
        if self.end_frame <= self.start_frame:
            raise ValueError("Preview window must include at least one frame")
        return self

    @property
    def duration(self) -> Fraction:
        return Fraction(self.end_frame - self.start_frame, 30)


class ViewingProxy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: Path
    sha256: str
    size_bytes: int
    codec: str = "h264"
    label: str = "观看代理"


class PreviewClip(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    window: PreviewWindow
    official: OutputManifest
    proxy: ViewingProxy


class PreviewManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    plan: MediaPlan
    cache_key: str
    clips: list[PreviewClip]
    created_at: datetime
    digest: str

    @property
    def plan_digest(self) -> str:
        return self.plan.digest


def _manifest_digest(manifest: PreviewManifest) -> str:
    return hashlib.sha256(
        json.dumps(
            manifest.model_dump(mode="json", exclude={"digest"}),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _final_frame_count(plan: MediaPlan) -> int:
    if plan.composition_summary:
        duration = sum(Fraction(s["duration"]) for s in plan.composition_summary)
    else:
        duration = Fraction(plan.timing_summary.get("main_duration", plan.source.duration))
    return math.ceil(duration * 30)


def normalize_windows(plan: MediaPlan, windows: list[tuple[int, int]]) -> list[PreviewWindow]:
    if not windows or len(windows) > 128:
        raise PlanInvalid("Choose between 1 and 128 preview windows")
    result = []
    for start, end in windows:
        if start < 0 or end <= start or end > plan.expected_duration_ms:
            raise PlanInvalid("Preview window is outside the final timeline")
        result.append(
            PreviewWindow(
                start_frame=math.ceil(Fraction(start * 30, 1000)),
                end_frame=min(_final_frame_count(plan), math.ceil(Fraction(end * 30, 1000))),
            )
        )
    if sum(w.duration for w in result) > 300:
        raise PlanInvalid("Combined preview windows exceed five minutes")
    return result


def default_windows(plan: MediaPlan) -> list[tuple[int, int]]:
    duration = plan.expected_duration_ms
    main = next((s for s in plan.composition_summary if s["kind"] == "main"), None)
    start = main["start_ms"] if main else 0
    length = main["duration_ms"] if main else duration
    middle = start + max(0, (length - 15000) // 2)
    ranges = [(middle, min(middle + 15000, start + length))]
    for segment in plan.composition_summary:
        for point in [segment["start_ms"], segment["start_ms"] + segment["duration_ms"]]:
            ranges.append((max(0, point - 500), min(duration, point + 500)))
    for layer in plan.layer_summary:
        offset = start if layer["scope"] == "main" else 0
        for point in layer.get("boundaries_ms", []):
            point += offset
            ranges.append((max(0, point - 250), min(duration, point + 250)))
    merged = []
    for left, right in sorted(ranges):
        if right <= left:
            continue
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    return merged


def window_plan(plan: MediaPlan, window: PreviewWindow) -> MediaPlan:
    verify_plan_digest(plan)
    if window.end_frame > _final_frame_count(plan):
        raise PlanInvalid("Preview window exceeds the final plan")
    start, end = Fraction(window.start_frame, 30), Fraction(window.end_frame, 30)
    video = (
        f"trim=start_frame={window.start_frame}:end_frame={window.end_frame},setpts=PTS-STARTPTS"
    )
    if plan.video_graph:
        graph = plan.video_graph.replace("[vout]", "[preview_vin]")
    else:
        vf = [f for stage in plan.stages for f in stage.video_filters]
        graph = f"[0:{plan.source.video_stream_index}]" + ",".join(vf) + "[preview_vin]"
    graph += ";[preview_vin]" + video + "[vout]"
    af = [
        f"apad=whole_dur={seconds(end)}",
        f"atrim=start={seconds(start)}:end={seconds(end)}",
        "asetpts=PTS-STARTPTS",
    ]
    stages = plan.stages
    if plan.complex_audio:
        graph = graph.replace("[aout]", "[preview_ain]")
        graph += ";[preview_ain]" + ",".join(af) + "[aout]"
    else:
        stages = [
            RenderStage(
                kind="preview",
                video_filters=[],
                audio_filters=[f for s in stages for f in s.audio_filters] + af,
            )
        ]
    result = plan.model_copy(
        update={
            "video_graph": graph,
            "stages": stages,
            "expected_duration_ms": round(window.duration * 1000),
            "audio_profile": plan.audio_profile.model_copy(
                update={"expected_duration": window.duration}
            ),
        }
    )
    return result.model_copy(update={"digest": plan_digest(result)})


def _verify_inputs(plan: MediaPlan) -> None:
    for path, digest in [
        (plan.source.path, plan.source_hash),
        *((a.local_path, a.sha256) for a in plan.asset_refs.values()),
    ]:
        assert_no_reparse(path)
        if not path.is_file() or sha256_file(path) != digest:
            raise PlanInvalid("A preview source or asset is missing or changed")


def verify_preview(manifest: PreviewManifest) -> None:
    verify_plan_digest(manifest.plan)
    if not manifest.clips or _manifest_digest(manifest) != manifest.digest:
        raise PlanInvalid("Preview manifest integrity failed")
    _verify_inputs(manifest.plan)
    for clip in manifest.clips:
        if clip.official.plan_digest != window_plan(manifest.plan, clip.window).digest:
            raise PlanInvalid("Preview does not match the production plan window")
        for item in [clip.official, clip.proxy]:
            assert_no_reparse(item.path)
            if (
                not item.path.is_file()
                or item.path.stat().st_size != item.size_bytes
                or sha256_file(item.path) != item.sha256
            ):
                raise PlanInvalid("Preview file is missing or failed integrity verification")


def _proxy(official: OutputManifest, path: Path, tools: MediaTools, cancel: Event) -> ViewingProxy:
    result = tools.ffmpeg(
        [
            "-hide_banner",
            "-v",
            "error",
            "-nostdin",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(official.path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-vf",
            "scale=640:640:force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-metadata",
            "title=观看代理",
            "-metadata",
            "comment=Viewing proxy only; not the production HEVC sample",
            "-n",
            str(path),
        ],
        cancel,
    )
    if result.exit_code or result.timed_out or result.canceled or result.output_limit_exceeded:
        raise PlanInvalid("Viewing proxy generation failed")
    info = probe_media(path, tools.probe)
    if (
        info.codec != "h264"
        or info.audio is None
        or abs(info.duration - official.media_info.duration) > Fraction(2, 30)
    ):
        raise PlanInvalid("Viewing proxy metadata failed verification")
    decoded = tools.ffmpeg(
        [
            "-v",
            "error",
            "-xerror",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        cancel,
    )
    if decoded.exit_code or decoded.stderr.strip() or decoded.canceled or decoded.timed_out:
        raise PlanInvalid("Viewing proxy decode failed")
    return ViewingProxy(path=path, sha256=info.sha256, size_bytes=path.stat().st_size)


def build_preview(
    plan: MediaPlan,
    windows: list[tuple[int, int]] | None = None,
    *,
    workspace: Path,
    tools: MediaTools,
    cancel: Event | None = None,
) -> PreviewManifest:
    verify_plan_digest(plan)
    if plan.tool_identity:
        tools.validate_plan_tools(plan)
    _verify_inputs(plan)
    selected = normalize_windows(plan, default_windows(plan) if windows is None else windows)
    key = hashlib.sha256(
        json.dumps(
            {
                "plan": plan.digest,
                "engine": plan.engine_version,
                "windows": [w.model_dump() for w in selected],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if not workspace.is_absolute():
        raise PlanInvalid("Preview workspace must be absolute")
    assert_no_reparse(workspace)
    directory = workspace / key
    directory.mkdir(parents=True, exist_ok=True)
    saved = directory / "manifest.json"
    if saved.is_file():
        try:
            cached = PreviewManifest.model_validate_json(saved.read_text(encoding="utf-8"))
            if datetime.now(UTC) - cached.created_at > PREVIEW_MAX_AGE:
                raise PlanInvalid("Preview cache expired")
            if cached.cache_key != key or cached.plan != plan:
                raise PlanInvalid("Preview cache does not match this plan")
            if any(
                item.path.parent != directory
                for clip in cached.clips
                for item in [clip.official, clip.proxy]
            ):
                raise PlanInvalid("Preview cache path is outside its directory")
            verify_preview(cached)
            return cached
        except (ValueError, OSError):
            # Corrupt or stale cache is not a confirmed sample; create independent new outputs.
            pass
    cancel = cancel or Event()
    identifier = str(uuid4())
    clips = []
    for index, window in enumerate(selected):
        part = window_plan(plan, window)
        candidate = render(part, directory, cancel, tools=tools)
        approved = validate_output(candidate, part, tools=tools, cancel=cancel)
        official = promote_output(approved, directory / f"official-{identifier}-{index}.mp4")
        proxy = _proxy(
            official, directory / f"watching-proxy-{identifier}-{index}.mp4", tools, cancel
        )
        clips.append(PreviewClip(window=window, official=official, proxy=proxy))
    manifest = PreviewManifest(
        id=identifier,
        plan=plan,
        cache_key=key,
        clips=clips,
        created_at=datetime.now(UTC),
        digest="",
    )
    manifest = manifest.model_copy(update={"digest": _manifest_digest(manifest)})
    verify_preview(manifest)
    temporary = directory / f"manifest-{identifier}.tmp"
    temporary.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    os.replace(temporary, saved)
    return manifest
