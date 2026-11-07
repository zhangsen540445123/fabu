"""Owned background runtime. Private HMAC control is not an application publishing API."""

import argparse
import hmac
import os
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import psutil
import uvicorn
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from fabu.api.app import create_app
from fabu.launcher import ROOT, _effective, _read_private, _signature, _write_private, runtime_path
from fabu.settings import Settings
from fabu.store import JobStore


class ControlCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["status", "bootstrap", "stop"]
    nonce: str = Field(pattern=r"^[0-9a-f]{32}$")
    timestamp: int
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


def serve(config_path):
    boot = _read_private(config_path)
    settings = _effective(Settings.model_validate(boot["settings"]))
    path = runtime_path(settings)
    if config_path.parent != path.parent or not config_path.name.startswith("launch-"):
        raise ValueError("Runtime config must be in its private directory")
    config_path.unlink()
    secret = boot["secret"]
    with socket.socket() as sock:
        # Windows exclusive binding prevents another local server from sharing this port.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((settings.bind_host, settings.port))
        sock.listen(128)
        app = create_app(settings, JobStore(settings.db_path), enable_worker=True)
        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def owned_lifespan(application):
            async with original_lifespan(application):
                if not application.state.media_controller.status()["running"]:
                    raise ValueError("Global worker ownership could not be acquired")
                from fabu.assets import assert_no_reparse

                log = path.parent / "runtime.log"
                assert_no_reparse(log)
                log.write_text(
                    "runtime_ready: new jobs require explicit local policy\n", encoding="utf-8"
                )
                yield

        app.router.lifespan_context = owned_lifespan
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=settings.bind_host,
                port=settings.port,
                access_log=False,
                log_config=None,
                log_level="critical",
            )
        )
        process = psutil.Process(os.getpid())
        record = {
            "pid": process.pid,
            "created": process.create_time(),
            "exe": process.exe(),
            "port": settings.port,
            "state_dir": str(settings.state_dir),
            "secret": secret,
        }

        def identity():
            return {
                "source_root": str(ROOT.resolve()),
                "python_executable": str(Path(sys.executable).resolve()),
                "python_prefix": str(Path(sys.prefix).resolve()),
                "state_dir": str(app.state.settings.state_dir.resolve()),
                "db_path": str(app.state.store.path.resolve()),
                "settings_path": str((app.state.settings.state_dir / "ui-settings.json").resolve()),
                "credentials_path": str(app.state.credentials.path.resolve()),
                "isolated": bool(sys.flags.isolated),
                "settings": app.state.settings.model_dump(mode="json"),
            }

        record["identity"] = identity()
        used = {}
        lock = threading.Lock()

        @app.post("/_runtime/control")
        def control(command: ControlCommand):
            payload = command.model_dump(exclude={"signature"})
            now = int(time.time())
            with lock:
                for nonce, stamp in list(used.items()):
                    if now - stamp > 60:
                        used.pop(nonce)
                if (
                    abs(now - command.timestamp) > 30
                    or command.nonce in used
                    or len(used) >= 512
                    or not hmac.compare_digest(
                        command.signature, _signature(secret, "request", payload)
                    )
                ):
                    raise HTTPException(403, "Runtime control denied")
                used[command.nonce] = command.timestamp
            data = {
                "nonce": command.nonce,
                "pid": os.getpid(),
                "identity": identity(),
                "ready": server.started
                and app.state.media_controller.status()["running"]
                and not server.should_exit,
            }
            if command.action == "bootstrap":
                if not data["ready"]:
                    raise HTTPException(409, "Runtime not ready")
                data["bootstrap"] = app.state.security.issue_bootstrap()
            elif command.action == "stop":
                server.should_exit = True
            return {"data": data, "signature": _signature(secret, "response", data)}

        _write_private(path, record)
        try:
            server.run(sockets=[sock])
        finally:
            if path.exists() and _read_private(path) == record:
                path.unlink()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        serve(args.config)
    except Exception as exc:
        # Never dump exception text/traceback, CLI output, config or control secrets.
        from fabu.assets import assert_no_reparse

        log = args.config.parent / "runtime.log"
        assert_no_reparse(log)
        log.write_text(f"runtime_failed: {type(exc).__name__}\n", encoding="utf-8")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
