"""Compile literal timed text/image layers with safe relative resource filenames."""

import re
from fractions import Fraction

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field

from fabu.assets import AssetRef, assert_no_reparse, sha256_file
from fabu.media.filters_time import seconds
from fabu.media.geometry import Size
from fabu.media.models import PlanInvalid
from fabu.media.registry import ImageParams, SubtitleParams, TextParams
from fabu.media.subtitles import parse_srt, validate_font
from fabu.presets import StepSpec


class LayerResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    graph: str
    image_inputs: list[AssetRef] = Field(default_factory=list)
    text_files: dict[str, str] = Field(default_factory=dict)
    font_copies: dict[str, str] = Field(default_factory=dict)
    summary: list[dict] = Field(default_factory=list)


def compile_layers(
    steps: list[StepSpec],
    canvas: Size,
    scope_duration_ms: int,
    assets: dict[str, AssetRef],
    *,
    input_label: str = "layer_in",
    output_label: str = "vout",
    input_offset: int = 1,
    prefix: str = "",
) -> LayerResult:
    if (
        not re.fullmatch(r"[a-zA-Z0-9_]*", prefix)
        or not re.fullmatch(r"[a-zA-Z0-9_]+", input_label)
        or not re.fullmatch(r"[a-zA-Z0-9_]+", output_label)
        or input_offset < 1
    ):
        raise PlanInvalid("Unsafe internal graph namespace")
    graph = []
    images = []
    texts = {}
    fonts = {}
    summary = []
    current = input_label
    count = 0

    def check_asset(identifier, kind):
        asset = assets.get(identifier)
        if not asset or asset.media_type != kind:
            raise PlanInvalid("Layer asset type is invalid")
        assert_no_reparse(asset.local_path)
        if sha256_file(asset.local_path) != asset.sha256:
            raise PlanInvalid("Layer asset integrity failed")
        return asset

    def add_text(text, font_id, font_size, color, opacity, x, y, start, end):
        nonlocal count, current
        if re.search(r"[\x00-\x09\x0b-\x1f\x7f]", text):
            raise PlanInvalid("Text contains unsupported control characters")
        font = check_asset(font_id, "font")
        validate_font(font.local_path, text)
        face = ImageFont.truetype(str(font.local_path), font_size)
        box = ImageDraw.Draw(Image.new("L", (1, 1))).multiline_textbbox((0, 0), text, font=face)
        if box[2] - box[0] > canvas.width or box[3] - box[1] > canvas.height:
            raise PlanInvalid("Text does not fit in the selected canvas; reduce font size")
        name = f"{prefix}text-{count}.txt"
        font_name = next((name for name, fid in fonts.items() if fid == font_id), None)
        if font_name is None:
            font_name = f"{prefix}font-{len(fonts)}{font.local_path.suffix.lower()}"
        texts[name] = text
        fonts[font_name] = font_id
        label = f"{prefix}layer{count}"
        enable = f"gte(t,{seconds(Fraction(start, 1000))})*lt(t,{seconds(Fraction(end, 1000))})"
        graph.append(
            f"[{current}]drawtext=fontfile={font_name}:textfile={name}:expansion=none:"
            f"fontsize={font_size}:fontcolor=0x{color[1:]}@{opacity}:x=(w-text_w)*{x}:"
            f"y=(h-text_h)*{y}:enable='{enable}'[{label}]"
        )
        current = label
        count += 1

    for step in steps:
        if not step.enabled:
            continue
        boundaries = []
        if step.kind == "image_layer":
            p = ImageParams.model_validate(step.params)
            if len(step.asset_ids) != 1:
                raise PlanInvalid("Select one image for a layer")
            asset = check_asset(step.asset_ids[0], "image")
            end = scope_duration_ms if p.end_ms is None else p.end_ms
            if end > scope_duration_ms or p.start_ms >= end:
                raise PlanInvalid("Layer timing exceeds scope")
            boundaries = [p.start_ms, end]
            with Image.open(asset.local_path) as image:
                width = int(canvas.width * p.width) // 2 * 2
                height = round(width * image.height / image.width / 2) * 2
            if width < 2 or height < 2 or height > canvas.height:
                raise PlanInvalid("Image layer exceeds canvas")
            index = input_offset + len(images)
            images.append(asset)
            picture = f"{prefix}image{count}"
            graph.append(
                f"[{index}:v]scale={width}:{height}:flags=lanczos,format=rgba,"
                f"colorchannelmixer=aa={p.opacity}[{picture}]"
            )
            label = f"{prefix}layer{count}"
            enable = (
                f"gte(t,{seconds(Fraction(p.start_ms, 1000))})*lt(t,{seconds(Fraction(end, 1000))})"
            )
            graph.append(
                f"[{current}][{picture}]overlay=x=(main_w-overlay_w)*{p.x}:"
                f"y=(main_h-overlay_h)*{p.y}:shortest=1:eof_action=repeat:"
                f"enable='{enable}'[{label}]"
            )
            current = label
            count += 1
        elif step.kind == "text_layer":
            p = TextParams.model_validate(step.params)
            end = scope_duration_ms if p.end_ms is None else p.end_ms
            if end > scope_duration_ms or p.start_ms >= end:
                raise PlanInvalid("Text timing exceeds scope")
            boundaries = [p.start_ms, end]
            add_text(p.text, p.font_id, p.font_size, p.color, p.opacity, p.x, p.y, p.start_ms, end)
        elif step.kind == "subtitles":
            p = SubtitleParams.model_validate(step.params)
            subtitle = check_asset(p.subtitle_id, "subtitle")
            cues = parse_srt(subtitle.local_path, scope_duration_ms)
            boundaries = [cues[0].start_ms, cues[-1].end_ms]
            for cue in cues:
                add_text(
                    cue.text,
                    p.font_id,
                    p.font_size,
                    p.color,
                    1,
                    0.5,
                    0.95 if p.position == "bottom" else 0.05,
                    cue.start_ms,
                    cue.end_ms,
                )
        else:
            raise PlanInvalid("Unrecognized layer operation")
        summary.append(
            {
                "step_id": step.id,
                "kind": step.kind,
                "scope": step.scope,
                "boundaries_ms": boundaries,
            }
        )
    graph.append(f"[{current}]null[{output_label}]")
    return LayerResult(
        graph=";".join(graph),
        image_inputs=images,
        text_files=texts,
        font_copies=fonts,
        summary=summary,
    )
