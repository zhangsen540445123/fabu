import importlib

import pytest


def module():
    try:
        return importlib.import_module("fabu.windows")
    except ModuleNotFoundError:
        pytest.fail("Windows adapter not implemented", pytrace=False)


def test_exact_normalized_process_path_not_name():
    w = module()
    assert w.same_executable(r"D:\Apps\蚁小二.EXE", r"d:\apps\蚁小二.exe")
    assert not w.same_executable(r"C:\fake\蚁小二.exe", r"D:\Apps\蚁小二.exe")
    assert not w.same_executable(None, r"D:\Apps\蚁小二.exe")


def test_shortcut_resolution_keeps_arguments_and_working_directory(tmp_path):
    w = module()
    from fabu.client import Shortcut

    path = tmp_path / "程序.lnk"
    path.write_bytes(b"fixture")

    class Resolver:
        def resolve(self, value):
            assert value == path
            return Shortcut(path, tmp_path / "client.exe", tmp_path / "安装目录", "--custom")

    result = w.resolve_shortcut(path, resolver=Resolver())
    assert result.arguments == "--custom"
    assert result.working_directory == tmp_path / "安装目录"


def test_shortcut_discovery_does_not_guess_multiple(tmp_path):
    w = module()
    for name in ["蚁小二.lnk", "蚁小二4.0.lnk"]:
        (tmp_path / name).touch()
    with pytest.raises(w.ConfigurationError):
        w.discover_shortcut(desktop_directories=[tmp_path])


def test_shortcut_discovery_honors_explicit_path(tmp_path):
    w = module()
    path = tmp_path / "选择的客户端.lnk"
    path.touch()
    assert w.discover_shortcut(path, desktop_directories=[]) == path


def test_importing_native_module_has_no_windows_side_effects():
    w = module()
    assert callable(w.WindowsClientHost)


def test_shell_launch_uses_link_and_explicit_directory(tmp_path):
    w = module()
    from fabu.client import Shortcut

    calls = []
    shortcut = Shortcut(tmp_path / "程序.lnk", tmp_path / "程序.exe", tmp_path, "--custom")
    host = w.WindowsClientHost(shell_opener=lambda link, directory: calls.append((link, directory)))
    host.launch(shortcut)
    assert calls == [(shortcut.path, shortcut.working_directory)]


def test_probe_timeout_cannot_be_readiness_success(tmp_path, monkeypatch):
    w = module()
    import subprocess

    def expired(*args, **kwargs):
        raise subprocess.TimeoutExpired("only-owned-helper", kwargs["timeout"])

    monkeypatch.setattr(w.subprocess, "run", expired)
    snap = w.read_window_snapshot(17, 42, tmp_path / "client.exe", timeout=0.1)
    assert snap.error == "probe_timeout"
    assert not snap.controls


def test_probe_does_not_accept_bad_or_wrong_window_data(tmp_path, monkeypatch):
    w = module()
    import subprocess

    for output in ["not JSON", '{"hwnd":18,"pid":42,"controls":[]}']:
        monkeypatch.setattr(
            w.subprocess,
            "run",
            lambda *a, _out=output, **k: subprocess.CompletedProcess(a[0], 0, _out, ""),
        )
        assert w.read_window_snapshot(17, 42, tmp_path / "client.exe").error


def test_probe_reader_is_child_process_no_shell(tmp_path, monkeypatch):
    w = module()
    import subprocess

    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '{"hwnd":17,"pid":42,"controls":[]}', "")

    monkeypatch.setattr(w.subprocess, "run", run)
    w.read_window_snapshot(17, 42, tmp_path / "client.exe")
    assert isinstance(calls[0][0], list)
    assert not calls[0][1].get("shell", False)
    assert calls[0][1]["timeout"] <= 12


def test_read_only_inspection_never_launches(tmp_path, monkeypatch):
    w = module()
    from fabu.client import Observation, Shortcut

    calls = []
    host = w.WindowsClientHost(shell_opener=lambda *args: calls.append(args))
    monkeypatch.setattr(host, "observe", lambda shortcut: Observation(desktop_locked=False))
    shortcut = Shortcut(tmp_path / "程序.lnk", tmp_path / "程序.exe", tmp_path)
    report = host.inspect(shortcut)
    assert report["state"] == "not_running"
    assert calls == []


def test_desktop_lock_check_uses_public_win32_function(monkeypatch):
    w = module()
    import sys
    from types import SimpleNamespace

    calls = []

    class Handle:
        def CloseDesktop(self):
            calls.append("closed")

    handle = Handle()
    api = SimpleNamespace(
        OpenInputDesktop=lambda *args: handle,
        GetUserObjectInformation=lambda actual, index: "Default",
    )
    monkeypatch.setitem(sys.modules, "win32service", api)
    monkeypatch.setattr(w.sys, "platform", "win32")
    assert w.desktop_locked() is False
    assert calls == ["closed"]


def test_probe_failure_reports_reason_not_sensitive_exception(tmp_path, monkeypatch):
    w = module()

    def error(*args, **kwargs):
        raise RuntimeError("token=secret-value")

    monkeypatch.setattr(w.subprocess, "run", error)
    snap = w.read_window_snapshot(17, 42, tmp_path / "client.exe")
    assert snap.error == "probe_failed"
    assert "secret-value" not in str(snap)


def test_named_mutex_releases_only_owned_lock(monkeypatch):
    w = module()
    import sys
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(w.sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "win32event",
        SimpleNamespace(
            CreateMutex=lambda *args: 77,
            WaitForSingleObject=lambda handle, timeout: 0,
            ReleaseMutex=lambda handle: calls.append(("release", handle)),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32api",
        SimpleNamespace(CloseHandle=lambda handle: calls.append(("close", handle))),
    )
    with w.client_mutex():
        pass
    assert calls == [("release", 77), ("close", 77)]


def test_busy_named_mutex_does_not_release_someone_elses_lock(monkeypatch):
    w = module()
    import sys
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(w.sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "win32event",
        SimpleNamespace(
            CreateMutex=lambda *args: 77,
            WaitForSingleObject=lambda handle, timeout: 258,
            ReleaseMutex=lambda handle: calls.append(("release", handle)),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32api",
        SimpleNamespace(CloseHandle=lambda handle: calls.append(("close", handle))),
    )
    with pytest.raises(w.ClientBusyError):
        with w.client_mutex():
            pytest.fail("busy lock entered")
    assert calls == [("close", 77)]


def test_failed_shell_rechecks_shortcut_target_before_launch(tmp_path, monkeypatch):
    w = module()
    from fabu.client import Shortcut

    calls = []
    trusted = Shortcut(tmp_path / "client.lnk", tmp_path / "client.exe", tmp_path)
    changed = Shortcut(trusted.path, tmp_path / "other.exe", tmp_path)
    monkeypatch.setattr(w, "resolve_shortcut", lambda path: changed)
    monkeypatch.setattr(w, "desktop_locked", lambda: False)
    monkeypatch.setattr(w, "shell_open", lambda *args: calls.append(args))
    host = w.WindowsClientHost()
    with pytest.raises(w.ConfigurationError):
        host.launch(trusted)
    assert calls == []


def test_hidden_tray_window_is_discovered_without_second_launch(tmp_path, monkeypatch):
    w = module()
    import sys
    from types import SimpleNamespace

    from fabu.client import Shortcut, WindowSnapshot

    target = tmp_path / "蚁小二4.0.exe"

    class Process:
        pid = 42

        def exe(self):
            return str(target)

    monkeypatch.setattr(w.sys, "platform", "win32")
    monkeypatch.setattr(w, "desktop_locked", lambda: False)
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(
            process_iter=lambda: [Process()],
            NoSuchProcess=ProcessLookupError,
            AccessDenied=PermissionError,
            Error=OSError,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            EnumWindows=lambda callback, value: callback(17, value),
            IsWindowVisible=lambda handle: False,
            GetWindowText=lambda handle: "蚁小二4.0",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda handle: (1, 42)),
    )
    monkeypatch.setattr(
        w, "read_window_snapshot", lambda hwnd, pid, exe, **kwargs: WindowSnapshot(hwnd, pid, exe)
    )
    observed = w.WindowsClientHost().observe(Shortcut(tmp_path / "client.lnk", target, tmp_path))
    assert observed.windows[0].hwnd == 17


def test_probe_rejects_invalid_completeness_flags(tmp_path, monkeypatch):
    w = module()
    import subprocess

    for extra in ['"truncated":"false"', '"error":123', '"controls":null']:
        output = '{"hwnd":17,"pid":42,"controls":[],' + extra + "}"
        monkeypatch.setattr(
            w.subprocess,
            "run",
            lambda *args, _out=output, **kwargs: subprocess.CompletedProcess(args[0], 0, _out, ""),
        )
        assert w.read_window_snapshot(17, 42, tmp_path / "client.exe").error == "probe_failed"


def test_native_fallback_is_disabled_until_safe_bounded_helper_exists(tmp_path):
    w = module()
    from fabu.client import Shortcut

    shortcut = Shortcut(tmp_path / "client.lnk", tmp_path / "client.exe", tmp_path)
    assert w.WindowsClientHost().open_shortcut_via_uia(shortcut) is False
