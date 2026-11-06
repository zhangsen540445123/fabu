"""The accepted CPU CRF20 baseline; filters precede one final video/audio encode."""

from fractions import Fraction
from pathlib import Path

from fabu.media.geometry import fit_output
from fabu.media.models import (
    AudioProfile,
    MediaPlan,
    OutputProfile,
    RenderCommand,
    RenderStage,
    plan_digest,
    verify_plan_digest,
)
from fabu.media.probe import MediaInfo, validate_supported_input


def build_baseline_plan(source: MediaInfo) -> MediaPlan:
    validate_supported_input(source)
    size = fit_output(source)
    filters = ["setpts=PTS-STARTPTS"]
    if source.rotation_deg == 90:
        filters.append("transpose=cclock")
    elif source.rotation_deg == 180:
        filters.extend(["hflip", "vflip"])
    elif source.rotation_deg == 270:
        filters.append("transpose=clock")
    filters.extend(
        [
            f"scale={size.width}:{size.height}:flags=lanczos:in_range={source.color.range}:out_range=tv",
            "setsar=1",
            "fps=30",
            "format=yuv420p",
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        ]
    )
    audio = source.audio
    offset = audio.start_time - source.start_time
    audio_filters = ["asetpts=PTS-STARTPTS"]
    if offset < 0:
        audio_filters.extend([f"atrim=start={float(-offset):.9f}", "asetpts=PTS-STARTPTS"])
    elif offset > 0:
        # Preserve a real source audio offset rather than silently moving it to video t=0.
        audio_filters.append(f"adelay={round(offset * audio.sample_rate)}S:all=1")
    plan = MediaPlan(
        digest="",
        source=source,
        source_hash=source.sha256,
        stages=[RenderStage(kind="baseline", video_filters=filters, audio_filters=audio_filters)],
        output_size=size,
        expected_duration_ms=source.duration_ms,
        audio_profile=AudioProfile(
            sample_rate=audio.sample_rate,
            channels=audio.channels,
            channel_layout=audio.channel_layout,
            bitrate_kbps=192 if audio.channels == 2 else 128,
            expected_duration=max(Fraction(0), audio.duration + offset),
        ),
        output_profile=OutputProfile(),
    )
    return plan.model_copy(update={"digest": plan_digest(plan)})


def build_render_command(plan: MediaPlan, candidate: Path, progress: Path) -> RenderCommand:
    verify_plan_digest(plan)
    video_filters = [f for stage in plan.stages for f in stage.video_filters]
    audio_filters = [f for stage in plan.stages for f in stage.audio_filters]
    profile = plan.output_profile
    audio = plan.audio_profile
    return RenderCommand(
        args=[
            "-hide_banner",
            "-v",
            "error",
            "-nostdin",
            "-noautorotate",
            "-display_rotation",
            "0",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(plan.source.path),
            "-map",
            f"0:{plan.source.video_stream_index}",
            "-map",
            f"0:{plan.source.audio.stream_index}",
            "-vf",
            ",".join(video_filters),
            "-af",
            ",".join(audio_filters),
            "-c:v",
            profile.codec,
            "-preset",
            profile.preset,
            "-crf",
            str(profile.crf),
            "-x265-params",
            "pools=6:frame-threads=2:log-level=error",
            "-pix_fmt",
            profile.pixel_format,
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-c:a",
            "aac",
            "-b:a",
            f"{audio.bitrate_kbps}k",
            "-ar",
            str(audio.sample_rate),
            "-ac",
            str(audio.channels),
            "-movflags",
            "+faststart",
            "-progress",
            str(progress),
            "-stats_period",
            "0.5",
            "-n",
            str(candidate),
        ]
    )
