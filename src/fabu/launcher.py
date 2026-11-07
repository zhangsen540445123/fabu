"""Windows local runtime launcher. Reuse verified ownership; never kill by process name."""

import argparse
import hashlib
import hmac
import importlib.metadata
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

import psutil
import pywintypes
import win32crypt
from pydantic import BaseModel, ConfigDict, ValidationError

from fabu.assets import assert_no_reparse
from fabu.credentials import secure_directory
from fabu.settings import Settings, check_environment, load_settings
from fabu.worker import AlreadyRunning, SingleInstance

ROOT = Path(__file__).resolve().parents[2]


class LaunchResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    pid: int
    reused: bool
    url: str
    log_path: Path


def runtime_path(settings):
    path = settings.state_dir / "runtime" / "owner.dpapi"
    assert_no_reparse(path)
    return path


def _write_private(path, value):
    secure_directory(path.parent)
    assert_no_reparse(path)
    temp = path.parent / ("pending-" + str(uuid4()) + ".dpapi")
    try:
        cipher = win32crypt.CryptProtectData(
            json.dumps(value).encode(), "Fabu runtime", None, None, None, 1
        )
        with temp.open("xb") as stream:
            stream.write(cipher)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _read_private(path):
    assert_no_reparse(path)
    if not path.is_file() or path.stat().st_size > 131072:
        raise ValueError("Runtime ownership metadata is missing or invalid")
    try:
        _, plain = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 1)
        data = json.loads(plain)
        if not isinstance(data, dict):
            raise ValueError("Runtime metadata must be an object")
        return data
    except (pywintypes.error, ValueError) as exc:
        raise ValueError("Runtime metadata could not be authenticated") from exc


def load_runtime_record(settings):
    record = _read_private(runtime_path(settings))
    if (
        record.get("state_dir") != str(settings.state_dir)
        or record.get("port") != settings.port
        or not isinstance(record.get("secret"), str)
        or len(record["secret"]) != 64
        or not isinstance(record.get("pid"), int)
        or record["pid"] <= 0
        or not isinstance(record.get("created"), (int, float))
        or not isinstance(record.get("exe"), str)
    ):
        raise ValueError("Runtime metadata belongs to another configuration")
    return record


def _same_process(record):
    try:
        process = psutil.Process(record["pid"])
        return abs(process.create_time() - record["created"]) < 0.01 and os.path.normcase(
            process.exe()
        ) == os.path.normcase(record["exe"])
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied as exc:
        raise ValueError("Cannot verify runtime process identity; refusing to control it") from exc


def _signature(secret, purpose, content):
    text = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hmac.new(secret.encode(), (purpose + "\0" + text).encode(), hashlib.sha256).hexdigest()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Runtime control refuses redirects")


def control_request(record, action):
    if not _same_process(record):
        raise ValueError("Runtime process is no longer the recorded owner")
    value = {"action": action, "nonce": secrets.token_hex(16), "timestamp": int(time.time())}
    body = {**value, "signature": _signature(record["secret"], "request", value)}
    url = f"http://127.0.0.1:{record['port']}/_runtime/control"
    request = Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Origin": url.rsplit("/_runtime", 1)[0]},
        method="POST",
    )
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=3) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("Runtime response is too large")
    envelope = json.loads(raw)
    data = envelope.get("data", {})
    if (
        data.get("nonce") != value["nonce"]
        or data.get("pid") != record["pid"]
        or not hmac.compare_digest(
            envelope.get("signature", ""), _signature(record["secret"], "response", data)
        )
    ):
        raise ValueError("Runtime control response could not be authenticated")
    return data


@contextmanager
def _launch_lock(settings):
    deadline = time.monotonic() + 25
    lock = SingleInstance("launcher:" + str(settings.state_dir))
    while True:
        try:
            lock.__enter__()
            break
        except AlreadyRunning:
            if time.monotonic() >= deadline:
                raise ValueError("Another launcher is busy; retry later") from None
            time.sleep(0.1)
    try:
        yield
    finally:
        lock.__exit__()


def _effective(settings):
    path = settings.state_dir / "ui-settings.json"
    assert_no_reparse(path)
    if path.is_file():
        from fabu.api.app import SettingsPatch

        patch = SettingsPatch.model_validate_json(path.read_text(encoding="utf-8"))
        return Settings.model_validate(
            {**settings.model_dump(), **patch.model_dump(exclude_none=True)}
        )
    return settings


def check_installation(settings):
    settings = _effective(settings)
    checks = []
    for name, path in [
        ("Python", Path(sys.executable)),
        ("FFmpeg", settings.tools_dir / "ffmpeg.exe"),
        ("ffprobe", settings.tools_dir / "ffprobe.exe"),
        ("Node.js", settings.node_path),
        ("前端", ROOT / "frontend/dist/index.html"),
    ]:
        checks.append(
            {
                "name": name,
                "path": str(path) if path else "未配置：请在运行设置中选择Node.js .exe",
                "ok": bool(path and path.is_file()),
            }
        )
    dependencies = {}
    for name in ["fastapi", "uvicorn", "yt-dlp", "yt-dlp-ejs", "pydantic", "pywin32"]:
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = "missing"
    return {
        "checks": checks,
        "dependencies": dependencies,
        "environment": check_environment(settings).model_dump(mode="json"),
        "publishing_enabled": False,
        "note": "Missing tools do not prevent opening settings. No downloads or upgrades.",
    }


def launch(settings: Settings, *, open_browser=True):
    path = runtime_path(settings)
    with _launch_lock(settings):
        record = None
        reused = False
        if path.exists():
            old = load_runtime_record(settings)
            if _same_process(old):
                data = control_request(old, "status")
                if not data.get("ready"):
                    raise ValueError("Existing runtime is not ready; inspect its local log")
                record = old
                reused = True
            else:
                path.unlink()
        if record is None:
            if not (ROOT / "frontend/dist/index.html").is_file():
                raise ValueError(f"Frontend build is missing: {ROOT / 'frontend/dist/index.html'}")
            try:
                with socket.socket() as sock:
                    sock.bind((settings.bind_host, settings.port))
            except OSError as exc:
                raise ValueError(
                    f"Local port {settings.port} is occupied; no other process was stopped"
                ) from exc
            secure_directory(path.parent)
            config = path.parent / ("launch-" + str(uuid4()) + ".dpapi")
            launch_secret = secrets.token_hex(32)
            _write_private(
                config,
                {"settings": settings.model_dump(mode="json"), "secret": launch_secret},
            )
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
            env["PYTHONUTF8"] = "1"
            child = subprocess.Popen(
                [sys.executable, "-m", "fabu.runtime", "--config", str(config)],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise ValueError(f"Fabu startup failed; see {path.parent / 'runtime.log'}")
                if path.exists():
                    candidate = load_runtime_record(settings)
                    try:
                        descendants = {
                            p.pid for p in psutil.Process(child.pid).children(recursive=True)
                        }
                    except psutil.NoSuchProcess:
                        descendants = set()
                    if candidate["pid"] not in {child.pid, *descendants} or not hmac.compare_digest(
                        candidate["secret"], launch_secret
                    ):
                        raise ValueError("Unexpected runtime owner; refusing takeover")
                    try:
                        if control_request(candidate, "status").get("ready"):
                            record = candidate
                            break
                    except (OSError, ValueError):
                        pass
                time.sleep(0.1)
            if record is None:
                # Startup timeout is not proof of failure; never spawn a second instance.
                raise ValueError(
                    f"Runtime is still starting; inspect {path.parent / 'runtime.log'}"
                )
        url = f"http://127.0.0.1:{settings.port}/"
        if open_browser:
            bootstrap = control_request(record, "bootstrap")["bootstrap"]
            webbrowser.open(url + "#bootstrap=" + bootstrap)
        return LaunchResult(
            pid=record["pid"], reused=reused, url=url, log_path=path.parent / "runtime.log"
        )


def stop_owned_runtime(settings: Settings):
    with _launch_lock(settings):
        path = runtime_path(settings)
        if not path.exists():
            return False
        record = load_runtime_record(settings)
        if not _same_process(record):
            path.unlink()
            return False
        control_request(record, "stop")
        deadline = time.monotonic() + 45
        while _same_process(record):
            if time.monotonic() >= deadline:
                raise ValueError("Fabu is still stopping owned work; no process was force-killed")
            time.sleep(0.1)
        if path.exists() and load_runtime_record(settings) == record:
            path.unlink()
        return True


def main():
    parser = argparse.ArgumentParser(description="Fabu local Windows launcher")
    parser.add_argument("action", choices=["start", "stop", "check"])
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        env = dict(os.environ)
        if args.action == "stop":
            # Control identity is state_dir + port, not media/tool configuration.
            # A moved Node executable or invalid data path must not block shutdown.
            for name in ("FABU_DATA_DIR", "FABU_TOOLS_DIR", "FABU_NODE_PATH", "FABU_TEST_MODE"):
                env.pop(name, None)
        settings = load_settings(env)
        if args.action == "start":
            result = launch(settings, open_browser=not args.no_browser).model_dump(mode="json")
        elif args.action == "stop":
            result = {"stopped": stop_owned_runtime(settings)}
        else:
            result = check_installation(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValidationError as exc:
        # Pydantic's string form includes input values; never echo configuration secrets.
        fields = sorted(
            {
                "FABU_" + str(error["loc"][0]).upper()
                for error in exc.errors(
                    include_input=False, include_context=False, include_url=False
                )
                if error["loc"] and error["loc"][0] in Settings.model_fields
            }
        )
        names = ", ".join(fields) or "FABU_* environment variables and local settings"
        print(json.dumps({"error": "Invalid launcher configuration; check " + names}))
        raise SystemExit(1) from None
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
