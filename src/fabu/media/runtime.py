"""Explicit tool binding; no PATH search, automatic download or runtime updates."""

import os
from pathlib import Path
from threading import Event

from pydantic import BaseModel, ConfigDict, field_validator

from fabu.tools import CommandResult, CommandSpec, run_command


class MediaTools(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ffmpeg_path: Path
    ffprobe_path: Path

    @field_validator("ffmpeg_path", "ffprobe_path")
    @classmethod
    def executable_exists(cls, path: Path) -> Path:
        if not path.is_absolute() or not path.is_file() or path.suffix.lower() != ".exe":
            raise ValueError("Select an existing absolute media tool .exe path")
        return path

    def _run(
        self,
        path: Path,
        args: list[str],
        cancel: Event | None,
        timeout_s: int,
        cwd: Path | None = None,
    ) -> CommandResult:
        return run_command(
            CommandSpec(
                executable=path,
                args=args,
                cwd=cwd or path.parent,
                env=dict(os.environ),
                timeout_s=timeout_s,
                max_output_bytes=16_777_216,
            ),
            cancel or Event(),
        )

    def probe(self, args: list[str], cancel: Event | None = None) -> CommandResult:
        return self._run(self.ffprobe_path, args, cancel, 1800)

    def ffmpeg(
        self,
        args: list[str],
        cancel: Event | None = None,
        timeout_s: int = 86400,
        *,
        cwd: Path | None = None,
    ) -> CommandResult:
        return self._run(self.ffmpeg_path, args, cancel, timeout_s, cwd)

    def versions(self) -> dict[str, str]:
        versions = {}
        for name, path in (("ffmpeg", self.ffmpeg_path), ("ffprobe", self.ffprobe_path)):
            result = self._run(path, ["-version"], None, 10)
            if result.exit_code:
                raise ValueError(f"{name} version probe failed")
            versions[name] = result.stdout.decode("utf-8", "replace").splitlines()[0][:300]
        return versions
