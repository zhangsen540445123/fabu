import importlib
import json
from contextlib import nullcontext

import pytest

from fabu.client import Observation, Shortcut


def cli_module():
    try:
        return importlib.import_module("fabu.cli")
    except ModuleNotFoundError:
        pytest.fail("CLI not implemented", pytrace=False)


def setup_cli(cli, monkeypatch, tmp_path):
    shortcut = Shortcut(tmp_path / "client.lnk", tmp_path / "client.exe", tmp_path)
    shortcut.path.touch()
    shortcut.target.touch()
    monkeypatch.setattr(cli, "discover_shortcut", lambda path: shortcut.path)
    monkeypatch.setattr(cli, "resolve_shortcut", lambda path: shortcut)
    return shortcut


def test_inspect_is_readonly_by_default(monkeypatch, tmp_path, capsys):
    cli = cli_module()
    setup_cli(cli, monkeypatch, tmp_path)

    class Host:
        def observe(self, shortcut, *, timeout=12):
            return Observation(desktop_locked=False)

        def launch(self, shortcut):
            pytest.fail("inspect must not launch")

    monkeypatch.setattr(cli, "WindowsClientHost", Host)
    assert cli.main(["client", "inspect"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["state"] == "not_running"
    assert output["publication_enabled"] is False


def test_cli_shortcut_errors_are_clear_and_scrubbed(monkeypatch, capsys):
    cli = cli_module()

    def unavailable(*args):
        raise cli.ConfigurationError("Alice token=secret")

    monkeypatch.setattr(cli, "discover_shortcut", unavailable)
    assert cli.main(["client", "inspect"]) == 2
    output = capsys.readouterr().out
    assert "config_error" in output
    assert "secret" not in output and "Alice" not in output


def test_cli_start_has_bounded_policy(monkeypatch, tmp_path, capsys):
    cli = cli_module()
    setup_cli(cli, monkeypatch, tmp_path)
    calls = []
    from fabu.client import ClientState, result

    class Launcher:
        def __init__(self, host, timeout):
            assert timeout == 90

        def ensure_ready(self, shortcut):
            calls.append(shortcut)
            return result(ClientState.START_TIMEOUT)

    monkeypatch.setattr(cli, "ClientLauncher", Launcher)
    monkeypatch.setattr(cli, "WindowsClientHost", lambda: object())
    monkeypatch.setattr(cli, "client_mutex", nullcontext)
    assert cli.main(["client", "start"]) == 2
    assert len(calls) == 1
    assert json.loads(capsys.readouterr().out)["publication_enabled"] is False


def test_cli_rejects_out_of_policy_timeout(capsys):
    cli = cli_module()
    with pytest.raises(SystemExit):
        cli.main(["client", "start", "--timeout", "120"])


def test_cli_no_publish_command(capsys):
    cli = cli_module()
    with pytest.raises(SystemExit):
        cli.main(["publish"])


def test_unknown_os_error_does_not_reveal_raw_page_or_config(monkeypatch, tmp_path, capsys):
    cli = cli_module()
    setup_cli(cli, monkeypatch, tmp_path)

    class Host:
        def observe(self, shortcut, *, timeout=12):
            raise OSError("password=secret")

    monkeypatch.setattr(cli, "WindowsClientHost", Host)
    assert cli.main(["client", "inspect"]) == 2
    output = capsys.readouterr().out
    assert "inspection_failed" in output and "secret" not in output


def test_inspect_passes_requested_timeout(monkeypatch, tmp_path, capsys):
    cli = cli_module()
    setup_cli(cli, monkeypatch, tmp_path)
    budgets = []

    class Host:
        def observe(self, shortcut, *, timeout=12):
            budgets.append(timeout)
            return Observation(desktop_locked=False)

    monkeypatch.setattr(cli, "WindowsClientHost", Host)
    assert cli.main(["client", "inspect", "--timeout", "0.5"]) == 2
    assert budgets == [0.5]
