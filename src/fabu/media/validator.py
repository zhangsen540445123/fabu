"""Verify decode, metadata and expected timing before promoting an independent output."""

import os
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from threading import Event

from fabu.assets import assert_no_reparse, sha256_file
from fabu.media.models import MediaPlan, OutputManifest, verify_plan_digest
from fabu.media.probe import MediaInfo, probe_media, scan_frame_geometry
from fabu.media.runtime import MediaTools


class OutputInvalid(ValueError):
    pass


def validate_metadata(info: MediaInfo, plan: MediaPlan) -> None:
    verify_plan_digest(plan)
    if (info.coded_width, info.coded_height) != (plan.output_size.width, plan.output_size.height):
        raise OutputInvalid("Output dimensions do not match the approved plan")
    if (
        info.codec != "hevc"
        or info.pixel_format != "yuv420p"
        or info.fps != 30
        or info.sar != 1
        or info.rotation_deg != 0
        or info.color.range != "tv"
    ):
        raise OutputInvalid("Output format/frame rate/display metadata does not match the plan")
    expected = Fraction(plan.expected_duration_ms, 1000)
    tolerance = Fraction(2, 30)
    if abs(info.duration - expected) > tolerance:
        raise OutputInvalid("Output video duration differs from the planned timeline")
    audio = info.audio
    profile = plan.audio_profile
    if (
        audio is None
        or audio.codec != "aac"
        or audio.sample_rate != profile.sample_rate
        or audio.channels != profile.channels
        or audio.channel_layout != profile.channel_layout
    ):
        raise OutputInvalid("Output audio profile does not match the planned source layout")
    if abs(audio.duration - profile.expected_duration) > tolerance + Fraction(
        2048, audio.sample_rate
    ):
        raise OutputInvalid("Output audio duration differs from the planned timeline")
    if abs(info.start_time) > tolerance or abs(audio.start_time) > tolerance:
        raise OutputInvalid("Output start timestamps are not normalized")


def validate_output(
    candidate: Path, plan: MediaPlan, *, tools: MediaTools, cancel: Event | None = None
) -> OutputManifest:
    if cancel is not None and cancel.is_set():
        raise OutputInvalid("Output validation canceled")

    def probe(args):
        return tools.probe(args, cancel)

    assert_no_reparse(candidate)
    if candidate == plan.source.path or candidate.samefile(plan.source.path):
        raise OutputInvalid("The source cannot be treated as a newly processed output")
    info = probe_media(candidate, probe)
    validate_metadata(info, plan)
    frames = scan_frame_geometry(info, probe)
    if abs(frames - round(Fraction(plan.expected_duration_ms * 30, 1000))) > 2:
        raise OutputInvalid("Decoded video frame count differs from the planned timeline")
    result = tools.ffmpeg(
        [
            "-hide_banner",
            "-v",
            "error",
            "-xerror",
            "-nostdin",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(candidate),
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
    if result.exit_code or result.stderr.strip() or result.timed_out or result.canceled:
        raise OutputInvalid("The complete video/audio decode failed")
    if sha256_file(candidate) != info.sha256:
        raise OutputInvalid("Candidate changed during verification")
    return OutputManifest(
        path=candidate,
        sha256=info.sha256,
        size_bytes=candidate.stat().st_size,
        media_info=info,
        plan_digest=plan.digest,
        verified_at=datetime.now(UTC),
    )


def promote_output(manifest: OutputManifest, destination: Path) -> OutputManifest:
    if not destination.is_absolute() or destination.suffix.lower() != ".mp4":
        raise OutputInvalid("Choose an absolute MP4 output path")
    assert_no_reparse(manifest.path)
    assert_no_reparse(destination)
    if destination.exists():
        raise OutputInvalid("Existing files are never overwritten")
    if destination.anchor.casefold() != manifest.path.anchor.casefold():
        raise OutputInvalid("Atomic output promotion requires the same volume")
    if sha256_file(manifest.path) != manifest.sha256:
        raise OutputInvalid("Validated output changed before promotion")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.rename(manifest.path, destination)
    return manifest.model_copy(
        update={
            "path": destination,
            "media_info": manifest.media_info.model_copy(update={"path": destination}),
        }
    )
