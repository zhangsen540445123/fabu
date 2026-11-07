"""Run desktop batch entries without touching real Fabu state or opening browsers."""

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_batch(path, env, cwd):
    shell = Path(os.environ["SystemRoot"]) / "System32/cmd.exe"
    return subprocess.run(
        f'"{shell}" /d /s /c ""{path}""',
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=55,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


@pytest.fixture
def desktop_env(tmp_path):
    env = dict(os.environ)
    local = tmp_path / "local space & !"
    local.mkdir()
    # The desktop profile pins the existing physical store, including outside
    # the packaged Codex file view. Keep that store wholly inside this test.
    from fabu.store import JobStore

    selected = local / "Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/Fabu"
    JobStore(selected / "fabu.sqlite3")
    env.update(
        LOCALAPPDATA=str(local),
        FABU_STATE_DIR=str(tmp_path / "unrelated-state"),
        FABU_PORT="bad-stale-port",
        FABU_BIND_HOST="0.0.0.0",
        FABU_NODE_PATH=str(tmp_path / "missing-node.exe"),
        FABU_DATA_DIR="invalid-relative-path",
        FABU_TOOLS_DIR="invalid-relative-path",
        FABU_TEST_MODE="invalid-mode",
        PYTHONPATH=str(tmp_path / "shadow"),
        PYTHONHOME="invalid-python-home",
    )
    return local, env


def test_desktop_stop_pins_identity_and_does_not_use_stale_shell_config(desktop_env, tmp_path):
    local, env = desktop_env
    script = ROOT / "scripts/stop-fabu.bat"
    assert script.is_file(), "Desktop stop entry has not been implemented"
    result = run_batch(script, env, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip())["stopped"] is False
    assert not (local / "Fabu/runtime/owner.dpapi").exists()
    assert not (tmp_path / "unrelated-state").exists()


@pytest.mark.parametrize("name", ["start-fabu.bat", "stop-fabu.bat"])
def test_desktop_entry_handles_missing_python_and_shell_metacharacters(tmp_path, desktop_env, name):
    _, env = desktop_env
    source = ROOT / "scripts" / name
    assert source.is_file(), "Desktop entry has not been implemented"
    destination = tmp_path / "project space & !" / "scripts" / name
    destination.parent.mkdir(parents=True)
    shutil.copyfile(source, destination)
    result = run_batch(destination, env, tmp_path)
    assert result.returncode != 0
    assert "Python environment missing" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_desktop_start_rejects_unrelated_port_owner_without_opening_browser(desktop_env, tmp_path):
    _, env = desktop_env
    script = ROOT / "scripts/start-fabu.bat"
    assert script.is_file(), "Desktop start entry has not been implemented"
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", 8765))
        except OSError:
            pytest.skip("A real service owns 8765; never stop it for this isolated test")
        listener.listen(1)
        result = run_batch(script, env, tmp_path)
        assert result.returncode != 0
        assert "8765 is occupied" in result.stdout
        assert "Traceback" not in result.stdout + result.stderr
        with socket.create_connection(("127.0.0.1", 8765), timeout=2):
            pass
