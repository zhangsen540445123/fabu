"""Bounded local hardware evidence; encoder enumeration alone is never success."""

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fabu.assets import assert_no_reparse
from fabu.media.encoders import HardwareUnavailable
from fabu.tools import CommandSpec, run_command


class HardwareReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    available: bool
    reason: Literal[
        "nvenc_verified",
        "driver_incompatible",
        "no_device",
        "encoder_unavailable",
        "probe_failed",
        "inventory_unavailable",
    ]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    device_name: str | None = Field(default=None, max_length=200)
    checked_at: datetime
    cuda_fast_path: Literal[False] = False
    cuda_reason: Literal["quality_validation_pending"] = "quality_validation_pending"

    @model_validator(mode="after")
    def coherent(self):
        if (
            self.available != (self.reason == "nvenc_verified")
            or self.checked_at.utcoffset() is None
        ):
            raise ValueError("Hardware evidence is inconsistent")
        return self


def _ok(result):
    return not (
        result.exit_code or result.timed_out or result.canceled or result.output_limit_exceeded
    )


def gpu_inventory():
    """Fixed, read-only Windows query; no PATH executable search or driver changes."""
    if sys.platform != "win32":
        raise OSError("Windows inventory unavailable")
    executable = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = (
        "Get-CimInstance Win32_VideoController | "
        "Where-Object {$_.Name -like '*NVIDIA*'} | "
        "ForEach-Object {[pscustomobject]@{name=$_.Name;driver=$_.DriverVersion;"
        "id=$_.PNPDeviceID}} | ConvertTo-Json -Compress"
    )
    result = run_command(
        CommandSpec(
            executable=executable,
            args=["-NoProfile", "-NonInteractive", "-Command", script],
            cwd=executable.parent,
            env=dict(os.environ),
            timeout_s=10,
            max_output_bytes=65536,
        ),
        threading.Event(),
    )
    if not _ok(result) or result.stderr.strip():
        raise OSError("GPU inventory unavailable")
    rows = json.loads(result.stdout.decode("utf-8-sig")) if result.stdout.strip() else []
    rows = [rows] if isinstance(rows, dict) else rows
    if not isinstance(rows, list) or len(rows) > 16:
        raise ValueError("Invalid GPU inventory")
    if any(
        not isinstance(r, dict)
        or set(r) != {"name", "driver", "id"}
        or any(not isinstance(v, str) or len(v) > 300 for v in r.values())
        for r in rows
    ):
        raise ValueError("Invalid GPU inventory")
    return rows


def environment_fingerprint(tools, inventory=None):
    rows = (inventory or gpu_inventory)()
    payload = {
        "probe_schema": 1,
        "tools": tools.identity(force=True),
        "devices": sorted(rows, key=lambda r: r["id"]),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest(), rows


class HardwareProbe:
    """Only check() runs tests. cached_report() never authorizes GPU use."""

    def __init__(self, root: Path, *, inventory=None, clock=time.monotonic):
        if not root.is_absolute():
            raise ValueError("Hardware probe directory must be absolute")
        self.root, self.inventory, self.clock = root, inventory or gpu_inventory, clock
        self._work = threading.Lock()
        self._state = threading.Lock()
        self._cache = None

    def cached_report(self, tools):
        identity = tools.identity()
        with self._state:
            cache = self._cache
        if cache:
            fp, cached_identity, report, expiry = cache
            if identity == cached_identity:
                return {
                    "status": "checked",
                    "stale": self.clock() >= expiry,
                    "report": report.model_dump(mode="json"),
                }
            return {"status": "unchecked", "stale": True, "report": None}
        # Persisted evidence is display-only; never loaded into the authorization cache.
        evidence = self.root / "last-hardware-check.json"
        try:
            assert_no_reparse(evidence)
            if evidence.stat().st_size < 32768:
                saved = json.loads(evidence.read_text(encoding="utf-8"))
                if saved["tools"] == identity:
                    report = HardwareReport.model_validate(saved["report"])
                    return {
                        "status": "checked",
                        "stale": True,
                        "report": report.model_dump(mode="json"),
                    }
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return {"status": "unchecked", "stale": True, "report": None}

    def check(self, tools, cancel):
        while not self._work.acquire(timeout=0.1):
            if cancel.is_set():
                raise HardwareUnavailable("processing_canceled")
        try:
            return self._check(tools, cancel)
        finally:
            self._work.release()

    def _check(self, tools, cancel):
        if cancel.is_set():
            raise HardwareUnavailable("processing_canceled")
        identity = tools.identity(force=True)
        try:
            fingerprint, rows = environment_fingerprint(tools, self.inventory)
            reason = None if rows else "no_device"
        except (OSError, ValueError, KeyError):
            fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            rows, reason = [], "inventory_unavailable"
        if cancel.is_set():
            raise HardwareUnavailable("processing_canceled")
        with self._state:
            cache = self._cache
        if cache and cache[0] == fingerprint and self.clock() < cache[3]:
            return cache[2]
        if reason is None:
            try:
                reason = self._probe(tools, cancel)
                after, _ = environment_fingerprint(tools, self.inventory)
                if after != fingerprint:
                    reason = "probe_failed"
            except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
                reason = "probe_failed"
        if cancel.is_set():
            raise HardwareUnavailable("processing_canceled")
        report = HardwareReport(
            available=reason == "nvenc_verified",
            reason=reason,
            fingerprint=fingerprint,
            device_name=rows[0]["name"][:200]
            if len(rows) == 1
            else ("NVIDIA NVENC device 0" if rows else None),
            checked_at=datetime.now(UTC),
        )
        with self._state:
            self._cache = (
                fingerprint,
                identity,
                report,
                self.clock() + (900 if report.available else 60),
            )
        try:
            assert_no_reparse(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            destination = self.root / "last-hardware-check.json"
            assert_no_reparse(destination)
            temporary = self.root / "last-hardware-check.tmp"
            assert_no_reparse(temporary)
            temporary.write_text(
                json.dumps({"tools": identity, "report": report.model_dump(mode="json")}),
                encoding="utf-8",
            )
            os.replace(temporary, destination)
        except (OSError, ValueError):
            pass  # Display evidence persistence is not a capability or authorization gate.
        return report

    def _probe(self, tools, cancel):
        listing = tools.ffmpeg(["-hide_banner", "-encoders"], cancel, timeout_s=10)
        if not _ok(listing) or b"hevc_nvenc" not in listing.stdout:
            return "encoder_unavailable"
        if cancel.is_set():
            raise HardwareUnavailable("processing_canceled")
        assert_no_reparse(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        # TemporaryDirectory owns exactly this fresh subdirectory; never delete a caller path.
        with tempfile.TemporaryDirectory(prefix="probe-", dir=self.root) as directory:
            path = Path(directory).resolve()
            if not path.is_relative_to(self.root.resolve()):
                raise ValueError("Probe directory escaped its workspace")
            assert_no_reparse(path)
            output = path / "probe.mp4"
            encoded = tools.ffmpeg(
                [
                    "-hide_banner",
                    "-v",
                    "error",
                    "-nostdin",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=2560x1440:rate=30",
                    "-frames:v",
                    "6",
                    "-an",
                    "-c:v",
                    "hevc_nvenc",
                    "-gpu",
                    "0",
                    "-preset",
                    "p5",
                    "-tune",
                    "hq",
                    "-rc",
                    "vbr",
                    "-cq",
                    "20",
                    "-b:v",
                    "0",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    "-n",
                    str(output),
                ],
                cancel,
                timeout_s=30,
            )
            if not _ok(encoded) or encoded.stderr.strip():
                message = encoded.stderr.lower()
                if (
                    b"required nvenc api version" in message
                    or b"minimum required nvidia driver" in message
                ):
                    return "driver_incompatible"
                return "probe_failed"
            if cancel.is_set():
                raise HardwareUnavailable("processing_canceled")
            if not output.is_file() or not (0 < output.stat().st_size < 32 * 1024 * 1024):
                return "probe_failed"
            probed = tools.probe(
                [
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-count_frames",
                    "-show_streams",
                    "-of",
                    "json",
                    str(output),
                ],
                cancel,
                timeout_s=15,
            )
            if not _ok(probed) or probed.stderr.strip():
                return "probe_failed"
            streams = json.loads(probed.stdout)["streams"]
            if len(streams) != 1:
                return "probe_failed"
            v = streams[0]
            if (
                (
                    v.get("codec_name"),
                    v.get("width"),
                    v.get("height"),
                    v.get("pix_fmt"),
                    v.get("nb_read_frames"),
                )
                != ("hevc", 2560, 1440, "yuv420p", "6")
                or Fraction(v["avg_frame_rate"]) != 30
                or abs(Fraction(v["duration"]) - Fraction(1, 5)) > Fraction(1, 30)
            ):
                return "probe_failed"
            decoded = tools.ffmpeg(
                [
                    "-hide_banner",
                    "-v",
                    "error",
                    "-xerror",
                    "-nostdin",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(output),
                    "-map",
                    "0:v:0",
                    "-f",
                    "null",
                    "-",
                ],
                cancel,
                timeout_s=15,
            )
            return (
                "nvenc_verified" if _ok(decoded) and not decoded.stderr.strip() else "probe_failed"
            )
