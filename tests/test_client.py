from dataclasses import replace
from pathlib import Path

import pytest


def node(c, name="", kind="Text", children=(), value="", **kwargs):
    return c.Control(name=name, control_type=kind, children=tuple(children), value=value, **kwargs)


def snapshot(c, scene="home", conflict=False, login=False, truncated=False):
    content = {
        "home": [node(c, "主页"), node(c, "开始发布", "Button"), node(c, "账号", "Text")],
        "accounts": [node(c, "账号管理"), node(c, "授权账号", "Button")],
        "publish": [node(c, "视频发布"), node(c, "标题", "Edit")],
        "records": [node(c, "发布记录"), node(c, "任务列表", "Table")],
        "unknown": [node(c, "首页 ｜ 蚁小二")],
    }[scene]
    if conflict:
        content.append(node(c, "检测到未完成的视频任务，请确认是否继续上传。"))
    if login:
        content = [node(c, "手机号", "Edit"), node(c, "登录", "Button")]
    return c.WindowSnapshot(
        hwnd=17,
        pid=42,
        executable=Path("client.exe"),
        controls=(
            node(c, "首页 ｜ 蚁小二", "Document", content, value="https://www.yixiaoer.cn/web"),
        ),
        truncated=truncated,
    )


def observation(c, window=None, processes=(42,), locked=False):
    return c.Observation(
        process_ids=tuple(processes),
        windows=(() if window is None else (window,)),
        desktop_locked=locked,
    )


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class FakeHost:
    def __init__(self, observations):
        self.observations = list(observations)
        self.launches = []
        self.restores = []
        self.fallbacks = []

    def observe(self, shortcut, *, timeout=12):
        if len(self.observations) > 1:
            return self.observations.pop(0)
        return self.observations[0]

    def launch(self, shortcut):
        self.launches.append(shortcut)

    def restore(self, hwnd):
        self.restores.append(hwnd)

    def open_shortcut_via_uia(self, shortcut):
        self.fallbacks.append(shortcut)
        return False


def launcher(c, host, clock, **kwargs):
    return c.ClientLauncher(host, clock=clock, sleep=clock.sleep, **kwargs)


def shortcut(c, tmp_path):
    path = tmp_path / "蚁小二.lnk"
    path.write_bytes(b"test fixture")
    target = tmp_path / "client.exe"
    target.write_bytes(b"test fixture")
    return c.Shortcut(path=path, target=target, working_directory=tmp_path)


def test_missing_program_is_not_ready(client):
    assert (
        client.classify(observation(client, processes=())).state == client.ClientState.NOT_RUNNING
    )


@pytest.mark.parametrize("scene", ["home", "accounts", "publish", "records"])
def test_only_readable_embedded_app_is_ready(client, scene):
    result = client.classify(observation(client, snapshot(client, scene)))
    assert result.state == client.ClientState.READY
    assert result.scene == scene


def test_outer_browser_shell_is_not_ready(client):
    shell = replace(
        snapshot(client),
        controls=(
            node(
                client, "首页 ｜ 蚁小二", "Document", value="file:///resources/app.asar/index.html"
            ),
        ),
    )
    assert client.classify(observation(client, shell)).state == client.ClientState.UI_UNAVAILABLE


def test_page_title_without_content_is_not_ready(client):
    assert (
        client.classify(observation(client, snapshot(client, "unknown"))).state
        == client.ClientState.UI_UNAVAILABLE
    )


def test_untrusted_website_cannot_impersonate_app(client):
    snap = snapshot(client)
    snap = replace(
        snap, controls=(replace(snap.controls[0], value="https://www.yixiaoer.cn.evil.test/web"),)
    )
    assert client.classify(observation(client, snap)).state == client.ClientState.UI_UNAVAILABLE


def test_hidden_content_cannot_establish_readiness(client):
    snap = snapshot(client)
    snap = replace(snap, controls=(replace(snap.controls[0], visible=False),))
    assert client.classify(observation(client, snap)).state == client.ClientState.UI_UNAVAILABLE


def test_incomplete_probe_cannot_establish_safety(client):
    assert (
        client.classify(observation(client, snapshot(client, truncated=True))).state
        == client.ClientState.INSPECTION_FAILED
    )


def test_existing_upload_blocks_automation(client):
    assert (
        client.classify(observation(client, snapshot(client, conflict=True))).state
        == client.ClientState.TASK_CONFLICT
    )


def test_login_blocks_automation(client):
    assert (
        client.classify(observation(client, snapshot(client, login=True))).state
        == client.ClientState.LOGIN_REQUIRED
    )


@pytest.mark.parametrize("locked", [True, None])
def test_locked_or_unknown_desktop_never_ready(client, locked):
    assert client.classify(observation(client, snapshot(client), locked=locked)).state in {
        client.ClientState.DESKTOP_LOCKED,
        client.ClientState.INSPECTION_FAILED,
    }


def test_multiple_windows_are_not_guessed(client):
    obs = observation(client, snapshot(client))
    obs = replace(obs, windows=(snapshot(client), replace(snapshot(client), hwnd=18)))
    assert client.classify(obs).state == client.ClientState.AMBIGUOUS


def test_window_without_live_owner_not_ready(client):
    assert (
        client.classify(observation(client, snapshot(client), processes=())).state
        == client.ClientState.INSPECTION_FAILED
    )


def test_cold_launch_waits_for_verified_page(client, tmp_path):
    clock = Clock()
    host = FakeHost(
        [
            observation(client, processes=()),
            observation(client),
            observation(client, snapshot(client)),
        ]
    )
    result = launcher(client, host, clock).ensure_ready(shortcut(client, tmp_path))
    assert result.state == client.ClientState.READY
    assert len(host.launches) == 1
    assert clock.now > 0


def test_existing_window_restored_without_launch(client, tmp_path):
    host = FakeHost([observation(client, snapshot(client))])
    result = launcher(client, host, Clock()).ensure_ready(shortcut(client, tmp_path))
    assert result.state == client.ClientState.READY
    assert host.launches == []
    assert host.restores == [17]


def test_restore_refresh_detects_conflicting_task(client, tmp_path):
    host = FakeHost(
        [
            observation(client, snapshot(client)),
            observation(client, snapshot(client, conflict=True)),
        ]
    )
    result = launcher(client, host, Clock()).ensure_ready(shortcut(client, tmp_path))
    assert result.state == client.ClientState.TASK_CONFLICT
    assert host.launches == []


def test_background_process_is_not_repeatedly_launched(client, tmp_path):
    clock = Clock()
    host = FakeHost([observation(client)])
    result = launcher(client, host, clock).ensure_ready(shortcut(client, tmp_path))
    assert result.state == client.ClientState.WINDOW_UNAVAILABLE
    assert clock.now == 90
    assert not host.launches
    assert not host.fallbacks


def test_at_most_two_launches_with_total_90_second_budget(client, tmp_path):
    clock = Clock()
    host = FakeHost([observation(client, processes=())])
    result = launcher(client, host, clock).ensure_ready(shortcut(client, tmp_path))
    assert result.state == client.ClientState.START_TIMEOUT
    assert len(host.launches) == 2
    assert clock.now == 90


def test_retry_rechecks_process_existence(client, tmp_path):
    clock = Clock()
    host = FakeHost([observation(client, processes=()), observation(client)])
    launcher(client, host, clock).ensure_ready(shortcut(client, tmp_path))
    assert len(host.launches) == 1


def test_unsafe_preconditions_prevent_any_input(client, tmp_path):
    host = FakeHost([observation(client, snapshot(client, conflict=True))])
    result = launcher(client, host, Clock()).ensure_ready(shortcut(client, tmp_path))
    assert result.state == client.ClientState.TASK_CONFLICT
    assert not host.launches and not host.restores


def test_missing_shortcut_has_clear_state(client, tmp_path):
    item = client.Shortcut(
        path=tmp_path / "missing.lnk", target=tmp_path / "missing.exe", working_directory=tmp_path
    )
    host = FakeHost([observation(client, processes=())])
    result = launcher(client, host, Clock()).ensure_ready(item)
    assert result.state == client.ClientState.CONFIG_ERROR
    assert not host.launches


def test_restore_error_does_not_guess_or_start_another_instance(client, tmp_path):
    class BrokenHost(FakeHost):
        def restore(self, hwnd):
            raise OSError("cannot activate")

    host = BrokenHost([observation(client, snapshot(client))])
    result = launcher(client, host, Clock()).ensure_ready(shortcut(client, tmp_path))
    assert result.state == client.ClientState.INSPECTION_FAILED
    assert not host.launches


def test_snapshot_error_is_not_converted_to_no_process(client, tmp_path):
    class BrokenHost(FakeHost):
        def observe(self, shortcut, *, timeout=12):
            raise OSError("access denied")

    host = BrokenHost([])
    result = launcher(client, host, Clock()).ensure_ready(shortcut(client, tmp_path))
    assert result.state == client.ClientState.INSPECTION_FAILED
    assert not host.launches


def test_disabled_ancestor_cannot_make_children_ready(client):
    snap = snapshot(client)
    snap = replace(
        snap, controls=(node(client, kind="Pane", enabled=False, children=snap.controls),)
    )
    assert client.classify(observation(client, snap)).state != client.ClientState.READY


def test_late_probe_success_cannot_escape_90_second_deadline(client, tmp_path):
    clock = Clock()

    class SlowHost(FakeHost):
        def observe(self, shortcut, *, timeout=12):
            clock.now += 91
            return observation(client, snapshot(client))

    result = launcher(client, SlowHost([]), clock).ensure_ready(shortcut(client, tmp_path))
    assert result.state == client.ClientState.START_TIMEOUT


def test_unsafe_fallback_is_disabled_in_validation_stage(client, tmp_path):
    class FallbackHost(FakeHost):
        def launch(self, shortcut):
            self.launches.append(shortcut)
            raise OSError("shell failed")

        def open_shortcut_via_uia(self, shortcut):
            self.fallbacks.append(shortcut)
            self.observations = [observation(client, snapshot(client))]
            return True

    host = FallbackHost([observation(client, processes=())])
    result = launcher(client, host, Clock()).ensure_ready(shortcut(client, tmp_path))
    assert result.state == client.ClientState.INSPECTION_FAILED
    assert len(host.launches) == 1 and not host.fallbacks


def test_fallback_forbidden_if_failed_shell_started_a_process(client, tmp_path):
    class PartialHost(FakeHost):
        def launch(self, shortcut):
            self.launches.append(shortcut)
            self.observations = [observation(client)]
            raise OSError("shell outcome unknown")

    host = PartialHost([observation(client, processes=())])
    result = launcher(client, host, Clock()).ensure_ready(shortcut(client, tmp_path))
    assert result.state == client.ClientState.WINDOW_UNAVAILABLE
    assert len(host.launches) == 1 and not host.fallbacks


def test_observation_has_remaining_deadline_budget(client, tmp_path):
    clock = Clock()

    class BudgetHost(FakeHost):
        def __init__(self):
            super().__init__([observation(client)])
            self.budgets = []

        def observe(self, shortcut, *, timeout=12):
            self.budgets.append(timeout)
            clock.now += timeout
            return self.observations[0]

    host = BudgetHost()
    launcher(client, host, clock, timeout=5).ensure_ready(shortcut(client, tmp_path))
    assert host.budgets[0] <= 5
    assert clock.now <= 5


def test_configuration_rejection_cannot_enter_any_fallback(client, tmp_path):
    class RejectedHost(FakeHost):
        def launch(self, shortcut):
            raise client.ConfigurationError("Shortcut changed")

    host = RejectedHost([observation(client, processes=())])
    result = launcher(client, host, Clock()).ensure_ready(shortcut(client, tmp_path))
    assert result.state == client.ClientState.CONFIG_ERROR
    assert not host.fallbacks


def test_subsecond_timeout_uses_valid_poll_interval(client, tmp_path):
    clock = Clock()
    host = FakeHost([observation(client)])
    result = launcher(client, host, clock, timeout=0.5).ensure_ready(shortcut(client, tmp_path))
    assert result.state == client.ClientState.WINDOW_UNAVAILABLE
    assert clock.now == 0.5
