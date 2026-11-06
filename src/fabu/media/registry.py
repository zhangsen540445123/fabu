"""Finite built-in step schemas and ordering constraints; never executable user scripts."""

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from fabu.presets import StepSpec


class ValidationIssue(BaseModel):
    step_id: str
    code: str
    message: str


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TrimParams(Params):
    start_ms: int = Field(default=0, ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def interval(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("Trim interval must be positive")
        return self


class CropParams(Params):
    x: float = Field(default=0, ge=0, lt=1)
    y: float = Field(default=0, ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def bounds(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("Crop exceeds current frame")
        return self


class ColorParams(Params):
    brightness: float = Field(default=0, ge=-1, le=1)
    contrast: float = Field(default=1, ge=0, le=2)
    saturation: float = Field(default=1, ge=0, le=3)


class SpeedParams(Params):
    rate: float = Field(default=1, ge=0.5, le=2)


class LayoutParams(Params):
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    top_fraction: float = Field(default=0, ge=0, le=0.4)
    bottom_fraction: float = Field(default=0, ge=0, le=0.4)
    top_image_id: str | None = None
    bottom_image_id: str | None = None
    background: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")

    @model_validator(mode="after")
    def image_bands(self):
        if (self.top_image_id and self.top_fraction <= 0) or (
            self.bottom_image_id and self.bottom_fraction <= 0
        ):
            raise ValueError("Decoration images require a nonempty reserved band")
        return self


class LayerParams(Params):
    x: float = Field(default=0, ge=0, le=1)
    y: float = Field(default=0, ge=0, le=1)
    opacity: float = Field(default=1, ge=0, le=1)
    start_ms: int = Field(default=0, ge=0)
    end_ms: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def interval(self):
        if self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("Layer interval invalid")
        return self


class ImageParams(LayerParams):
    width: float = Field(default=0.2, gt=0, le=1)


class TextParams(LayerParams):
    text: str = Field(min_length=1, max_length=1000)
    font_id: str
    font_size: int = Field(default=36, ge=8, le=200)
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")


class SubtitleParams(Params):
    subtitle_id: str
    font_id: str
    font_size: int = Field(default=36, ge=8, le=200)
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    position: Literal["bottom", "top"] = "bottom"


class SegmentParams(Params):
    duration_ms: int | None = Field(default=None, ge=100, le=60000)
    keep_audio: bool = True
    fit: Literal["reject", "contain"] = "reject"


class AudioParams(Params):
    gain_db: float = Field(default=0, ge=-60, le=24)
    fade_in_ms: int = Field(default=0, ge=0)
    fade_out_ms: int = Field(default=0, ge=0)


PARAM_SCHEMAS = {
    "trim": TrimParams,
    "crop": CropParams,
    "flip": Params,
    "color": ColorParams,
    "speed": SpeedParams,
    "layout": LayoutParams,
    "image_layer": ImageParams,
    "text_layer": TextParams,
    "subtitles": SubtitleParams,
    "intro": SegmentParams,
    "outro": SegmentParams,
    "audio_effects": AudioParams,
}
_MAIN_ONLY = {"trim", "crop", "flip", "color", "speed", "layout"}
_MULTIPLE = {"image_layer", "text_layer", "subtitles"}
_BASE_PHASE = {
    "trim": 0,
    "speed": 1,
    "crop": 2,
    "flip": 2,
    "color": 2,
    "layout": 3,
    "intro": 5,
    "outro": 5,
}


def step_phase(step: StepSpec) -> int:
    return _BASE_PHASE.get(step.kind, 4 if step.scope == "main" else 6)


def validate_steps(steps: list[StepSpec]) -> list[ValidationIssue]:
    errors = []
    previous = -1
    counts = Counter()
    identifiers = set()

    def issue(step, code, message):
        errors.append(ValidationIssue(step_id=step.id, code=code, message=message))

    for step in steps:
        if step.id in identifiers:
            issue(step, "duplicate_id", "Step IDs must be unique")
        identifiers.add(step.id)
        schema = PARAM_SCHEMAS.get(step.kind)
        if schema is None:
            issue(step, "unknown_step", "Only registered built-in editing steps are allowed")
            continue
        if not step.enabled:
            continue
        try:
            params = schema.model_validate(step.params)
        except ValidationError:
            issue(
                step, "invalid_parameters", "Invalid parameters; check ranges and required fields"
            )
            continue
        if step.kind in _MAIN_ONLY and step.scope != "main":
            issue(step, "invalid_scope", "This operation can only affect the main video")
        if step.kind in {"intro", "outro"} and step.scope != "full":
            issue(step, "invalid_scope", "Intro/outro belong to full-film composition")
        key = (step.kind, step.scope if step.kind == "audio_effects" else "")
        counts[key] += 1
        if step.kind not in _MULTIPLE and counts[key] > 1:
            issue(step, "duplicate_step", "This editing operation can only be configured once")
        phase = step_phase(step)
        if phase < previous:
            issue(step, "invalid_order", "Step order violates media stage boundaries")
        previous = max(previous, phase)
        expected = None
        if step.kind in {"image_layer", "intro", "outro"}:
            if len(step.asset_ids) != 1:
                issue(step, "missing_asset", "Select exactly one local asset")
        elif step.kind == "text_layer":
            expected = {params.font_id}
        elif step.kind == "subtitles":
            expected = {params.font_id, params.subtitle_id}
        elif step.kind == "layout":
            expected = {v for v in (params.top_image_id, params.bottom_image_id) if v}
        else:
            expected = set()
        if len(set(step.asset_ids)) != len(step.asset_ids):
            issue(step, "duplicate_asset", "Duplicate asset reference")
        if expected is not None and set(step.asset_ids) != expected:
            issue(
                step, "asset_mismatch", "Parameter assets must match the explicit asset references"
            )
    return errors
