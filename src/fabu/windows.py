"""Windows boundaries: verified shortcut launch, process ownership, bounded read-only UIA."""

from __future__ import annotations

import json
import ntpath
import os
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from .client import ConfigurationError, Control, Observation, Shortcut, WindowSnapshot
from .safety import diagnostic_report


class ShortcutResolver(Protocol):
    def resolve(self, path: Path) -> Shortcut: ...


def require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("This operation requires Windows")


def same_executable(left: str | Path | None, right: str | Path) -> bool:
    # Normalize Windows paths, never identify a process by basename alone.
    if not left:
        return False
    return ntpath.normcase(ntpath.abspath(str(left))) == ntpath.normcase(ntpath.abspath(str(right)))


def discover_shortcut(
    explicit: Path | None = None, *, desktop_directories: list[Path] | None = None
) -> Path:
    if explicit is not None:
        if not explicit.is_file() or explicit.suffix.lower() != ".lnk":
            raise ConfigurationError("Select an existing .lnk shortcut")
        return explicit.resolve()
    if desktop_directories is None:
        require_windows()
        from win32com.shell import shell, shellcon

        desktop_directories = [
            Path(shell.SHGetFolderPath(0, csidl, None, 0))
            for csidl in (shellcon.CSIDL_DESKTOPDIRECTORY, shellcon.CSIDL_COMMON_DESKTOPDIRECTORY)
        ]
    matches = {
        p.resolve()
        for folder in desktop_directories
        if folder.is_dir()
        for p in folder.glob("*.lnk")
        if "蚁小二" in p.stem or "yixiaoer" in p.stem.lower()
    }
    if len(matches) != 1:
        raise ConfigurationError("Expected exactly one client shortcut; select its explicit path")
    return next(iter(matches))


class WindowsShortcutResolver:
    def resolve(self, path: Path) -> Shortcut:
        require_windows()
        import pythoncom
        from win32com.shell import shell

        pythoncom.CoInitialize()
        try:
            link = pythoncom.CoCreateInstance(
                shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
            )
            link.QueryInterface(pythoncom.IID_IPersistFile).Load(str(path))
            target, _ = link.GetPath(shell.SLGP_RAWPATH)
            target_path = Path(os.path.expandvars(target))
            directory = os.path.expandvars(link.GetWorkingDirectory())
            return Shortcut(
                path.resolve(),
                target_path,
                Path(directory) if directory else target_path.parent,
                link.GetArguments(),
            )
        finally:
            pythoncom.CoUninitialize()


def resolve_shortcut(path: Path, *, resolver: ShortcutResolver | None = None) -> Shortcut:
    if path.suffix.lower() != ".lnk" or not path.is_file():
        raise ConfigurationError("Invalid shortcut")
    return (resolver or WindowsShortcutResolver()).resolve(path)


def shell_open(link: Path, directory: Path) -> None:
    require_windows()
    import pythoncom
    import win32api
    import win32con
    from win32com.shell import shell, shellcon

    pythoncom.CoInitialize()
    try:
        info = shell.ShellExecuteEx(
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS | shellcon.SEE_MASK_FLAG_NO_UI,
            lpVerb="open",
            lpFile=str(link),
            lpDirectory=str(directory),
            nShow=win32con.SW_SHOWNORMAL,
        )
        handle = info.get("hProcess")
        if handle:
            win32api.CloseHandle(handle)
    finally:
        pythoncom.CoUninitialize()


def desktop_locked() -> bool | None:
    require_windows()
    import win32service

    try:
        desktop = win32service.OpenInputDesktop(0, False, 1)
        try:
            return win32service.GetUserObjectInformation(desktop, 2) != "Default"
        finally:
            desktop.CloseDesktop()
    except Exception:
        # No permission to inspect the input desktop is not proof of an unlocked session.
        return None


def decode_control(data: dict, *, depth: int = 0, budget: list[int] | None = None) -> Control:
    if budget is None:
        budget = [8000]
    budget[0] -= 1
    if depth > 64 or budget[0] < 0 or not isinstance(data, dict):
        raise ValueError("Invalid bounded control tree")
    names = ("name", "control_type", "automation_id", "value")
    if any(
        not isinstance(data.get(name, ""), str) or len(data.get(name, "")) > 2048 for name in names
    ):
        raise ValueError("Invalid control metadata")
    for name in ("visible", "enabled"):
        if type(data.get(name, True)) is not bool:
            raise ValueError("Invalid control availability")
    children = data.get("children", [])
    if not isinstance(children, list):
        raise ValueError("Invalid child list")
    return Control(
        **{name: data.get(name, "") for name in names},
        visible=data.get("visible", True),
        enabled=data.get("enabled", True),
        children=tuple(decode_control(child, depth=depth + 1, budget=budget) for child in children),
    )


def read_window_snapshot(
    hwnd: int, pid: int, executable: Path, *, timeout: float = 12
) -> WindowSnapshot:
    # This helper owns no target process. A timed-out probe kills only its own Python child.
    command = [sys.executable, "-m", "fabu.probe", "--hwnd", str(hwnd), "--pid", str(pid)]
    try:
        response = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="strict",
            timeout=min(12, timeout),
            check=False,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        if response.returncode or len(response.stdout) > 4_000_000:
            raise ValueError("Probe failed")
        data = json.loads(response.stdout)
        if data.get("hwnd") != hwnd or data.get("pid") != pid:
            raise ValueError("Probe belongs to a different window")
        if (
            type(data.get("truncated", False)) is not bool
            or not isinstance(data.get("error", ""), str)
            or not isinstance(data.get("controls"), list)
        ):
            raise ValueError("Invalid probe status")
        budget = [8000]
        return WindowSnapshot(
            hwnd,
            pid,
            executable,
            tuple(decode_control(c, budget=budget) for c in data["controls"]),
            truncated=data.get("truncated", False),
            error=data.get("error", ""),
        )
    except subprocess.TimeoutExpired:
        return WindowSnapshot(hwnd, pid, executable, error="probe_timeout")
    except Exception:
        return WindowSnapshot(hwnd, pid, executable, error="probe_failed")


class WindowsClientHost:
    def __init__(self, *, shell_opener: Callable[[Path, Path], None] | None = None):
        self.shell_opener = shell_opener
        self._approved_windows: dict[int, tuple[int, Path]] = {}

    def launch(self, shortcut: Shortcut) -> None:
        # Open the link itself: Windows preserves its configured arguments and working directory.
        if self.shell_opener is not None:
            self.shell_opener(shortcut.path, shortcut.working_directory)
            return
        if desktop_locked() is not False:
            raise RuntimeError("Cannot start on an unverified desktop")
        current = resolve_shortcut(shortcut.path)
        if (
            not same_executable(current.target, shortcut.target)
            or current.arguments != shortcut.arguments
            or current.working_directory != shortcut.working_directory
        ):
            raise ConfigurationError("Shortcut changed after inspection")
        shell_open(shortcut.path, shortcut.working_directory)

    def observe(self, shortcut: Shortcut, *, timeout: float = 12) -> Observation:
        require_windows()
        import psutil
        import win32gui
        import win32process

        deadline = time.monotonic() + min(timeout, 12)
        self._approved_windows.clear()
        locked = desktop_locked()
        if locked is not False:
            return Observation(desktop_locked=locked)
        ids = []
        inaccessible = False
        for process in psutil.process_iter():
            try:
                if same_executable(process.exe(), shortcut.target):
                    ids.append(process.pid)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                try:
                    if process.name().casefold() == shortcut.target.name.casefold():
                        inaccessible = True
                except psutil.Error:
                    inaccessible = True
        windows = []

        def collect(hwnd, _):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in ids:
                if win32gui.GetWindowText(hwnd) == shortcut.target.stem:
                    windows.append((hwnd, pid))

        win32gui.EnumWindows(collect, None)
        if inaccessible:
            return Observation(tuple(ids), desktop_locked=False, error="process_access_denied")
        self._approved_windows = {hwnd: (pid, shortcut.target) for hwnd, pid in windows}
        if len(windows) > 1:
            snapshots = tuple(
                WindowSnapshot(hwnd, pid, shortcut.target) for hwnd, pid in windows[:2]
            )
        else:
            snapshots = tuple(
                read_window_snapshot(
                    hwnd, pid, shortcut.target, timeout=max(0.01, deadline - time.monotonic())
                )
                for hwnd, pid in windows
            )
        return Observation(tuple(ids), snapshots, False)

    def inspect(self, shortcut: Shortcut) -> dict:
        return diagnostic_report(self.observe(shortcut))

    def restore(self, hwnd: int) -> None:
        require_windows()
        import psutil
        import win32con
        import win32gui
        import win32process

        if desktop_locked() is not False:
            raise RuntimeError("Desktop cannot be safely controlled")
        expected = self._approved_windows.get(hwnd)
        if not expected or not win32gui.IsWindow(hwnd):
            raise RuntimeError("Stale or unapproved window")
        _, owner = win32process.GetWindowThreadProcessId(hwnd)
        if owner != expected[0] or not same_executable(psutil.Process(owner).exe(), expected[1]):
            raise RuntimeError("Window ownership changed")
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        if win32gui.GetForegroundWindow() != hwnd:
            raise RuntimeError("Window did not become foreground")

    def open_shortcut_via_uia(self, shortcut: Shortcut) -> bool:
        # Reserved fallback: disabled until a deadline-bound, revalidating helper is tested.
        # Never execute unbounded provider calls in the controller process.
        return False


class ClientBusyError(RuntimeError):
    pass


@contextmanager
def client_mutex():
    """Only this process's acquired mutex is released; never terminate another controller."""
    require_windows()
    import win32api
    import win32event

    handle = win32event.CreateMutex(None, False, "Local\\Fabu.YixiaoerController")
    owned = False
    try:
        status = win32event.WaitForSingleObject(handle, 0)
        if status not in (0, 128):
            raise ClientBusyError("Another client controller owns the desktop")
        owned = True
        yield
    finally:
        if owned:
            win32event.ReleaseMutex(handle)
        win32api.CloseHandle(handle)
