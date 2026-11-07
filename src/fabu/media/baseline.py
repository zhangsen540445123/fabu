"""The accepted CPU CRF20 baseline; filters precede one final video/audio encode."""

from fractions import Fraction
from pathlib import Path

from fabu.media.geometry import Size, fit_output
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


def source_video_filters(source: MediaInfo, size: Size) -> list[str]:
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
    return filters


def source_audio_filters(source: MediaInfo) -> list[str]:
    audio = source.audio
    offset = audio.start_time - source.start_time
    audio_filters = ["asetpts=PTS-STARTPTS"]
    if offset < 0:
        audio_filters.extend([f"atrim=start={float(-offset):.9f}", "asetpts=PTS-STARTPTS"])
    elif offset > 0:
        # Preserve a real source audio offset rather than silently moving it to video t=0.
        audio_filters.append(f"adelay={round(offset * audio.sample_rate)}S:all=1")
    return audio_filters


def build_baseline_plan(source: MediaInfo) -> MediaPlan:
    validate_supported_input(source)
    size = fit_output(source)
    filters = source_video_filters(source, size)
    audio_filters = source_audio_filters(source)
    audio = source.audio
    offset = audio.start_time - source.start_time
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


def build_render_command(
    plan: MediaPlan, candidate: Path, progress: Path, *, graph_path: Path | None = None
) -> RenderCommand:
    verify_plan_digest(plan)
    from fabu.media.encoders import encoder_args

    encoding = encoder_args(plan)
    video_filters = [f for stage in plan.stages for f in stage.video_filters]
    audio_filters = [f for stage in plan.stages for f in stage.audio_filters]
    profile = plan.output_profile
    audio = plan.audio_profile

    inputs = [
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
    ]
    for asset in plan.asset_inputs:
        if asset.media_type == "image":
            inputs.extend(["-loop", "1", "-framerate", "30"])
        elif asset.media_type == "video":
            inputs.extend(["-noautorotate", "-display_rotation", "0"])
        else:
            raise ValueError("Unsupported render input type")
        inputs.extend(["-protocol_whitelist", "file,pipe", "-i", str(asset.local_path)])
    if plan.video_graph:
        processing = [
            "-filter_complex_threads",
            "2",
            "-/filter_complex" if graph_path else "-filter_complex",
            str(graph_path) if graph_path else plan.video_graph,
            "-map",
            "[vout]",
        ]
    else:
        processing = ["-map", f"0:{plan.source.video_stream_index}", "-vf", ",".join(video_filters)]
    if plan.complex_audio:
        processing += ["-map", "[aout]"]
    else:
        processing += [
            "-map",
            f"0:{plan.source.audio.stream_index}",
            "-af",
            ",".join(audio_filters),
        ]
    output = encoding + [
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
    return RenderCommand(args=inputs + processing + output)
