"""Immutable media planning and independently verified output records."""

import hashlib
import json
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fabu.assets import AssetRef
from fabu.media.geometry import Size
from fabu.media.probe import MediaInfo

ENGINE_VERSION = "pipeline-5"


class PlanInvalid(ValueError):
    pass


class OutputProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
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
    return tool_compatibility(plan.tool_identity, plan.engine_version)


def tool_compatibility(identity: dict[str, str], engine=ENGINE_VERSION) -> str:
    if not identity:
        return engine
    if set(identity) != {"ffmpeg", "ffprobe"} or any(
        len(v) != 64 or any(c not in "0123456789abcdef" for c in v) for v in identity.values()
    ):
        raise PlanInvalid("Media tool fingerprint is invalid")
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return engine + ":" + digest
