"""Bounded single-video download orchestration, with no browser credentials or plugin execution."""

import json
import math
import os
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from fabu.assets import AssetRef, AssetStore, assert_no_reparse
from fabu.media.probe import probe_media, validate_supported_input
from fabu.media.thumbnail import ImageResponse, fetch_thumbnail
from fabu.media.youtube import VIDEO_ID, parse_youtube_url
from fabu.tools import CommandResult, CommandSpec, run_command

__all__ = [
    "parse_youtube_url",
    "choose_formats",
    "download_source",
    "YtDlpRunner",
    "SourceManifest",
]
_FORMAT_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class SourceUnavailable(ValueError):
    pass


class SourceAuthenticationRequired(SourceUnavailable):
    """Downloader explicitly requires authentication; never contains remote error text."""


class SelectedFormat(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    selector: str
    video_format_id: str
    audio_format_id: str | None
    width: int
    height: int
    fps: float
    estimated_bytes: int | None = None
    audio_language: str | None = None
    audio_selection: str = "single_track"


class SourceManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    video_id: str
    source: AssetRef
    thumbnail: AssetRef
    selected_format: SelectedFormat
    fetched_at: datetime


def _number(value, default=0):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else default
    except (TypeError, ValueError):
        return default


def choose_formats(metadata: dict) -> SelectedFormat:
    if (
        metadata.get("is_live")
        or metadata.get("live_status") in {"is_live", "is_upcoming", "post_live"}
        or metadata.get("_type") in {"playlist", "multi_video"}
    ):
        raise SourceUnavailable("Live streams, upcoming streams and playlists are not supported")
    rows = [
        f
        for f in metadata.get("formats", [])
        if isinstance(f, dict)
        and _FORMAT_ID.fullmatch(str(f.get("format_id", "")))
        and not f.get("has_drm")
    ]
    videos = [
        f
        for f in rows
        if f.get("vcodec") not in (None, "none")
        and _number(f.get("width")) > 0
        and _number(f.get("height")) > 0
        and f.get("dynamic_range") not in {"HDR10", "HLG", "DV"}
    ]
    audios = [
        f
        for f in rows
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    # yt-dlp's pinned YouTube extractor marks original=10, default=5. A default dub
    # must never outrank the original merely because it has a higher audio bitrate.
    audio_rows = [f for f in rows if f.get("acodec") not in (None, "none")]
    original = [f for f in audio_rows if f.get("language_preference") == 10]
    languages = {f.get("language") for f in audio_rows if f.get("language")}
    selection = "original" if original else "single_track"
    if not original and len(languages) > 1:
        raise SourceUnavailable("Ambiguous audio languages; original soundtrack needs review")
    selected_languages = {f.get("language") for f in original if f.get("language")}
    if len(selected_languages) > 1:
        raise SourceUnavailable("Ambiguous original audio tracks")
    if original:
        allowed = {f["format_id"] for f in original}
        audios = [f for f in audios if f["format_id"] in allowed]
        videos = [
            f for f in videos if f.get("acodec") in (None, "none") or f["format_id"] in allowed
        ]
    if not videos:
        raise SourceUnavailable("No supported non-DRM video format is available")
    videos = [f for f in videos if audios or f.get("acodec") not in (None, "none")]
    if not videos:
        raise SourceUnavailable("No audio stream is available; a silent download is not success")
    fitting = [
        f
        for f in videos
        if min(f["width"], f["height"]) <= 1440 and max(f["width"], f["height"]) <= 2560
    ]
    candidates = fitting or videos

    def rank(f):
        pixels = int(f["width"]) * int(f["height"])
        return (
            pixels if fitting else -pixels,
            _number(f.get("fps")) <= 30,
            _number(f.get("tbr")),
            f["format_id"],
        )

    video = max(candidates, key=rank)
    audio = (
        max(audios, key=lambda f: (_number(f.get("abr")), f.get("ext") == "m4a"))
        if audios and video.get("acodec") in (None, "none")
        else None
    )
    selector = video["format_id"] + (("+" + audio["format_id"]) if audio else "")
    sizes = [
        _number(f.get("filesize") or f.get("filesize_approx"))
        for f in ([video, audio] if audio else [video])
    ]
    return SelectedFormat(
        selector=selector,
        video_format_id=video["format_id"],
        audio_format_id=audio["format_id"] if audio else None,
        width=video["width"],
        height=video["height"],
        fps=_number(video.get("fps")),
        estimated_bytes=int(sum(sizes)) if all(sizes) else None,
        audio_language=(audio or video).get("language"),
        audio_selection=selection,
    )


class YtDlpRunner:
    def __init__(
        self, python_path: Path, node_path: Path, ffmpeg_path: Path, cancel: Event | None = None
    ):
        for path in (python_path, node_path, ffmpeg_path):
            if not path.is_absolute() or not path.is_file() or path.suffix.lower() != ".exe":
                raise ValueError("Downloader dependencies must use known absolute executable paths")
        self.python_path = python_path
        self.node_path = node_path
        self.ffmpeg_path = ffmpeg_path
        self.cancel = cancel or Event()

    def command(self, args: list[str], workspace: Path) -> CommandSpec:
        flags = [
            "-X",
            "utf8",
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-plugin-dirs",
            "--no-update",
            "--no-playlist",
            "--no-cookies",
            "--no-cookies-from-browser",
            "--no-cache-dir",
            "--no-remote-components",
            "--no-js-runtimes",
            "--js-runtimes",
            f"node:{self.node_path}",
            "--ffmpeg-location",
            str(self.ffmpeg_path),
            "--socket-timeout",
            "20",
            "--retries",
            "2",
            "--fragment-retries",
            "2",
            "--abort-on-unavailable-fragments",
            "--no-progress",
            "--no-write-info-json",
            "--no-overwrites",
        ]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("YIXIAOER_", "YXER_"))}
        return CommandSpec(
            executable=self.python_path,
            args=flags + args,
            cwd=workspace,
            env=env,
            timeout_s=7200,
            max_output_bytes=16_777_216,
        )

    def __call__(self, args: list[str], workspace: Path) -> CommandResult:
        return run_command(self.command(args, workspace), self.cancel)


def _successful(result: CommandResult) -> None:
    if result.exit_code or result.timed_out or result.canceled or result.output_limit_exceeded:
        message = result.stderr.decode("utf-8", "replace").lower()
        if "sign in" in message or "login" in message:
            raise SourceAuthenticationRequired(
                "authentication_required: YouTube requires login verification; "
                "no browser credentials were read"
            )
        raise SourceUnavailable("download_failed: check network/access; no login state was read")


def download_source(
    video_id: str,
    workspace: Path,
    runner: Callable,
    *,
    assets: AssetStore,
    probe_runner: Callable,
    thumbnail_transport: Callable[[str], ImageResponse],
) -> SourceManifest:
    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError("Invalid video ID")
    if not workspace.is_absolute():
        raise ValueError("Download workspace must be absolute")
    assert_no_reparse(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    checkpoint = workspace / "source-checkpoint.json"
    assert_no_reparse(checkpoint)
    if checkpoint.is_file():
        try:
            cached = json.loads(checkpoint.read_text(encoding="utf-8"))
            if cached["video_id"] != video_id:
                raise SourceUnavailable("Checkpoint belongs to a different source")
            source = assets.get(cached["source"]["id"])
            if source.sha256 != cached["source"]["sha256"] or not assets.verify(source.id):
                raise SourceUnavailable(
                    "Cached source integrity failed; explicit repair is required"
                )
            validate_supported_input(probe_media(source.local_path, probe_runner))
            choice = SelectedFormat.model_validate(cached["selected_format"])
            thumbnail = fetch_thumbnail(
                video_id, thumbnail_transport, assets=assets, workspace=workspace
            )
            return SourceManifest(
                video_id=video_id,
                source=source,
                thumbnail=thumbnail,
                selected_format=choice,
                fetched_at=datetime.now(UTC),
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise SourceUnavailable(
                "Invalid source checkpoint; explicit repair is required"
            ) from exc
    url = f"https://www.youtube.com/watch?v={video_id}"
    response = runner(["--dump-single-json", "--skip-download", url], workspace)
    _successful(response)
    try:
        metadata = json.loads(response.stdout)
    except (ValueError, TypeError) as exc:
        raise SourceUnavailable("Downloader metadata was invalid") from exc
    if metadata.get("id") != video_id:
        raise SourceUnavailable("Downloader returned a different source video")
    choice = choose_formats(metadata)
    expected_bytes = choice.estimated_bytes or int(
        _number(metadata.get("duration"), 600) * 2_500_000
    )
    if shutil.disk_usage(workspace).free < expected_bytes * 3 + 256 * 1024 * 1024:
        raise SourceUnavailable(
            "Insufficient disk space for download, merge and immutable source copy"
        )
    # File path is generated, never derived from titles or response URLs.
    prefix = f"source-{uuid4()}"
    template = workspace / (prefix + ".%(ext)s")
    result = runner(
        [
            "--format",
            choice.selector,
            "--merge-output-format",
            "mkv",
            "--remux-video",
            "mkv",
            "--output",
            str(template),
            "--print",
            "after_move:filepath",
            url,
        ],
        workspace,
    )
    _successful(result)
    try:
        lines = result.stdout.decode("utf-8-sig").strip().splitlines()
        path = Path(lines[-1])
        if path.parent != workspace or path.stem != prefix or path.suffix != ".mkv":
            raise ValueError("Unexpected downloaded file")
        info = probe_media(path, probe_runner)
        validate_supported_input(info)
        expected = _number(metadata.get("duration"))
        if expected and abs(float(info.duration) - expected) > 2:
            raise SourceUnavailable("Downloaded duration does not match the selected source")
    except (IndexError, ValueError, OSError) as exc:
        raise SourceUnavailable(
            "Downloaded media failed identity, track or duration checks"
        ) from exc
    source = assets.import_file(path, "video")
    temporary = checkpoint.with_suffix(".tmp")
    assert_no_reparse(temporary)
    temporary.write_text(
        json.dumps(
            {
                "video_id": video_id,
                "source": source.model_dump(mode="json"),
                "selected_format": choice.model_dump(mode="json"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, checkpoint)
    thumbnail = fetch_thumbnail(video_id, thumbnail_transport, assets=assets, workspace=workspace)
    return SourceManifest(
        video_id=video_id,
        source=source,
        thumbnail=thumbnail,
        selected_format=choice,
        fetched_at=datetime.now(UTC),
    )
