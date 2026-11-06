"""Hard-cut intro/main/outro in one graph; main effects never touch auxiliary segments."""

import math
from fractions import Fraction

from PIL import Image

from fabu.assets import AssetRef, assert_no_reparse, sha256_file
from fabu.media.baseline import source_audio_filters, source_video_filters
from fabu.media.filters_geometry import contain_size
from fabu.media.filters_layers import compile_layers
from fabu.media.filters_time import audio_effects, seconds
from fabu.media.models import MediaPlan, PlanInvalid, plan_digest, verify_plan_digest
from fabu.media.probe import MediaInfo, validate_supported_input
from fabu.media.registry import AudioParams, SegmentParams, validate_steps
from fabu.presets import StepSpec


def _frame_duration(duration: Fraction) -> Fraction:
    return Fraction(math.ceil(duration * 30), 30)


def _video_graph(plan: MediaPlan, output: str) -> str:
    if plan.video_graph:
        return plan.video_graph.removesuffix("[vout]") + f"[{output}]"
    filters = [f for stage in plan.stages for f in stage.video_filters]
    return f"[0:{plan.source.video_stream_index}]" + ",".join(filters) + f"[{output}]"


def _duration_audio(duration: Fraction, sample_rate: int, layout: str) -> list[str]:
    return [
        f"aresample={sample_rate}",
        f"aformat=sample_fmts=fltp:channel_layouts={layout}",
        f"apad=whole_dur={seconds(duration)}",
        f"atrim=duration={seconds(duration)}",
        "asetpts=PTS-STARTPTS",
    ]


def compose_segments(
    main: MediaPlan,
    intro: AssetRef | None,
    outro: AssetRef | None,
    params: dict,
    full_steps: list[StepSpec],
    *,
    assets: dict[str, AssetRef] | None = None,
    asset_media: dict[str, MediaInfo] | None = None,
) -> MediaPlan:
    verify_plan_digest(main)
    if validate_steps(full_steps) or any(
        s.enabled
        and (
            s.scope != "full"
            or s.kind not in {"image_layer", "text_layer", "subtitles", "audio_effects"}
        )
        for s in full_steps
    ):
        raise PlanInvalid("Composition accepts only valid full-scope layers/audio")
    assets = dict(assets or main.asset_refs)
    for ref in (intro, outro):
        if ref:
            if ref.id in assets and assets[ref.id] != ref:
                raise PlanInvalid("Conflicting auxiliary asset identity")
            assets[ref.id] = ref
    used = {s for step in full_steps if step.enabled for s in step.asset_ids}
    used.update(ref.id for ref in (intro, outro) if ref)
    if not used.issubset(assets):
        raise PlanInvalid("A required full-scope asset is missing")
    refs = {**main.asset_refs, **{key: assets[key] for key in used}}
    main = main.model_copy(
        update={"asset_refs": refs, "asset_hashes": {key: ref.sha256 for key, ref in refs.items()}}
    )
    asset_media = asset_media or {}
    plan = main
    duration = Fraction(main.timing_summary.get("main_duration", main.source.duration))
    if intro or outro:
        main_duration = _frame_duration(duration)
        size = main.output_size
        sr = main.audio_profile.sample_rate
        layout = main.audio_profile.channel_layout
        graph = [_video_graph(main, "main_preconcat")]
        graph.append(
            "[main_preconcat]tpad=stop_mode=clone:stop_duration=1,"
            f"trim=end_frame={int(main_duration * 30)},settb=AVTB,"
            "setpts=N/(30*TB)[segment_main_v]"
        )
        af = [f for stage in main.stages for f in stage.audio_filters]
        graph.append(
            f"[0:{main.source.audio.stream_index}]"
            + ",".join(af + _duration_audio(main_duration, sr, layout))
            + "[segment_main_a]"
        )
        inputs = list(main.asset_inputs)
        video_inputs = dict(main.video_inputs)
        segment_labels = []
        summary = []
        position = Fraction(0)
        for kind, asset in [("intro", intro), ("main", None), ("outro", outro)]:
            if kind == "main":
                part_duration = main_duration
            elif asset is None:
                continue
            else:
                assert_no_reparse(asset.local_path)
                if asset.sha256 != sha256_file(asset.local_path):
                    raise PlanInvalid("Auxiliary segment integrity failed")
                p = SegmentParams.model_validate(params.get(kind, {}))
                info = None
                if asset.media_type == "image":
                    if p.duration_ms is None:
                        raise PlanInvalid("An image segment needs an explicit duration")
                    exact_duration = Fraction(p.duration_ms, 1000)
                    with Image.open(asset.local_path) as image:
                        aspect = Fraction(image.width, image.height)
                elif asset.media_type == "video":
                    info = asset_media.get(asset.id)
                    if not info or info.path != asset.local_path or info.sha256 != asset.sha256:
                        raise PlanInvalid("A matching auxiliary video probe is required")
                    validate_supported_input(info, require_audio=False)
                    exact_duration = (
                        info.duration if p.duration_ms is None else Fraction(p.duration_ms, 1000)
                    )
                    if exact_duration > info.duration:
                        raise PlanInvalid("Auxiliary duration exceeds the available clip")
                    aspect = info.display_aspect
                    video_inputs[asset.id] = info
                else:
                    raise PlanInvalid("Intro/outro must be an image or local video")
                if exact_duration > 60 or exact_duration < Fraction(1, 10):
                    raise PlanInvalid("Auxiliary duration must be between 0.1 and 60 seconds")
                if aspect != Fraction(size.width, size.height) and p.fit != "contain":
                    raise PlanInvalid(
                        "Auxiliary aspect differs; explicitly select contain to add borders"
                    )
                part_duration = _frame_duration(exact_duration)
                fitted = contain_size(aspect, size.width, size.height)
                index = len(inputs) + 1
                inputs.append(asset)
                if info:
                    vf = source_video_filters(info, fitted)
                    stream = str(info.video_stream_index)
                else:
                    vf = [
                        "setpts=PTS-STARTPTS",
                        f"scale={fitted.width}:{fitted.height}:"
                        "flags=lanczos:out_color_matrix=bt709:out_range=tv",
                        "setsar=1",
                        "fps=30",
                        "format=yuv420p",
                    ]
                    stream = "v"
                vf += [
                    f"pad={size.width}:{size.height}:(ow-iw)/2:(oh-ih)/2:color=black",
                    "tpad=stop_mode=clone:stop_duration=1",
                    f"trim=end_frame={int(part_duration * 30)}",
                    "settb=AVTB",
                    "setpts=N/(30*TB)",
                    "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
                ]
                graph.append(f"[{index}:{stream}]" + ",".join(vf) + f"[segment_{kind}_v]")
                if info and info.audio and p.keep_audio:
                    af = source_audio_filters(info) + _duration_audio(part_duration, sr, layout)
                    graph.append(
                        f"[{index}:{info.audio.stream_index}]"
                        + ",".join(af)
                        + f"[segment_{kind}_a]"
                    )
                else:
                    graph.append(
                        f"anullsrc=r={sr}:cl={layout},atrim=duration={seconds(part_duration)},"
                        f"asetpts=PTS-STARTPTS[segment_{kind}_a]"
                    )
            summary.append(
                {
                    "kind": kind,
                    "start_ms": round(position * 1000),
                    "duration_ms": round(part_duration * 1000),
                    "duration": str(part_duration),
                    "asset_id": asset.id if asset else None,
                }
            )
            position += part_duration
            segment_labels.append(f"[segment_{kind}_v][segment_{kind}_a]")
        graph.append(
            "".join(segment_labels)
            + f"concat=n={len(segment_labels)}:v=1:a=1[composed_v][composed_a]"
        )
        graph.append("[composed_v]fps=30,setsar=1,format=yuv420p[vout]")
        graph.append("[composed_a]anull[aout]")
        duration = position
        plan = main.model_copy(
            update={
                "video_graph": ";".join(graph),
                "complex_audio": True,
                "asset_inputs": inputs,
                "video_inputs": video_inputs,
                "composition_summary": summary,
                "expected_duration_ms": round(duration * 1000),
                "audio_profile": main.audio_profile.model_copy(
                    update={"expected_duration": duration}
                ),
            }
        )
    layer_steps = [
        s for s in full_steps if s.enabled and s.kind in {"image_layer", "text_layer", "subtitles"}
    ]
    if layer_steps:
        layers = compile_layers(
            layer_steps,
            plan.output_size,
            round(duration * 1000),
            assets,
            input_label="full_in",
            input_offset=1 + len(plan.asset_inputs),
            prefix="full_",
        )
        # A combined graph can end in audio, so relabel the unique final video pad explicitly.
        graph = (
            plan.video_graph.replace("[vout]", "[full_in]")
            if plan.video_graph
            else _video_graph(plan, "full_in")
        )
        plan = plan.model_copy(
            update={
                "video_graph": graph + ";" + layers.graph,
                "asset_inputs": plan.asset_inputs + layers.image_inputs,
                "text_files": {**plan.text_files, **layers.text_files},
                "font_copies": {**plan.font_copies, **layers.font_copies},
                "layer_summary": plan.layer_summary + layers.summary,
            }
        )
    effects = []
    for step in full_steps:
        if step.enabled and step.kind == "audio_effects":
            effects += audio_effects(AudioParams.model_validate(step.params), duration)
    if effects:
        if plan.complex_audio:
            graph = plan.video_graph.replace("[aout]", "[full_audio_in]")
            graph += ";[full_audio_in]" + ",".join(effects) + "[aout]"
            plan = plan.model_copy(update={"video_graph": graph})
        else:
            stage = plan.stages[-1]
            plan = plan.model_copy(
                update={
                    "stages": plan.stages[:-1]
                    + [stage.model_copy(update={"audio_filters": stage.audio_filters + effects})]
                }
            )
    if full_steps:
        plan = plan.model_copy(
            update={"timing_summary": {**plan.timing_summary, "full_audio_effects": effects}}
        )
    return plan.model_copy(update={"digest": plan_digest(plan)})
