"""Pure, fail-closed client readiness and launch policy; no UI side effects on import."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """A verified launch configuration changed; never retry via a weaker path."""


class ClientState(StrEnum):
    NOT_RUNNING = "not_running"
    WINDOW_UNAVAILABLE = "window_unavailable"
    UI_UNAVAILABLE = "ui_unavailable"
    START_TIMEOUT = "start_timeout"
    READY = "ready"
    LOGIN_REQUIRED = "login_required"
    TASK_CONFLICT = "task_conflict"
    DESKTOP_LOCKED = "desktop_locked"
    AMBIGUOUS = "ambiguous"
    INSPECTION_FAILED = "inspection_failed"
    CONFIG_ERROR = "config_error"


@dataclass(frozen=True)
class Control:
    name: str = ""
    control_type: str = ""
    automation_id: str = ""
    value: str = ""
    enabled: bool = True
    visible: bool = True
    children: tuple[Control, ...] = ()

    def walk(self) -> Iterator[Control]:
        if not self.visible or not self.enabled:
            return
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass(frozen=True)
class WindowSnapshot:
    hwnd: int
    pid: int
    executable: Path
    controls: tuple[Control, ...] = ()
    truncated: bool = False
    error: str = ""


@dataclass(frozen=True)
class Observation:
    process_ids: tuple[int, ...] = ()
    windows: tuple[WindowSnapshot, ...] = ()
    desktop_locked: bool | None = None
    error: str = ""


@dataclass(frozen=True)
class Shortcut:
    path: Path
    target: Path
    working_directory: Path
    arguments: str = ""


@dataclass(frozen=True)
class Readiness:
    state: ClientState
    reason: str
    scene: str = "unknown"
    hwnd: int | None = None


REASONS = {
    ClientState.NOT_RUNNING: "蚁小二尚未运行。",
    ClientState.WINDOW_UNAVAILABLE: "客户端进程存在，但没有可操作主窗口；不会重复启动。",
    ClientState.UI_UNAVAILABLE: "只读到客户端外壳或不完整页面，不能安全定位投稿控件。",
    ClientState.START_TIMEOUT: "客户端未在90秒预算内完成启动。",
    ClientState.READY: "内嵌页面可读取；这不代表发布接入已验证或投稿已提交。",
    ClientState.LOGIN_REQUIRED: "需要你在客户端完成登录或验证。",
    ClientState.TASK_CONFLICT: "发现既存未完成任务，不能覆盖、继续或删除。",
    ClientState.DESKTOP_LOCKED: "桌面锁定，停止界面操作。",
    ClientState.AMBIGUOUS: "存在多个客户端窗口，不能猜测操作对象。",
    ClientState.INSPECTION_FAILED: "无法完整核验客户端状态，停止操作。",
    ClientState.CONFIG_ERROR: "快捷方式或目标程序配置无效。",
}


def result(state: ClientState, *, scene: str = "unknown", hwnd: int | None = None) -> Readiness:
    return Readiness(state, REASONS[state], scene, hwnd)


def web_document(control: Control) -> bool:
    if control.control_type != "Document" or not control.enabled:
        return False
    try:
        url = urlsplit(control.value)
        return (
            url.scheme == "https"
            and url.hostname == "www.yixiaoer.cn"
            and url.path.rstrip("/") == "/web"
            and url.port in (None, 443)
        )
    except ValueError:
        return False


def embedded_nodes(window: WindowSnapshot) -> tuple[Control, ...]:
    roots = [n for c in window.controls for n in c.walk() if web_document(n)]
    return tuple(n for root in roots for child in root.children for n in child.walk())


def classify(observation: Observation) -> Readiness:
    if observation.desktop_locked is True:
        return result(ClientState.DESKTOP_LOCKED)
    if observation.desktop_locked is None or observation.error:
        return result(ClientState.INSPECTION_FAILED)
    if len(observation.windows) > 1:
        return result(ClientState.AMBIGUOUS)
    if observation.windows and observation.windows[0].pid not in observation.process_ids:
        return result(ClientState.INSPECTION_FAILED)
    if not observation.process_ids:
        return result(ClientState.NOT_RUNNING)
    if not observation.windows:
        return result(ClientState.WINDOW_UNAVAILABLE)
    window = observation.windows[0]
    if window.error or window.truncated:
        return result(ClientState.INSPECTION_FAILED)
    nodes = embedded_nodes(window)
    names = {n.name.strip() for n in nodes if n.enabled}
    if not nodes:
        return result(ClientState.UI_UNAVAILABLE, hwnd=window.hwnd)
    conflicts = ("检测到未完成的视频", "继续上传", "继续编辑关闭前的内容")
    if any(any(marker in name for marker in conflicts) for name in names):
        return result(ClientState.TASK_CONFLICT, hwnd=window.hwnd)
    if any(n.control_type == "Edit" for n in nodes) and names.intersection(
        {"登录", "扫码登录", "验证码", "手机号"}
    ):
        return result(ClientState.LOGIN_REQUIRED, scene="login", hwnd=window.hwnd)
    scenes = {
        "accounts": {"账号管理", "授权账号"},
        "publish": {"视频发布", "标题"},
        "records": {"发布记录", "任务列表"},
        "home": {"主页", "开始发布", "账号"},
    }
    for scene, required in scenes.items():
        if required.issubset(names):
            return result(ClientState.READY, scene=scene, hwnd=window.hwnd)
    return result(ClientState.UI_UNAVAILABLE, hwnd=window.hwnd)


class ClientHost(Protocol):
    def observe(self, shortcut: Shortcut, *, timeout: float = 12) -> Observation: ...
    def launch(self, shortcut: Shortcut) -> None: ...
    def restore(self, hwnd: int) -> None: ...
    def open_shortcut_via_uia(self, shortcut: Shortcut) -> bool: ...


class ClientLauncher:
    """90-second total budget, at most two starts, never terminate the target application."""

    def __init__(
        self,
        host: ClientHost,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 90,
        poll_interval: float = 1,
    ):
        if not 0 < timeout <= 90 or not 0 < poll_interval:
            raise ValueError("Invalid bounded timeout/poll interval")
        self.host = host
        self.clock = clock
        self.sleep = sleep
        self.timeout = timeout
        self.poll_interval = min(poll_interval, timeout)

    def ensure_ready(self, shortcut: Shortcut) -> Readiness:
        if (
            shortcut.path.suffix.lower() != ".lnk"
            or not shortcut.path.is_file()
            or shortcut.target.suffix.lower() != ".exe"
            or not shortcut.target.is_file()
            or not shortcut.working_directory.is_dir()
        ):
            return result(ClientState.CONFIG_ERROR)
        started = self.clock()
        deadline = started + self.timeout
        launches = 0
        restored: set[int] = set()
        immediate = {
            ClientState.DESKTOP_LOCKED,
            ClientState.LOGIN_REQUIRED,
            ClientState.TASK_CONFLICT,
            ClientState.AMBIGUOUS,
            ClientState.INSPECTION_FAILED,
        }
        last = result(ClientState.NOT_RUNNING)
        while True:
            if self.clock() >= deadline:
                if last.state in {ClientState.NOT_RUNNING, ClientState.READY}:
                    return result(ClientState.START_TIMEOUT)
                return last
            try:
                obs = self.host.observe(shortcut, timeout=min(12, deadline - self.clock()))
                last = classify(obs)
            except Exception:
                return result(ClientState.INSPECTION_FAILED)
            now = self.clock()
            if last.state in immediate:
                return last
            if now >= deadline:
                if last.state in {ClientState.NOT_RUNNING, ClientState.READY}:
                    return result(ClientState.START_TIMEOUT)
                return last
            if last.hwnd is not None and last.hwnd not in restored:
                try:
                    self.host.restore(last.hwnd)
                    restored.add(last.hwnd)
                except Exception:
                    return result(ClientState.INSPECTION_FAILED)
                # Every window action invalidates the earlier observation.
                continue
            if last.state == ClientState.READY:
                return last
            if (
                last.state == ClientState.NOT_RUNNING
                and launches < 2
                and (launches == 0 or now >= started + self.timeout / 2)
            ):
                launches += 1
                try:
                    self.host.launch(shortcut)
                except ConfigurationError:
                    return result(ClientState.CONFIG_ERROR)
                except Exception:
                    # Shell failure can still have started the client. Recheck without retrying UI.
                    # Unbounded Explorer fallback is disabled until a safe helper is validated.
                    try:
                        checked = self.host.observe(
                            shortcut, timeout=min(12, deadline - self.clock())
                        )
                        safe = classify(checked)
                        if safe.state in immediate:
                            return safe
                        if safe.state == ClientState.NOT_RUNNING:
                            return result(ClientState.INSPECTION_FAILED)
                    except Exception:
                        return result(ClientState.INSPECTION_FAILED)
            self.sleep(min(self.poll_interval, max(0, deadline - self.clock())))
