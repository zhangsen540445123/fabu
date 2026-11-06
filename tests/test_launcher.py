"""Real isolated loopback subprocesses, no downloads, browser launch, or external accounts."""

import socket
import subprocess
import sys
from pathlib import Path

import httpx
import psutil
import pytest


@pytest.fixture
def runtime_settings(tmp_path):
    from fabu.settings import Settings

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings = Settings(
        state_dir=tmp_path / "状态 space" / "state",
        data_dir=tmp_path / "media",
        tools_dir=tmp_path / "tools",
        port=port,
    )
    yield settings
    from fabu.launcher import stop_owned_runtime

    stop_owned_runtime(settings)


def test_second_launch_reuses_instance(runtime_settings):
    from fabu.launcher import launch

    first = launch(runtime_settings, open_browser=False)
    second = launch(runtime_settings, open_browser=False)
    assert second.reused and first.pid == second.pid
    with httpx.Client(trust_env=False) as client:
        assert client.get(first.url + "api/health").json() == {
            "status": "ok",
            "publishing_enabled": False,
        }
    assert psutil.Process(first.pid).is_running()
    assert "bootstrap" not in repr(first)


def test_missing_tool_explains_actual_path(runtime_settings):
    from fabu.launcher import check_installation

    result = check_installation(runtime_settings)
    missing = {v["path"] for v in result["checks"] if not v["ok"]}
    assert str(runtime_settings.tools_dir / "ffmpeg.exe") in missing
    assert str(runtime_settings.tools_dir / "ffprobe.exe") in missing
    assert not runtime_settings.state_dir.exists()  # read-only checks do not initialize data


def test_close_browser_keeps_worker_and_reopen_gets_fresh_session(runtime_settings):
    from fabu.launcher import control_request, launch, load_runtime_record

    result = launch(runtime_settings, open_browser=False)
    record = load_runtime_record(runtime_settings)
    session = control_request(record, "bootstrap")
    with httpx.Client(trust_env=False) as browser:
        response = browser.post(
            result.url + "api/session",
            headers={
                "Origin": result.url.rstrip("/"),
                "Authorization": "Bearer " + session["bootstrap"],
            },
        )
        assert response.status_code == 200
        status = browser.get(result.url + "api/media/status").json()
        assert status["running"]
    assert psutil.Process(result.pid).is_running()
    second = control_request(record, "bootstrap")
    assert second["bootstrap"] != session["bootstrap"]
    with httpx.Client(trust_env=False) as browser:
        response = browser.post(
            result.url + "api/session",
            headers={
                "Origin": result.url.rstrip("/"),
                "Authorization": "Bearer " + second["bootstrap"],
            },
        )
        assert response.status_code == 200


def test_stop_only_terminates_owned_processes(runtime_settings):
    from fabu.launcher import launch, stop_owned_runtime

    innocent = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(40)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        result = launch(runtime_settings, open_browser=False)
        assert stop_owned_runtime(runtime_settings)
        assert innocent.poll() is None
        assert not psutil.pid_exists(result.pid)
        assert not stop_owned_runtime(runtime_settings)
    finally:
        innocent.terminate()
        innocent.wait(timeout=10)


def test_unrelated_port_owner_is_not_controlled_or_replaced(runtime_settings):
    from fabu.launcher import launch

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", runtime_settings.port))
        sock.listen()
        with pytest.raises(ValueError, match="port|端口"):
            launch(runtime_settings, open_browser=False)


def test_control_endpoint_rejects_unsigned_and_cross_origin_requests(runtime_settings):
    from fabu.launcher import launch

    result = launch(runtime_settings, open_browser=False)
    with httpx.Client(trust_env=False) as client:
        response = client.post(result.url + "_runtime/control", json={"action": "stop"})
        assert response.status_code in {401, 403, 422}
        assert (
            client.post(
                result.url + "_runtime/control", json={}, headers={"Origin": "https://evil.invalid"}
            ).status_code
            == 403
        )
        assert client.get(result.url + "api/health").status_code == 200


def test_private_runtime_record_never_contains_plain_control_secret(runtime_settings):
    from fabu.launcher import launch, load_runtime_record, runtime_path

    launch(runtime_settings, open_browser=False)
    record = load_runtime_record(runtime_settings)
    assert record["secret"].encode() not in runtime_path(runtime_settings).read_bytes()
    assert record["secret"] not in repr(runtime_path(runtime_settings))


def test_pid_reuse_metadata_cannot_stop_unrelated_process(runtime_settings):
    from fabu.launcher import (
        _write_private,
        launch,
        load_runtime_record,
        runtime_path,
        stop_owned_runtime,
    )

    launch(runtime_settings, open_browser=False)
    record = load_runtime_record(runtime_settings)
    innocent = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(40)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        changed = {**record, "pid": innocent.pid, "created": 0.0}
        _write_private(runtime_path(runtime_settings), changed)
        assert not stop_owned_runtime(runtime_settings)
        assert innocent.poll() is None
    finally:
        _write_private(runtime_path(runtime_settings), record)
        innocent.terminate()
        innocent.wait(timeout=10)


def test_replayed_control_message_is_rejected(runtime_settings):
    import time

    from fabu.launcher import _signature, launch, load_runtime_record

    result = launch(runtime_settings, open_browser=False)
    record = load_runtime_record(runtime_settings)
    command = {"action": "bootstrap", "nonce": "a" * 32, "timestamp": int(time.time())}
    body = {**command, "signature": _signature(record["secret"], "request", command)}
    with httpx.Client(trust_env=False) as client:
        first = client.post(result.url + "_runtime/control", json=body)
        second = client.post(result.url + "_runtime/control", json=body)
        assert first.status_code == 200 and second.status_code == 403


def test_concurrent_launch_is_serialized(runtime_settings):
    from concurrent.futures import ThreadPoolExecutor

    from fabu.launcher import launch

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(launch, runtime_settings, open_browser=False) for _ in range(2)]
        results = [f.result(timeout=35) for f in futures]
    assert results[0].pid == results[1].pid
    assert sum(r.reused for r in results) == 1


def test_successful_runtime_has_sanitized_local_log(runtime_settings):
    from fabu.launcher import launch, load_runtime_record

    result = launch(runtime_settings, open_browser=False)
    assert result.log_path.is_file()
    text = result.log_path.read_text(encoding="utf-8")
    assert "ready" in text
    assert load_runtime_record(runtime_settings)["secret"] not in text


def test_launcher_failure_does_not_start_second_worker(runtime_settings):
    from fabu.launcher import launch
    from fabu.store import JobStore
    from fabu.worker import Worker

    with Worker(JobStore(runtime_settings.db_path)):
        with pytest.raises(ValueError, match="startup failed"):
            launch(runtime_settings, open_browser=False)


def test_powershell_scripts_work_with_spaces_and_reuse_instance(runtime_settings):
    import json
    import os

    root = Path(__file__).resolve().parents[1]
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"

    def run(name, extra=()):
        result = subprocess.run(
            [
                str(shell),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "scripts" / name),
                "-StateDir",
                str(runtime_settings.state_dir),
                "-Port",
                str(runtime_settings.port),
                *extra,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=40,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout.lstrip("\ufeff"))

    report = run(
        "verify-installation.ps1",
        ["-DataDir", str(runtime_settings.data_dir), "-ToolsDir", str(runtime_settings.tools_dir)],
    )
    assert report["publishing_enabled"] is False
    first = run("start-fabu.ps1", ["-NoBrowser", "-DataDir", str(runtime_settings.data_dir)])
    second = run("start-fabu.ps1", ["-NoBrowser"])
    assert first["pid"] == second["pid"] and second["reused"]
    assert run("stop-fabu.ps1")["stopped"]
