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
        parent = psutil.Process(result.pid).parent()
        runtime_parent = parent if parent and "fabu.runtime" in parent.cmdline() else None
        assert stop_owned_runtime(runtime_settings)
        assert innocent.poll() is None
        assert not psutil.pid_exists(result.pid)
        if runtime_parent:
            runtime_parent.wait(timeout=5)
        with socket.socket() as sock:
            assert sock.connect_ex(("127.0.0.1", runtime_settings.port)) != 0
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


@pytest.mark.parametrize("redirected", [False, True], ids=["direct", "msix-merged-view"])
def test_runtime_reads_saved_settings_credentials_and_frozen_job(
    runtime_settings, tmp_path, monkeypatch, redirected
):
    import json

    from fabu.credentials import Credentials, CredentialStore
    from fabu.domain import JobSpec
    from fabu.launcher import control_request, launch, load_runtime_record
    from fabu.store import JobStore

    store = JobStore(runtime_settings.db_path)
    job = store.create(
        JobSpec(
            source_url="https://www.youtube.com/watch?v=abcdefghijk",
            title="Isolated persistence fixture",
            preset_version_id="test-preset",
        )
    )
    job = store.transition(job.id, job.revision, "PAUSED", "test_pause")
    with store.transaction() as db:
        db.execute("UPDATE jobs SET content_frozen=1 WHERE id=?", (job.id,))
    expected_job = store.get(job.id).model_dump(mode="json")
    node = runtime_settings.state_dir / "configured-node.exe"
    node.write_bytes(b"test placeholder, never executed")
    patch = {
        "tools_dir": str(runtime_settings.state_dir / "configured-tools"),
        "node_path": str(node),
        "data_dir": str(runtime_settings.data_dir),
        "test_mode": "simulation",
    }
    patch_path = runtime_settings.state_dir / "ui-settings.json"
    patch_path.write_text(json.dumps(patch), encoding="utf-8")
    credentials = CredentialStore(runtime_settings.state_dir / "credentials")
    credentials.save(Credentials(api_key="synthetic-test-key", local_client_id="test-client"))
    before = patch_path.read_bytes(), credentials.path.read_bytes()
    requested = runtime_settings
    if redirected:
        logical = tmp_path / "merged-appdata" / "Fabu"
        logical.mkdir(parents=True)
        (logical / "fabu.sqlite3").write_bytes(b"do not initialize this logical alias")
        resolve = Path.resolve
        monkeypatch.setattr(
            Path,
            "resolve",
            lambda p, *a, **kw: (
                runtime_settings.db_path if p == logical / "fabu.sqlite3" else resolve(p, *a, **kw)
            ),
        )
        requested = runtime_settings.model_copy(
            update={
                "state_dir": logical,
                "tools_dir": logical / "tools",
            }
        )

    result = launch(requested, open_browser=False)
    record = load_runtime_record(requested)
    with httpx.Client(base_url=result.url, trust_env=False) as browser:
        response = browser.post(
            "api/session",
            headers={
                "Origin": result.url.rstrip("/"),
                "Authorization": "Bearer " + control_request(record, "bootstrap")["bootstrap"],
            },
        )
        assert response.status_code == 200
        settings = browser.get("api/settings").json()
        assert {key: settings[key] for key in patch} == patch
        assert settings["credentials"]["configured"] is True
        assert browser.get("api/jobs").json() == [expected_job]
    assert store.get(job.id).model_dump(mode="json") == expected_job
    assert (patch_path.read_bytes(), credentials.path.read_bytes()) == before
    if redirected:
        assert (logical / "fabu.sqlite3").read_bytes() == b"do not initialize this logical alias"


def test_runtime_does_not_import_a_shadow_package_from_cwd(runtime_settings, monkeypatch, tmp_path):
    from fabu import launcher

    # The parent uses -I; the runtime must retain that boundary too. A stray
    # package in a checkout must not rewrite storage/configuration reads.
    root = tmp_path / "checkout"
    (root / "frontend/dist").mkdir(parents=True)
    (root / "frontend/dist/index.html").write_text("test", encoding="utf-8")
    marker = tmp_path / "unexpected-shadow-import"
    package = root / "fabu"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from pathlib import Path\nPath("
        + repr(str(marker))
        + ").write_text('executed')\n"
        + "__path__.append("
        + repr(str(Path(launcher.__file__).parent))
        + ")\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "ROOT", root)
    try:
        launcher.launch(runtime_settings, open_browser=False)
    except ValueError as exc:
        # The synthetic checkout can be rejected on provenance as well, but its
        # Python package must never execute before that rejection.
        assert "identity" in str(exc)
    assert not marker.exists(), "Runtime dropped launcher import isolation"


def test_runtime_status_reports_actual_source_interpreter_and_storage(runtime_settings):
    from fabu.launcher import ROOT, control_request, launch, load_runtime_record

    launch(runtime_settings, open_browser=False)
    data = control_request(load_runtime_record(runtime_settings), "status")
    identity = data.get("identity", {})
    assert identity.get("source_root") == str(ROOT)
    assert identity.get("python_executable") == str(Path(sys.executable).resolve())
    assert identity.get("python_prefix") == str(Path(sys.prefix).resolve())
    assert identity.get("db_path") == str(runtime_settings.db_path.resolve())
    assert identity.get("state_dir") == str(runtime_settings.state_dir.resolve())
    assert identity.get("isolated") is True


def test_reuse_rejects_another_checkout_but_authenticated_stop_still_works(
    runtime_settings, monkeypatch, tmp_path
):
    from fabu import launcher

    first = launcher.launch(runtime_settings, open_browser=False)
    monkeypatch.setattr(launcher, "ROOT", tmp_path / "other-checkout")
    with pytest.raises(ValueError, match="identity|configuration|checkout"):
        launcher.launch(runtime_settings, open_browser=False)
    assert psutil.Process(first.pid).is_running()
    assert launcher.stop_owned_runtime(runtime_settings)


def test_check_resolves_redirected_state_before_reading_saved_settings(tmp_path, monkeypatch):
    import json

    from fabu.launcher import check_installation
    from fabu.settings import Settings

    logical = tmp_path / "logical-appdata" / "Fabu"
    physical = tmp_path / "package-cache" / "Fabu"
    logical.mkdir(parents=True)
    physical.mkdir(parents=True)
    tools = tmp_path / "saved-tools"
    tools.mkdir()
    (tools / "ffmpeg.exe").write_bytes(b"test")
    node = tmp_path / "saved-node.exe"
    node.write_bytes(b"test")
    (physical / "ui-settings.json").write_text(
        json.dumps(
            {
                "tools_dir": str(tools),
                "node_path": str(node),
            }
        ),
        encoding="utf-8",
    )
    # MSIX may resolve the directory to native AppData while redirecting its files.
    (logical / "fabu.sqlite3").write_bytes(b"logical file is visible")
    (physical / "fabu.sqlite3").write_bytes(b"physical backing file")
    resolve = Path.resolve
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda p, *a, **kw: (
            physical / "fabu.sqlite3" if p == logical / "fabu.sqlite3" else resolve(p, *a, **kw)
        ),
    )
    settings = Settings(state_dir=logical, data_dir=tmp_path / "data", tools_dir=logical / "tools")
    report = check_installation(settings)
    assert report["environment"]["paths"]["state_dir"]["path"] == str(physical)
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["Node.js"]["path"] == str(node)
    assert checks["FFmpeg"]["path"] == str(tools / "ffmpeg.exe")
    assert (logical / "fabu.sqlite3").read_bytes() == b"logical file is visible"


def test_launcher_profile_selects_one_physical_state_unless_explicitly_overridden(
    tmp_path, monkeypatch, capsys
):
    import json

    from fabu import launcher

    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    local = tmp_path / "appdata"
    selected = local / "package-cache" / "Fabu"
    (root / "scripts/start-fabu.profile.json").write_text(
        json.dumps(
            {
                "state_dir": "%LOCALAPPDATA%/package-cache/Fabu",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "ROOT", root)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    for key in ("FABU_STATE_DIR", "FABU_DATA_DIR", "FABU_TOOLS_DIR", "FABU_NODE_PATH", "FABU_PORT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, "argv", ["fabu.launcher", "check"])
    launcher.main()
    report = json.loads(capsys.readouterr().out)
    assert report["environment"]["paths"]["state_dir"]["path"] == str(selected)
    assert not selected.exists(), "Profile selection is read-only; never migrate/rebuild data"
    explicit = tmp_path / "explicit-state"
    monkeypatch.setenv("FABU_STATE_DIR", str(explicit))
    launcher.main()
    assert json.loads(capsys.readouterr().out)["environment"]["paths"]["state_dir"]["path"] == str(
        explicit
    )


def test_split_state_files_are_rejected_without_merging(tmp_path, monkeypatch):
    from fabu.launcher import check_installation
    from fabu.settings import Settings

    logical = tmp_path / "logical"
    backing = tmp_path / "backing"
    logical.mkdir()
    backing.mkdir()
    (logical / "fabu.sqlite3").write_bytes(b"keep-db")
    (logical / "ui-settings.json").write_text("{}", encoding="utf-8")
    (backing / "fabu.sqlite3").write_bytes(b"keep-other-db")
    resolve = Path.resolve
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda p, *a, **kw: (
            backing / "fabu.sqlite3" if p == logical / "fabu.sqlite3" else resolve(p, *a, **kw)
        ),
    )
    settings = Settings(
        state_dir=logical, data_dir=tmp_path / "media", tools_dir=tmp_path / "tools"
    )
    with pytest.raises(ValueError, match="storage|physical|split"):
        check_installation(settings)
    assert (logical / "fabu.sqlite3").read_bytes() == b"keep-db"
    assert (backing / "fabu.sqlite3").read_bytes() == b"keep-other-db"


def test_script_requested_checkout_cannot_silently_use_another_editable_install(
    tmp_path, monkeypatch, capsys
):
    import json

    from fabu import launcher

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fabu.launcher",
            "check",
            "--project-root",
            str(tmp_path / "different-checkout"),
        ],
    )
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 1
    assert "checkout" in json.loads(capsys.readouterr().out)["error"]


def test_profile_start_refuses_to_create_a_replacement_database(tmp_path, monkeypatch, capsys):
    import json

    from fabu import launcher

    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/start-fabu.profile.json").write_text(
        json.dumps(
            {
                "state_dir": str(tmp_path / "missing-existing-state"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "ROOT", root)
    monkeypatch.delenv("FABU_STATE_DIR", raising=False)
    monkeypatch.setattr(sys, "argv", ["fabu.launcher", "start", "--no-browser"])
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 1
    assert "existing state" in json.loads(capsys.readouterr().out)["error"]
    assert not (tmp_path / "missing-existing-state").exists()
