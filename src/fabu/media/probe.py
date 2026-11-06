"""Probe local media without guessing color, selecting hidden tracks, or dropping sound."""

import json
import re
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from fabu.assets import assert_no_reparse, sha256_file
from fabu.tools import CommandResult


class UnsupportedMedia(ValueError):
    """Safe user-facing input problem; raw tool output is never embedded."""


class ColorInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    space: str
    transfer: str
    primaries: str
    range: str


class AudioInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    stream_index: int
    codec: str
    sample_rate: int
    channels: int
    channel_layout: str
    duration: Fraction
    start_time: Fraction


class MediaInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: Path
    sha256: str
    duration: Fraction
    video_stream_index: int
    codec: str
    coded_width: int = Field(gt=0)
    coded_height: int = Field(gt=0)
    sar: Fraction
    rotation_deg: int
    fps: Fraction
    pixel_format: str
    color: ColorInfo
    start_time: Fraction
    audio: AudioInfo | None
    warnings: list[str] = Field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return round(self.duration * 1000)

    @property
    def display_aspect(self) -> Fraction:
        ratio = Fraction(self.coded_width, self.coded_height) * self.sar
        return 1 / ratio if self.rotation_deg in {90, 270} else ratio


def _rational(value, label: str, *, fallback=None) -> Fraction:
    if value in (None, "N/A", ""):
        value = fallback
    try:
        return Fraction(str(value).replace(":", "/"))
    except (ValueError, ZeroDivisionError, TypeError) as exc:
        raise UnsupportedMedia(f"Missing or invalid {label}") from exc


def _rotation(video: dict) -> int:
    angles = []
    for data in video.get("side_data_list", []):
        if "rotation" in data:
            angles.append(_rational(data["rotation"], "rotation"))
        if "displaymatrix" in data:
            rows = [
                re.findall(r"-?\d+", line.split(":", 1)[1])
                for line in data["displaymatrix"].strip().splitlines()
                if ":" in line
            ]
            if len(rows) != 3 or any(len(row) != 3 for row in rows):
                raise UnsupportedMedia("Unrecognized display matrix")
            a, b, u, c, d, v, x, y, w = [int(n) for row in rows for n in row]
            if (
                u
                or v
                or x
                or y
                or w != 1073741824
                or a * d - b * c != 65536**2
                or any(abs(n) not in {0, 65536} for n in (a, b, c, d))
            ):
                raise UnsupportedMedia("Reflected, scaled or skewed display matrix is unsupported")
    tag = video.get("tags", {}).get("rotate")
    if tag is not None:
        angles.append(_rational(tag, "rotation tag"))
    if any(angle.denominator != 1 or angle % 90 for angle in angles):
        raise UnsupportedMedia("Only right-angle display rotations are supported")
    normalized = {int(angle) % 360 for angle in angles}
    if len(normalized) > 1:
        raise UnsupportedMedia("Conflicting display rotation metadata")
    return next(iter(normalized), 0)


def validate_supported_input(info: MediaInfo, *, require_audio: bool = True) -> None:
    if info.pixel_format not in {"yuv420p", "yuvj420p"}:
        raise UnsupportedMedia("Only verified SDR 8-bit 4:2:0 video is supported initially")
    if (info.color.space, info.color.transfer, info.color.primaries) != ("bt709",) * 3:
        raise UnsupportedMedia("HDR or unknown color requires a separately verified conversion")
    if info.color.range not in {"tv", "pc"}:
        raise UnsupportedMedia("Unknown video range cannot be relabeled as BT.709")
    if require_audio and info.audio is None:
        raise UnsupportedMedia("Main video has no audio; silent publishing requires confirmation")
    if info.audio and (
        info.audio.sample_rate not in {44100, 48000}
        or info.audio.channels not in {1, 2}
        or info.audio.channel_layout not in {"mono", "stereo"}
    ):
        raise UnsupportedMedia("Audio sample rate or channel layout is not supported")
    if info.audio and ((info.audio.channels == 1) != (info.audio.channel_layout == "mono")):
        raise UnsupportedMedia("Inconsistent audio channel layout")
    if info.duration <= 0 or info.fps <= 0 or info.sar <= 0:
        raise UnsupportedMedia("Video duration, frame rate and aspect must be positive")


def probe_media(path: Path, runner: Callable[[list[str]], CommandResult]) -> MediaInfo:
    if (
        not path.is_absolute()
        or not path.is_file()
        or path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}
    ):
        raise UnsupportedMedia("Select a supported local video file, not a URL or playlist")
    assert_no_reparse(path)
    before = path.stat()
    result = runner(
        [
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            "-i",
            str(path),
        ]
    )
    if result.exit_code or result.timed_out or result.canceled or result.output_limit_exceeded:
        raise UnsupportedMedia("Media probe failed; check the input and configured tool")
    try:
        raw = json.loads(result.stdout)
        streams = raw["streams"]
        video_tracks = [
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ]
        audio_tracks = [s for s in streams if s.get("codec_type") == "audio"]
        if len(video_tracks) != 1 or len(audio_tracks) > 1:
            raise UnsupportedMedia("Ambiguous video/audio tracks require explicit selection")
        v = video_tracks[0]
        duration = _rational(
            v.get("duration"), "video duration", fallback=raw.get("format", {}).get("duration")
        )
        warnings = []
        sar_raw = v.get("sample_aspect_ratio")
        if sar_raw in {None, "N/A", "0:1"}:
            sar_raw = "1:1"
            warnings.append("unspecified_sar_assumed_square")
        audio = None
        if audio_tracks:
            a = audio_tracks[0]
            channels = int(a["channels"])
            audio = AudioInfo(
                stream_index=a["index"],
                codec=a.get("codec_name", "unknown"),
                sample_rate=int(a["sample_rate"]),
                channels=channels,
                channel_layout=a.get("channel_layout")
                or {1: "mono", 2: "stereo"}.get(channels, "unknown"),
                duration=_rational(a.get("duration"), "audio duration", fallback=duration),
                start_time=_rational(a.get("start_time"), "audio start", fallback=0),
            )
        else:
            warnings.append("missing_audio")
        info = MediaInfo(
            path=path,
            sha256=sha256_file(path),
            duration=duration,
            video_stream_index=v["index"],
            codec=v.get("codec_name", "unknown"),
            coded_width=v["width"],
            coded_height=v["height"],
            sar=_rational(sar_raw, "sample aspect"),
            rotation_deg=_rotation(v),
            fps=_rational(v.get("avg_frame_rate"), "frame rate"),
            pixel_format=v.get("pix_fmt", "unknown"),
            color=ColorInfo(
                space=v.get("color_space", "unknown"),
                transfer=v.get("color_transfer", "unknown"),
                primaries=v.get("color_primaries", "unknown"),
                range=v.get("color_range", "unknown"),
            ),
            start_time=_rational(v.get("start_time"), "video start", fallback=0),
            audio=audio,
            warnings=warnings,
        )
        validate_supported_input(info, require_audio=False)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise UnsupportedMedia("Source changed while probing; reselect the stable input")
        return info
    except UnsupportedMedia:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsupportedMedia("Unrecognized or incomplete media metadata") from exc


def scan_frame_geometry(info: MediaInfo, runner: Callable[[list[str]], CommandResult]) -> int:
    """Decode frame headers before rendering to refuse mid-stream size changes."""
    result = runner(
        [
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-select_streams",
            str(info.video_stream_index),
            "-show_frames",
            "-show_entries",
            "frame=width,height:frame_side_data=",
            "-of",
            "compact=p=0:nk=0",
            "-i",
            str(info.path),
        ]
    )
    if (
        result.exit_code
        or result.timed_out
        or result.canceled
        or result.output_limit_exceeded
        or result.stderr.strip()
    ):
        raise UnsupportedMedia("Input frame scan failed; damaged media cannot be rendered")
    count = 0
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        fields = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
        if "width" not in fields or "height" not in fields:
            continue
        if (fields["width"], fields["height"]) != (str(info.coded_width), str(info.coded_height)):
            raise UnsupportedMedia("Video geometry changes mid-stream; this input is unsupported")
        count += 1
    if not count:
        raise UnsupportedMedia("Input frame scan did not produce valid video frames")
    return count
