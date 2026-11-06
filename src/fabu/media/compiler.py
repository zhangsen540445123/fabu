"""Pure compilation; unsupported enabled operations are rejected, never silently skipped."""

import hashlib
import json
from fractions import Fraction

from PIL import Image

from fabu.assets import assert_no_reparse, sha256_file
from fabu.media.baseline import build_baseline_plan
from fabu.media.composition import compose_segments
from fabu.media.filters_geometry import compile_geometry, contain_size
from fabu.media.filters_layers import compile_layers
from fabu.media.filters_time import compile_timing
from fabu.media.geometry import Size
from fabu.media.models import (
    ENGINE_VERSION,
    InputBundle,
    MediaPlan,
    OutputProfile,
    PlanInvalid,
    RenderStage,
    plan_digest,
)
from fabu.media.registry import validate_steps
from fabu.presets import PresetVersion


def semantic_fingerprint(
    preset: PresetVersion, asset_hashes: dict[str, str], engine_version: str
) -> str:
    data = {
        "steps": [s.model_dump(mode="json", exclude={"id"}) for s in preset.steps if s.enabled],
        "output": OutputProfile.model_validate(preset.output).model_dump(mode="json"),
        "assets": asset_hashes,
        "engine": engine_version,
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def compile_plan(bundle: InputBundle, preset: PresetVersion) -> MediaPlan:
    errors = validate_steps(preset.steps)
    if errors:
        raise PlanInvalid("; ".join(f"{e.step_id}: {e.code}" for e in errors))
    profile = OutputProfile.model_validate(preset.output)
    active = [step for step in preset.steps if step.enabled]
    used_assets = {key for step in active for key in step.asset_ids}
    if not used_assets.issubset(bundle.assets):
        raise PlanInvalid("A required immutable asset is missing")

    if any(
        step.kind
        not in {
            "crop",
            "flip",
            "color",
            "layout",
            "trim",
            "speed",
            "audio_effects",
            "image_layer",
            "text_layer",
            "subtitles",
            "intro",
            "outro",
        }
        for step in active
    ):
        raise PlanInvalid(
            "Enabled editing modules are not yet available in this implementation stage"
        )
    plan = build_baseline_plan(bundle.source)
    asset_hashes = {key: bundle.assets[key].sha256 for key in sorted(used_assets)}
    for key in used_assets:
        asset = bundle.assets[key]
        assert_no_reparse(asset.local_path)
        if sha256_file(asset.local_path) != asset.sha256:
            raise PlanInvalid("Asset integrity is invalid")
    timing = compile_timing(bundle.source, [s for s in active if s.scope == "main"])
    geometry_steps = [step for step in active if step.kind in {"crop", "flip", "color", "layout"}]
    if geometry_steps:
        source = bundle.source
        # Convert non-square source pixels before applying user crop coordinates.
        sw = round(Fraction(source.coded_width) * source.sar / 2) * 2
        sh = source.coded_height
        initial = ["setpts=PTS-STARTPTS"]
        if source.sar != 1:
            initial.extend([f"scale={sw}:{sh}:flags=lanczos", "setsar=1"])
        else:
            sw = source.coded_width
        if source.rotation_deg == 90:
            initial.append("transpose=cclock")
        elif source.rotation_deg == 180:
            initial.extend(["hflip", "vflip"])
        elif source.rotation_deg == 270:
            initial.append("transpose=clock")
        if source.rotation_deg in {90, 270}:
            sw, sh = sh, sw
        geometry = compile_geometry(geometry_steps, Size(width=sw, height=sh))
        finish = [
            "setsar=1",
            "fps=30",
            "format=yuv420p",
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        ]
        # Range conversion is an actual scale conversion, not merely changing the output tag.
        if source.color.range == "pc":
            initial.append("scale=in_range=pc:out_range=tv")
        filters = initial + timing.video_filters + geometry.video_filters
        graph = None
        asset_inputs = []
        if geometry.decorations:
            graph_parts = [f"[0:{source.video_stream_index}]" + ",".join(filters) + "[main0]"]
            for index, decoration in enumerate(geometry.decorations):
                asset = bundle.assets[decoration.asset_id]
                if asset.media_type != "image":
                    raise PlanInvalid("Decoration asset type is invalid")
                asset_inputs.append(asset)
                with Image.open(asset.local_path) as image:
                    size = contain_size(
                        Fraction(image.width, image.height), decoration.width, decoration.height
                    )
                padding = (
                    f"pad={decoration.width}:{decoration.height}:(ow-iw)/2:(oh-ih)/2:"
                    f"color=0x{decoration.background[1:]}"
                )
                graph_parts.append(
                    f"[{index + 1}:v]scale={size.width}:{size.height}:flags=lanczos,"
                    + padding
                    + f",setsar=1[decor{index}]"
                )
                graph_parts.append(
                    f"[main{index}][decor{index}]overlay=x={decoration.x}:y={decoration.y}"
                    + f":shortest=1:eof_action=repeat[main{index + 1}]"
                )
            graph_parts.append(f"[main{len(asset_inputs)}]" + ",".join(finish) + "[vout]")
            graph = ";".join(graph_parts)
        stage = RenderStage(
            kind="geometry",
            video_filters=filters + finish,
            audio_filters=plan.stages[0].audio_filters,
        )
        plan = plan.model_copy(
            update={
                "stages": [stage],
                "output_size": geometry.output_size,
                "asset_inputs": asset_inputs,
                "video_graph": graph,
                "geometry_summary": geometry.model_dump(mode="json"),
            }
        )
    if active:
        stage = plan.stages[0]
        video_filters = stage.video_filters
        if not geometry_steps:
            video_filters = stage.video_filters[:1] + timing.video_filters + stage.video_filters[1:]
        stage = stage.model_copy(
            update={
                "video_filters": video_filters,
                "audio_filters": stage.audio_filters
                + timing.audio_filters
                + timing.main_audio_effects
                + timing.full_audio_effects,
            }
        )
        plan = plan.model_copy(
            update={
                "stages": [stage],
                "expected_duration_ms": timing.main_duration_ms,
                "audio_profile": plan.audio_profile.model_copy(
                    update={"expected_duration": timing.audio_duration}
                ),
                "timing_summary": timing.model_dump(mode="json"),
            }
        )
    layer_steps = [
        s
        for s in active
        if s.scope == "main" and s.kind in {"image_layer", "text_layer", "subtitles"}
    ]
    if layer_steps:
        layers = compile_layers(
            layer_steps,
            plan.output_size,
            plan.expected_duration_ms,
            bundle.assets,
            input_offset=1 + len(plan.asset_inputs),
        )
        if plan.video_graph:
            graph = plan.video_graph.removesuffix("[vout]") + "[layer_in]"
        else:
            filters = [f for stage in plan.stages for f in stage.video_filters]
            graph = f"[0:{plan.source.video_stream_index}]" + ",".join(filters) + "[layer_in]"
        plan = plan.model_copy(
            update={
                "video_graph": graph + ";" + layers.graph,
                "asset_inputs": plan.asset_inputs + layers.image_inputs,
                "text_files": layers.text_files,
                "font_copies": layers.font_copies,
                "layer_summary": layers.summary,
            }
        )
    plan = plan.model_copy(
        update={
            "asset_refs": {key: bundle.assets[key] for key in sorted(used_assets)},
            "output_profile": profile,
            "asset_hashes": asset_hashes,
            "preset_digest": semantic_fingerprint(preset, asset_hashes, ENGINE_VERSION),
        }
    )
    segments = {s.kind: s for s in active if s.kind in {"intro", "outro"}}
    full_steps = [s for s in active if s.scope == "full" and s.kind not in {"intro", "outro"}]
    plan = plan.model_copy(update={"digest": plan_digest(plan)})
    plan = compose_segments(
        plan,
        bundle.assets[segments["intro"].asset_ids[0]] if "intro" in segments else None,
        bundle.assets[segments["outro"].asset_ids[0]] if "outro" in segments else None,
        {kind: s.params for kind, s in segments.items()},
        full_steps,
        assets=bundle.assets,
        asset_media=bundle.asset_media,
    )
    return plan.model_copy(update={"digest": plan_digest(plan)})
