"""Local immutable asset snapshots. The source is never overwritten or removed."""

import hashlib
import os
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict

from fabu.store import JobStore

MediaType = Literal["image", "video", "subtitle", "font"]
_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg"},
    "video": {".mp4", ".mkv", ".webm", ".mov"},
    "subtitle": {".srt"},
    "font": {".ttf", ".otf"},
}


class AssetInUse(ValueError):
    pass


class AssetRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    sha256: str
    local_path: Path
    media_type: MediaType
    size_bytes: int


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat()
    except FileNotFoundError:
        return False
    return bool(getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def assert_no_reparse(path: Path) -> None:
    for candidate in (path, *path.parents):
        if _is_reparse_point(candidate):
            raise ValueError("A reparse point is not allowed in an asset path")


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _copy_file(source: Path, destination: Path) -> None:
    with source.open("rb") as src, destination.open("xb") as dest:
        shutil.copyfileobj(src, dest, length=1024 * 1024)
        dest.flush()
        os.fsync(dest.fileno())


def _check_content(path: Path, media_type: str, extension: str) -> None:
    if extension not in _EXTENSIONS.get(media_type, set()):
        raise ValueError("Unsupported media type or extension")
    size = path.stat().st_size
    if size == 0:
        raise ValueError("Empty assets are not supported")
    with path.open("rb") as stream:
        signature = stream.read(16)
    if media_type == "image":
        try:
            with Image.open(path) as image:
                if image.format not in {"PNG", "JPEG"} or image.width * image.height > 40_000_000:
                    raise ValueError("Unsupported or oversized image")
                expected = "PNG" if extension == ".png" else "JPEG"
                if image.format != expected:
                    raise ValueError("Image extension does not match content")
                image.verify()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError("Image content failed validation") from exc
    elif media_type == "subtitle":
        if size > 5_000_000:
            raise ValueError("Subtitle exceeds the supported size")
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeError as exc:
            raise ValueError("Subtitle must be UTF-8") from exc
        if "-->" not in text or "\0" in text:
            raise ValueError(
                "Subtitle lacks SRT timing; full timing validation occurs before render"
            )
    elif media_type == "font":
        if size > 50_000_000 or signature[:4] not in {b"\x00\x01\x00\x00", b"OTTO", b"true"}:
            raise ValueError("Only supported TTF/OTF font files may be imported")
    elif media_type == "video":
        valid = (
            signature[4:8] == b"ftyp"
            if extension in {".mp4", ".mov"}
            else signature[:4] == b"\x1aE\xdf\xa3"
        )
        if not valid:
            raise ValueError(
                "Video container signature is invalid; full probe occurs before render"
            )


class AssetStore:
    def __init__(self, root: Path, store: JobStore):
        if not root.is_absolute():
            raise ValueError("Asset root must be absolute")
        assert_no_reparse(root)
        self.root = Path(os.path.abspath(root))
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = store

    def _safe_managed_path(self, path: Path) -> Path:
        assert_no_reparse(self.root)
        assert_no_reparse(path)
        if path.parent != self.root:
            raise ValueError("Asset path escapes the managed library")
        return path

    def import_file(self, path: Path, media_type: str) -> AssetRef:
        if not path.is_absolute() or not path.is_file():
            raise ValueError("Select an existing absolute file")
        assert_no_reparse(path)
        assert_no_reparse(self.root)
        extension = path.suffix.lower()
        _check_content(path, media_type, extension)
        source_stat = path.stat()
        if shutil.disk_usage(self.root).free < source_stat.st_size + 64 * 1024 * 1024:
            raise OSError("Insufficient free space to snapshot asset safely")
        identifier = str(uuid4())
        temporary = self._safe_managed_path(self.root / f".{identifier}.part")
        final = self._safe_managed_path(self.root / f"{identifier}{extension}")
        promoted = False
        try:
            _copy_file(path, temporary)
            after = path.stat()
            if (source_stat.st_size, source_stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("Source changed during import; retry explicitly")
            _check_content(temporary, media_type, extension)
            digest = sha256_file(temporary)
            if digest != sha256_file(path):
                raise ValueError("Source changed during import; snapshot integrity is uncertain")
            ref = AssetRef(
                id=identifier,
                sha256=digest,
                local_path=final,
                media_type=media_type,
                size_bytes=temporary.stat().st_size,
            )
            with self.store.transaction() as db:
                self._safe_managed_path(final)
                os.rename(temporary, final)  # Windows rename does not overwrite an existing asset.
                promoted = True
                db.execute(
                    "INSERT INTO assets VALUES (?,?,?,?,?,?)",
                    (
                        identifier,
                        digest,
                        str(final),
                        media_type,
                        ref.size_bytes,
                        datetime.now(UTC).isoformat(),
                    ),
                )
            return ref
        except BaseException:
            if promoted:
                self._safe_managed_path(final).unlink(missing_ok=True)
            raise
        finally:
            self._safe_managed_path(temporary).unlink(missing_ok=True)

    def get(self, asset_id: str) -> AssetRef:
        UUID(asset_id)
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError("Asset does not exist")
        ref = AssetRef.model_validate({key: row[key] for key in AssetRef.model_fields})
        self._safe_managed_path(ref.local_path)
        return ref

    def verify(self, asset_id: str) -> bool:
        try:
            ref = self.get(asset_id)
            return (
                ref.local_path.is_file()
                and ref.local_path.stat().st_size == ref.size_bytes
                and sha256_file(ref.local_path) == ref.sha256
            )
        except (ValueError, KeyError, OSError):
            return False

    def delete_unreferenced(self, asset_id: str) -> None:
        ref = self.get(asset_id)
        with self.store.transaction() as db:
            if db.execute("SELECT 1 FROM asset_refs WHERE asset_id=?", (asset_id,)).fetchone():
                raise AssetInUse("Asset is referenced by a preset or task")
            self._safe_managed_path(ref.local_path).unlink(missing_ok=True)
            db.execute("DELETE FROM assets WHERE id=?", (asset_id,))
