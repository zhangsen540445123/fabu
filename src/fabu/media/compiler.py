"""Pure compilation; unsupported enabled operations are rejected, never silently skipped."""

import hashlib
import json
from fractions import Fraction

from PIL import Image

from fabu.assets import assert_no_reparse, sha256_file
from fabu.media.baseline import build_baseline_plan
from fabu.media.filters_geometry import compile_geometry, contain_size
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

    if any(step.kind not in {"crop", "flip", "color", "layout"} for step in active):
        raise PlanInvalid(
            "Enabled editing modules are not yet available in this implementation stage"
        )
    plan = build_baseline_plan(bundle.source)
    asset_hashes = {key: bundle.assets[key].sha256 for key in sorted(used_assets)}
    for key in used_assets:
        asset = bundle.assets[key]
        assert_no_reparse(asset.local_path)
        if asset.media_type != "image" or sha256_file(asset.local_path) != asset.sha256:
            raise PlanInvalid("Decoration asset type or integrity is invalid")
    if active:
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
        geometry = compile_geometry(active, Size(width=sw, height=sh))
        finish = [
            "setsar=1",
            "fps=30",
            "format=yuv420p",
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        ]
        # Range conversion is an actual scale conversion, not merely changing the output tag.
        if source.color.range == "pc":
            initial.append("scale=in_range=pc:out_range=tv")
        filters = initial + geometry.video_filters
        graph = None
        asset_inputs = []
        if geometry.decorations:
            graph_parts = [f"[0:{source.video_stream_index}]" + ",".join(filters) + "[main0]"]
            for index, decoration in enumerate(geometry.decorations):
                asset = bundle.assets[decoration.asset_id]
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
    plan = plan.model_copy(
        update={
            "output_profile": profile,
            "asset_hashes": asset_hashes,
            "preset_digest": semantic_fingerprint(preset, asset_hashes, ENGINE_VERSION),
        }
    )
    return plan.model_copy(update={"digest": plan_digest(plan)})
