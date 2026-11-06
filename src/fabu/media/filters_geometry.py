"""Explicit frame editing in normalized display coordinates, followed by one output scale."""

from fractions import Fraction

from pydantic import BaseModel, ConfigDict, Field

from fabu.media.geometry import Size
from fabu.media.models import PlanInvalid
from fabu.media.registry import ColorParams, CropParams, LayoutParams
from fabu.presets import StepSpec


class Decoration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    asset_id: str
    width: int
    height: int
    x: int
    y: int
    background: str


class GeometryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    video_filters: list[str]
    output_size: Size
    subject_size: Size
    subject_offset: tuple[int, int] = (0, 0)
    coordinate_map: list[dict] = Field(default_factory=list)
    decorations: list[Decoration] = Field(default_factory=list)


def _even_floor(number: Fraction | float | int) -> int:
    return int(number // 2) * 2


def contain_size(ratio: Fraction, width: int, height: int) -> Size:
    h = min(Fraction(height), Fraction(width) / ratio)
    w = h * ratio
    if min(w, h) < 2:
        raise PlanInvalid("Frame region is too small to preserve even output geometry")
    return Size(width=_even_floor(w), height=_even_floor(h))


def compile_geometry(steps: list[StepSpec], input_size: Size) -> GeometryResult:
    w, h = input_size.width, input_size.height
    filters = []
    coordinates = []
    decorations = []
    layout = None
    for step in steps:
        if not step.enabled:
            continue
        if step.kind == "crop":
            p = CropParams.model_validate(step.params)
            x, y = _even_floor(Fraction(str(p.x)) * w), _even_floor(Fraction(str(p.y)) * h)
            cw, ch = (
                _even_floor(Fraction(str(p.width)) * w),
                _even_floor(Fraction(str(p.height)) * h),
            )
            if min(cw, ch) < 2 or x + cw > w or y + ch > h:
                raise PlanInvalid("Crop rounds to an empty or out-of-bounds region")
            coordinates.append({"step_id": step.id, "input": [w, h], "crop": [cw, ch, x, y]})
            filters.append(f"crop={cw}:{ch}:{x}:{y}")
            w, h = cw, ch
        elif step.kind == "flip":
            filters.append("hflip")
            coordinates.append(
                {"step_id": step.id, "input": [w, h], "transform": "horizontal_flip"}
            )
        elif step.kind == "color":
            p = ColorParams.model_validate(step.params)
            if (p.brightness, p.contrast, p.saturation) != (0, 1, 1):
                filters.append(
                    f"eq=brightness={p.brightness}:contrast={p.contrast}:saturation={p.saturation}"
                )
        elif step.kind == "layout":
            layout = LayoutParams.model_validate(step.params)
        else:
            raise PlanInvalid("Geometry stage received a non-geometry operation")
    ratio = Fraction(w, h)
    offset = (0, 0)
    if layout:
        width, height = {"16:9": (2560, 1440), "9:16": (1440, 2560), "1:1": (1440, 1440)}[
            layout.aspect
        ]
        top, bottom = (
            _even_floor(Fraction(str(layout.top_fraction)) * height),
            _even_floor(Fraction(str(layout.bottom_fraction)) * height),
        )
        available = height - top - bottom
        subject = contain_size(ratio, width, available)
        offset = (
            _even_floor((width - subject.width) / 2),
            top + _even_floor((available - subject.height) / 2),
        )
        filters.append(f"scale={subject.width}:{subject.height}:flags=lanczos")
        filters.append(
            f"pad={width}:{height}:{offset[0]}:{offset[1]}:color=0x{layout.background[1:]}"
        )
        for asset_id, band, y in [
            (layout.top_image_id, top, 0),
            (layout.bottom_image_id, bottom, height - bottom),
        ]:
            if asset_id:
                if band < 2:
                    raise PlanInvalid("Decoration image requires a nonempty reserved band")
                decorations.append(
                    Decoration(
                        asset_id=asset_id,
                        width=width,
                        height=band,
                        x=0,
                        y=y,
                        background=layout.background,
                    )
                )
        output = Size(width=width, height=height)
    else:
        bound_w, bound_h = (2560, 1440) if ratio > 1 else (1440, 2560)
        if ratio == 1:
            bound_w, bound_h = 1440, 1440
        subject = contain_size(ratio, bound_w, bound_h)
        output = subject
        filters.append(f"scale={output.width}:{output.height}:flags=lanczos")
    return GeometryResult(
        video_filters=filters,
        output_size=output,
        subject_size=subject,
        subject_offset=offset,
        coordinate_map=coordinates,
        decorations=decorations,
    )
