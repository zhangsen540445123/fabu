from contextlib import contextmanager
from pathlib import Path

import pytest

from fabu.tools import CommandResult

URL = "https://www.youtube.com/watch?v=VbDUeWoESMY"


class Session:
    def __init__(self, root):
        self.root, self.calls, self.paths = root, [], []

    @contextmanager
    def cookie_file(self, video_id, cancel):
        self.calls.append(video_id)
        path = self.root / f"cookie-{len(self.calls)}.txt"
        path.write_text("synthetic-only")
        self.paths.append(path)
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)


def make_runner(tmp_path, session):
    from fabu.media.source import YtDlpRunner

    exe = tmp_path / "test.exe"
    exe.touch()
    return YtDlpRunner(exe, exe, exe, browser_session=session)


def test_anonymous_success_never_reads_browser(tmp_path, monkeypatch):
    import fabu.media.source as source

    session = Session(tmp_path)
    runner = make_runner(tmp_path, session)
    calls = []

    def run(spec, cancel):
        calls.append(spec)
        return CommandResult(exit_code=0, stdout=b"{}", stderr=b"")

    monkeypatch.setattr(source, "run_command", run)
    assert runner(["--dump-single-json", URL], tmp_path).exit_code == 0
    assert not session.calls
    assert "--no-cookies" in calls[0].args
    assert "--no-cookies-from-browser" in calls[0].args


def test_authentication_refreshes_each_command_and_cleans_up(tmp_path, monkeypatch):
    import fabu.media.source as source

    session = Session(tmp_path)
    runner = make_runner(tmp_path, session)
    calls = []

    def run(spec, cancel):
        calls.append(spec)
        if len(calls) == 1:
            return CommandResult(exit_code=1, stdout=b"", stderr=b"Sign in to confirm")
        assert "--no-cookies" not in spec.args
        assert "--no-cookies-from-browser" in spec.args
        path = Path(spec.args[spec.args.index("--cookies") + 1])
        assert path.exists()
        return CommandResult(exit_code=0, stdout=b"{}", stderr=b"synthetic-sensitive-warning")

    monkeypatch.setattr(source, "run_command", run)
    assert runner(["--dump-single-json", URL], tmp_path).stderr == b""
    assert runner(["--format", "137+140", URL], tmp_path).exit_code == 0
    assert len(calls) == 3
    assert session.calls == ["VbDUeWoESMY", "VbDUeWoESMY"]
    assert not any(path.exists() for path in session.paths)


@pytest.mark.parametrize(
    "options", [{"timed_out": True}, {"canceled": True}, {"output_limit_exceeded": True}]
)
def test_incomplete_command_never_triggers_authentication(tmp_path, monkeypatch, options):
    import fabu.media.source as source

    session = Session(tmp_path)
    runner = make_runner(tmp_path, session)
    result = CommandResult(exit_code=1, stdout=b"", stderr=b"Sign in", **options)
    monkeypatch.setattr(source, "run_command", lambda *args: result)
    assert runner([URL], tmp_path) == result
    assert not session.calls


def test_failed_authentication_is_not_retried_and_error_is_sanitized(tmp_path, monkeypatch):
    import fabu.media.source as source

    session = Session(tmp_path)
    runner = make_runner(tmp_path, session)
    calls = []

    def run(spec, cancel):
        calls.append(spec)
        return CommandResult(exit_code=1, stdout=b"", stderr=b"Sign in cookie=do-not-log")

    monkeypatch.setattr(source, "run_command", run)
    result = runner([URL], tmp_path)
    assert len(calls) == 2 and len(session.calls) == 1
    with pytest.raises(source.SourceAuthenticationRequired):
        source._successful(result)
    assert b"do-not-log" not in result.stderr


def test_session_failures_pause_without_starting_authenticated_process(tmp_path, monkeypatch):
    import fabu.media.source as source
    from fabu.browser_auth.cookies import BrowserSessionError

    session = Session(tmp_path)

    @contextmanager
    def unavailable(*args):
        raise BrowserSessionError("browser_not_connected")
        yield

    session.cookie_file = unavailable
    runner = make_runner(tmp_path, session)
    calls = []

    def run(spec, cancel):
        calls.append(spec)
        return CommandResult(exit_code=1, stdout=b"", stderr=b"Sign in")

    monkeypatch.setattr(source, "run_command", run)
    with pytest.raises(source.SourceBrowserSessionRequired, match="browser_session_required"):
        runner([URL], tmp_path)
    assert len(calls) == 1


def test_auth_scope_does_not_follow_other_video_or_arbitrary_url(tmp_path, monkeypatch):
    import fabu.media.source as source

    session = Session(tmp_path)
    runner = make_runner(tmp_path, session)
    calls = []

    def run(spec, cancel):
        calls.append(spec)
        return CommandResult(
            exit_code=0 if "--cookies" in spec.args else 1, stdout=b"{}", stderr=b"Sign in"
        )

    monkeypatch.setattr(source, "run_command", run)
    runner([URL], tmp_path)
    for url in ("https://example.com/secret", "https://www.youtube.com/watch?v=AAAAAAAAAAA"):
        with pytest.raises(ValueError):
            runner([url], tmp_path)
    assert len(calls) == 2 and session.calls == ["VbDUeWoESMY"]


def test_canceled_before_browser_attempt_does_not_start_child(tmp_path, monkeypatch):
    import fabu.media.source as source

    session = Session(tmp_path)
    runner = make_runner(tmp_path, session)

    def run(spec, cancel):
        cancel.set()
        return CommandResult(exit_code=1, stdout=b"", stderr=b"Sign in")

    monkeypatch.setattr(source, "run_command", run)
    with pytest.raises(source.SourceUnavailable):
        runner([URL], tmp_path)
    assert not session.calls


def test_application_source_factory_wires_optional_youtube_provider(tmp_path, monkeypatch):
    from threading import Event, Lock
    from types import SimpleNamespace

    from fabu.browser_auth.bridge import BrowserSessionClient
    from fabu.media.bootstrap import configured_service
    from fabu.settings import Settings

    for name in ("ffmpeg.exe", "ffprobe.exe", "node.exe"):
        (tmp_path / name).touch()
    settings = Settings(
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
        tools_dir=tmp_path,
        node_path=tmp_path / "node.exe",
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=settings,
            settings_lock=Lock(),
            assets=object(),
            store=object(),
            presets=object(),
            approvals=object(),
            hardware=object(),
        )
    )

    def fake_download(video_id, workspace, runner, **kwargs):
        assert isinstance(runner.browser_session, BrowserSessionClient)
        assert runner.browser_session.root == settings.state_dir / "browser-auth"
        assert not runner.browser_session.root.exists()
        assert video_id == "VbDUeWoESMY"
        return "synthetic-source-only"

    monkeypatch.setattr("fabu.media.bootstrap.download_source", fake_download)
    service = configured_service(app, Event())
    assert service.hardware is app.state.hardware
    assert (
        service.source_loader(
            SimpleNamespace(id="synthetic-job", spec=SimpleNamespace(source_url=URL))
        )
        == "synthetic-source-only"
    )
