"""Exercise both Windows shells without policy bypass or production state access."""

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from fabu.launcher import launch, stop_owned_runtime
from fabu.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
SHELLS = [
    str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
    shutil.which("pwsh"),
]


@pytest.fixture(params=SHELLS, ids=["powershell-5.1", "powershell-7"])
def shell(request):
    if not request.param:
        pytest.skip("PowerShell 7 is not installed")
    return request.param


@pytest.fixture
def isolated(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings = Settings(
        state_dir=tmp_path / "状态 space",
        data_dir=tmp_path / "media",
        tools_dir=tmp_path / "tools",
        port=port,
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("FABU_") and key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    env.update(
        FABU_STATE_DIR=str(settings.state_dir),
        FABU_DATA_DIR=str(settings.data_dir),
        FABU_TOOLS_DIR=str(settings.tools_dir),
        FABU_PORT=str(settings.port),
        PYTHONUTF8="1",
    )
    yield settings, env
    stop_owned_runtime(settings)


def run_script(shell, name, env, *, cwd=ROOT, extra=(), direct=False):
    script = ROOT / "scripts" / name
    args = [shell, "-NoProfile"]
    if direct:

        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"

        args += [
            "-Command",
            "& "
            + quote(script)
            + " "
            + " ".join(v if v.startswith("-") else quote(v) for v in extra)
            + "; exit $LASTEXITCODE",
        ]
    else:
        args += ["-File", str(script), *extra]
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=55,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


@pytest.mark.parametrize("source", ["cwd", "pythonpath"])
def test_script_uses_its_venv_not_a_shadow_workspace(shell, isolated, tmp_path, source):
    settings, env = isolated
    shadow = tmp_path / "old workspace"
    package = shadow / "fabu"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "launcher.py").write_text(
        "print('{\"shadow_workspace\": true}')",
        encoding="utf-8",
    )
    cwd = shadow if source == "cwd" else ROOT
    if source == "pythonpath":
        env["PYTHONPATH"] = str(shadow)
    result = run_script(shell, "verify-installation.ps1", env, cwd=cwd)
    assert result.returncode == 0
    report = json.loads(result.stdout.lstrip("\ufeff"))
    assert "shadow_workspace" not in report
    assert report["publishing_enabled"] is False
    assert not settings.state_dir.exists()


@pytest.mark.parametrize("script", ["start-fabu.ps1", "stop-fabu.ps1", "verify-installation.ps1"])
def test_invalid_port_has_safe_configuration_error(shell, isolated, script):
    _, env = isolated
    sentinel = "sensitive-input-do-not-echo"
    env["FABU_PORT"] = sentinel
    result = run_script(shell, script, env)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert sentinel not in result.stdout + result.stderr
    report = json.loads(result.stdout.lstrip("\ufeff"))
    assert "configuration" in report["error"].lower()
    assert "FABU_PORT" in report["error"]


def test_stop_does_not_require_startup_media_configuration(shell, isolated):
    settings, env = isolated
    first = launch(settings, open_browser=False)
    # A tool was removed/moved after startup, or a stale shell inherited old paths.
    env["FABU_NODE_PATH"] = str(settings.state_dir / "removed-node.exe")
    env["FABU_DATA_DIR"] = "relative-invalid-data-directory"
    env["FABU_TOOLS_DIR"] = "relative-invalid-tools-directory"
    env["FABU_TEST_MODE"] = "invalid-startup-mode"
    result = run_script(shell, "stop-fabu.ps1", env)
    assert result.returncode == 0, "Stop must not require media tools or data configuration"
    assert json.loads(result.stdout.lstrip("\ufeff"))["stopped"]
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", settings.port)) != 0
    import psutil

    assert not psutil.pid_exists(first.pid)


@pytest.mark.parametrize("direct", [False, True], ids=["file", "call-operator"])
def test_script_start_stop_defaults_and_environment_override(shell, isolated, tmp_path, direct):
    settings, env = isolated
    # No -Port parameter: use the inherited value, not PowerShell's implicit int=0.
    first = run_script(
        shell, "start-fabu.ps1", env, cwd=tmp_path, extra=("-NoBrowser",), direct=direct
    )
    assert first.returncode == 0
    first_data = json.loads(first.stdout.lstrip("\ufeff"))
    assert first_data["log_path"] == str(settings.state_dir / "runtime" / "runtime.log")
    second = run_script(
        shell, "start-fabu.ps1", env, cwd=tmp_path, extra=("-NoBrowser",), direct=direct
    )
    assert second.returncode == 0
    second_data = json.loads(second.stdout.lstrip("\ufeff"))
    assert second_data["reused"] and second_data["pid"] == first_data["pid"]
    result = run_script(shell, "stop-fabu.ps1", env, cwd=tmp_path, direct=direct)
    assert result.returncode == 0
    assert json.loads(result.stdout.lstrip("\ufeff"))["stopped"]
    with socket.socket() as sock:
        assert sock.connect_ex(("127.0.0.1", settings.port)) != 0


def test_explicit_port_overrides_stale_environment(shell, isolated):
    settings, env = isolated
    env["FABU_PORT"] = "invalid-old-port"
    port_args = ("-Port", str(settings.port))
    result = run_script(shell, "start-fabu.ps1", env, extra=(*port_args, "-NoBrowser"))
    assert result.returncode == 0
    assert not json.loads(result.stdout.lstrip("\ufeff"))["reused"]
    result = run_script(shell, "stop-fabu.ps1", env, extra=port_args)
    assert result.returncode == 0
    assert json.loads(result.stdout.lstrip("\ufeff"))["stopped"]
