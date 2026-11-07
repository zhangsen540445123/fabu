"""Immutable media planning and independently verified output records."""

import hashlib
import json
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fabu.assets import AssetRef
from fabu.media.geometry import Size
from fabu.media.probe import MediaInfo

ENGINE_VERSION = "pipeline-5"


class PlanInvalid(ValueError):
    pass


class AccelerationOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["cpu", "auto", "gpu"] = "auto"
    quality: Literal["balanced"] = "balanced"


class ExecutionProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    path: Literal["cpu", "mixed"]
    encoder: Literal["libx265", "hevc_nvenc"]
    reason: Literal[
        "cpu_requested",
        "nvenc_verified",
        "driver_incompatible",
        "no_device",
        "encoder_unavailable",
        "probe_failed",
        "inventory_unavailable",
    ]
    device_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    device_name: str | None = Field(default=None, max_length=200)
    gpu_index: Literal[0] | None = None
    nvenc_preset: Literal["p5"] | None = None
    cq: Literal[20] | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.path == "cpu":
            if (
                self.encoder != "libx265"
                or self.reason == "nvenc_verified"
                or any(
                    v is not None
                    for v in (
                        self.device_fingerprint,
                        self.device_name,
                        self.gpu_index,
                        self.nvenc_preset,
                        self.cq,
                    )
                )
            ):
                raise ValueError("CPU execution cannot contain GPU parameters")
        elif (
            self.encoder != "hevc_nvenc"
            or self.reason != "nvenc_verified"
            or not self.device_fingerprint
            or self.gpu_index != 0
            or self.nvenc_preset != "p5"
            or self.cq != 20
        ):
            raise ValueError(
                "GPU execution requires verified device and explicit quality parameters"
            )
        return self


class OutputProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    acceleration: AccelerationOptions | None = Field(default=None, exclude_if=lambda v: v is None)
    codec: Literal["libx265"] = "libx265"
    preset: Literal["fast"] = "fast"
    crf: int = Field(default=20, ge=0, le=51)
    fps: Literal[30] = 30
    pixel_format: Literal["yuv420p"] = "yuv420p"
    size_policy: Literal["auto"] = "auto"


class AudioProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    sample_rate: Literal[44100, 48000]
    channels: Literal[1, 2]
    channel_layout: Literal["mono", "stereo"]
    bitrate_kbps: Literal[128, 192]
    expected_duration: Fraction


class RenderStage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: str
    video_filters: list[str]
    audio_filters: list[str]


class MediaPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    digest: str
    engine_version: str = ENGINE_VERSION
    tool_identity: dict[str, str] = Field(default_factory=dict)
    execution: ExecutionProfile | None = Field(default=None, exclude_if=lambda v: v is None)
    source: MediaInfo
    source_hash: str
    preset_digest: str | None = None
    stages: list[RenderStage]
    output_size: Size
    expected_duration_ms: int
    audio_profile: AudioProfile
    output_profile: OutputProfile
    asset_hashes: dict[str, str] = Field(default_factory=dict)
    asset_inputs: list[AssetRef] = Field(default_factory=list)
    asset_refs: dict[str, AssetRef] = Field(default_factory=dict)
    text_files: dict[str, str] = Field(default_factory=dict)
    font_copies: dict[str, str] = Field(default_factory=dict)
    layer_summary: list[dict] = Field(default_factory=list)
    composition_summary: list[dict] = Field(default_factory=list)
    video_inputs: dict[str, MediaInfo] = Field(default_factory=dict)
    complex_audio: bool = False
    video_graph: str | None = None
    geometry_summary: dict = Field(default_factory=dict)
    timing_summary: dict = Field(default_factory=dict)


class RenderCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    args: list[str]
    auxiliary_files: list[Path] = Field(default_factory=list)


class OutputManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: Path
    sha256: str
    size_bytes: int
    media_info: MediaInfo
    plan_digest: str
    verified_at: datetime
    timings: dict[str, float] = Field(default_factory=dict, exclude_if=lambda v: not v)


def plan_digest(plan: MediaPlan) -> str:
    data = plan.model_dump(mode="json", exclude={"digest"})
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def verify_plan_digest(plan: MediaPlan) -> None:
    if plan.source_hash != plan.source.sha256 or plan.digest != plan_digest(plan):
        raise PlanInvalid("Media plan or input identity changed after compilation")


class InputBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source: MediaInfo
    assets: dict[str, AssetRef] = Field(default_factory=dict)
    asset_media: dict[str, MediaInfo] = Field(default_factory=dict)


def compatibility_version(plan: MediaPlan) -> str:
    base = tool_compatibility(plan.tool_identity, plan.engine_version)
    if plan.execution is None:
        return base
    digest = hashlib.sha256(
        json.dumps(
            plan.execution.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return base + ":execution-1:" + digest


def tool_compatibility(identity: dict[str, str], engine=ENGINE_VERSION) -> str:
    if not identity:
        return engine
    if set(identity) != {"ffmpeg", "ffprobe"} or any(
        len(v) != 64 or any(c not in "0123456789abcdef" for c in v) for v in identity.values()
    ):
        raise PlanInvalid("Media tool fingerprint is invalid")
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return engine + ":" + digest
