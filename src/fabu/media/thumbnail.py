"""Original YouTube thumbnails only; no screenshot fallback or arbitrary remote host."""

import io
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from PIL import Image

from fabu.assets import AssetRef, AssetStore, assert_no_reparse
from fabu.media.youtube import VIDEO_ID

_MAX_BYTES = 8_000_000
_SIZES = (
    ("maxresdefault", 1280, 720),
    ("sddefault", 640, 480),
    ("hqdefault", 480, 360),
    ("mqdefault", 320, 180),
)


class ThumbnailUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class ImageResponse:
    status: int
    url: str
    content_type: str
    body: bytes


def _trusted_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and parts.hostname == "i.ytimg.com"
            and not parts.port
            and not parts.username
            and not parts.password
            and parts.path.startswith("/vi/")
        )
    except ValueError:
        return False


class HttpImageTransport:
    def __init__(self, cancel: Event | None = None):
        self.cancel = cancel

    def _check_cancel(self):
        if self.cancel is not None and self.cancel.is_set():
            raise ThumbnailUnavailable("Thumbnail request canceled")

    def __call__(self, url: str) -> ImageResponse:
        self._check_cancel()
        if not _trusted_url(url):
            raise ThumbnailUnavailable("Thumbnail host is not allowed")
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                with client.stream("GET", url) as response:
                    # A thumbnail label never authorizes following an unexpected redirect.
                    if response.is_redirect:
                        raise ThumbnailUnavailable("Unexpected thumbnail redirect")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        self._check_cancel()
                        content.extend(chunk)
                        if len(content) > _MAX_BYTES:
                            raise ThumbnailUnavailable("Thumbnail response is too large")
                    return ImageResponse(
                        response.status_code,
                        str(response.url),
                        response.headers.get("content-type", ""),
                        bytes(content),
                    )
        except httpx.HTTPError as exc:
            raise ThumbnailUnavailable("Original thumbnail network request failed") from exc


def fetch_thumbnail(
    video_id: str, transport: Callable[[str], ImageResponse], *, assets: AssetStore, workspace: Path
) -> AssetRef:
    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError("Invalid video ID")
    if not workspace.is_absolute():
        raise ValueError("Workspace must be absolute")
    assert_no_reparse(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    for name, min_width, min_height in _SIZES:
        url = f"https://i.ytimg.com/vi/{video_id}/{name}.jpg"
        try:
            response = transport(url)
            if (
                response.status != 200
                or response.url != url
                or not _trusted_url(response.url)
                or response.content_type.split(";")[0].lower() not in {"image/jpeg", "image/png"}
                or len(response.body) > _MAX_BYTES
            ):
                continue
            with Image.open(io.BytesIO(response.body)) as image:
                if (
                    image.width < min_width
                    or image.height < min_height
                    or image.width * image.height > 40_000_000
                    or image.format not in {"JPEG", "PNG"}
                ):
                    continue
                extension = ".png" if image.format == "PNG" else ".jpg"
                image.verify()
        except (ThumbnailUnavailable, OSError, ValueError, Image.DecompressionBombError):
            continue
        temporary = workspace / f"thumbnail-{uuid4()}{extension}"
        try:
            temporary.write_bytes(response.body)
            return assets.import_file(temporary, "image")
        finally:
            assert_no_reparse(temporary)
            temporary.unlink(missing_ok=True)
    raise ThumbnailUnavailable("No valid original YouTube thumbnail is available; task paused")
