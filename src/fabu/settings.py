"""Validated, read-only configuration discovery for a single local Windows user."""

import os
import platform
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    state_dir: Path
    data_dir: Path
    tools_dir: Path
    node_path: Path | None = None
    bind_host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)
    test_mode: Literal["simulation", "local"] = "simulation"

    @field_validator("state_dir", "data_dir", "tools_dir")
    @classmethod
    def absolute_directory(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Configured directory must use an absolute path")
        if value.exists() and not value.is_dir():
            raise ValueError("Configured path is not a directory")
        # Detect a file in a missing directory's parent chain as well.
        for parent in value.parents:
            if parent.exists():
                if not parent.is_dir():
                    raise ValueError("Configured ancestor is not a directory")
                break
        return value

    @field_validator("node_path")
    @classmethod
    def node_executable(cls, value: Path | None) -> Path | None:
        if value is not None and (
            not value.is_absolute() or not value.is_file() or value.suffix.lower() != ".exe"
        ):
            raise ValueError("Choose an existing absolute Node.js executable")
        return value

    @property
    def db_path(self) -> Path:
        return self.state_dir / "fabu.sqlite3"


class PathCheck(BaseModel):
    path: Path
    exists: bool
    free_bytes: int | None
    writable: bool | None = None
    error: str | None = None


class EnvironmentReport(BaseModel):
    python_version: str
    platform: str
    paths: dict[str, PathCheck]
    note: str = "Read-only report; writable=None until explicit initialization tests a write."


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    local = env.get("LOCALAPPDATA")
    if not local:
        raise ValueError("LOCALAPPDATA is required; run as the intended Windows user")
    state = Path(env.get("FABU_STATE_DIR", str(Path(local) / "Fabu")))
    return Settings(
        state_dir=state,
        data_dir=Path(env.get("FABU_DATA_DIR", "D:/FabuData/jobs")),
        tools_dir=Path(env.get("FABU_TOOLS_DIR", str(state / "tools"))),
        node_path=Path(env["FABU_NODE_PATH"]) if env.get("FABU_NODE_PATH") else None,
        bind_host=env.get("FABU_BIND_HOST", "127.0.0.1"),
        port=env.get("FABU_PORT", "8765"),
        test_mode=env.get("FABU_TEST_MODE", "simulation"),
    )


def physical_state_dir(settings: Settings) -> Path:
    """Resolve persisted files, not just a possibly merged MSIX AppData directory.

    Runtime ownership/log files are deliberately not anchors: a desktop process
    may have created them in native AppData while user data lives in LocalCache.
    Never merge, copy, initialize, or choose between conflicting stores here.
    """
    from fabu.assets import assert_no_reparse

    state = settings.state_dir
    assert_no_reparse(state)
    roots = set()
    for relative in ("fabu.sqlite3", "ui-settings.json", "credentials/credentials.dpapi"):
        path = state / relative
        assert_no_reparse(path)
        if path.is_file():
            resolved = path.resolve(strict=True)
            root = resolved.parents[len(Path(relative).parts) - 1]
            assert_no_reparse(root)
            roots.add(root)
    if len(roots) > 1:
        raise ValueError(
            "State files resolve to different physical storage roots; "
            "select an explicit FABU_STATE_DIR. No data was moved or rebuilt."
        )
    return next(iter(roots)) if roots else state.resolve()


def check_environment(settings: Settings) -> EnvironmentReport:
    checks = {}
    for name in ("state_dir", "data_dir", "tools_dir"):
        path = getattr(settings, name)
        ancestor = path
        while not ancestor.exists() and ancestor.parent != ancestor:
            ancestor = ancestor.parent
        try:
            free_bytes = shutil.disk_usage(ancestor).free
            error = None
        except OSError:
            free_bytes = None
            error = "Storage location is not accessible"
        checks[name] = PathCheck(
            path=path, exists=path.is_dir(), free_bytes=free_bytes, error=error
        )
    return EnvironmentReport(
        python_version=platform.python_version(), platform=platform.system(), paths=checks
    )
