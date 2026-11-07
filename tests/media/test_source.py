import json
from pathlib import Path

import pytest

from fabu.tools import CommandResult


@pytest.mark.parametrize(
    "url",
    [
        "L6imSiJLd9Q",
        "https://youtu.be/L6imSiJLd9Q",
        "https://www.youtube.com/watch?v=L6imSiJLd9Q",
        "https://youtube.com/shorts/L6imSiJLd9Q",
        "https://youtu.be/L6imSiJLd9Q?si=tracking",
        "https://m.youtube.com/watch?v=L6imSiJLd9Q&t=40",
    ],
)
def test_watch_short_and_shortlink_same_id(url):
    from fabu.media.source import parse_youtube_url

    assert parse_youtube_url(url) == "L6imSiJLd9Q"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=L6imSiJLd9Q",
        "file:///secret",
        "https://youtube.com/playlist?list=xxx",
        "https://youtube.com/@channel",
        "https://youtube.com/live/L6imSiJLd9Q",
        "https://localhost/L6imSiJLd9Q",
        "https://youtube.com/watch?v=L6imSiJLd9Q&list=abc",
        "https://youtu.be:bad/L6imSiJLd9Q",
        "https://user:pass@youtu.be/L6imSiJLd9Q",
        "https://youtube.com/watch?v=L6imSiJLd9Q&v=AAAAAAAAAAA",
        "https://youtu.be/L6imSiJLd9Q/extra",
    ],
)
def test_reject_playlist_live_and_arbitrary_host(url):
    from fabu.media.source import parse_youtube_url

    with pytest.raises(ValueError):
        parse_youtube_url(url)


def format_fixture():
    return {
        "id": "L6imSiJLd9Q",
        "duration": 10,
        "live_status": "not_live",
        "formats": [
            {
                "format_id": "video720",
                "width": 1280,
                "height": 720,
                "fps": 30,
                "vcodec": "avc1",
                "acodec": "none",
                "ext": "mp4",
            },
            {
                "format_id": "video1440",
                "width": 2560,
                "height": 1440,
                "fps": 30,
                "vcodec": "vp9",
                "acodec": "none",
                "ext": "webm",
            },
            {
                "format_id": "video1440high",
                "width": 2560,
                "height": 1440,
                "fps": 60,
                "vcodec": "vp9",
                "acodec": "none",
                "ext": "webm",
            },
            {
                "format_id": "video2160",
                "width": 3840,
                "height": 2160,
                "fps": 30,
                "vcodec": "vp9",
                "acodec": "none",
                "ext": "webm",
            },
            {"format_id": "audio", "vcodec": "none", "acodec": "mp4a", "abr": 128, "ext": "m4a"},
        ],
    }


def test_format_prefers_target_resolution_and_30fps():
    from fabu.media.source import choose_formats

    choice = choose_formats(format_fixture())
    assert choice.selector == "video1440+audio"
    assert choice.width == 2560 and choice.height == 1440
    assert "url" not in choice.model_dump()


def test_audio_required():
    from fabu.media.source import SourceUnavailable, choose_formats

    data = format_fixture()
    data["formats"] = data["formats"][:2]
    with pytest.raises(SourceUnavailable, match="audio"):
        choose_formats(data)


def test_live_drm_and_format_expression_injection_refused():
    from fabu.media.source import SourceUnavailable, choose_formats

    data = format_fixture()
    data["is_live"] = True
    with pytest.raises(SourceUnavailable):
        choose_formats(data)
    data = format_fixture()
    data["formats"][1]["has_drm"] = True
    assert choose_formats(data).selector != "video1440+audio"
    data["formats"][2]["format_id"] = "x;malicious"
    assert ";" not in choose_formats(data).selector


def test_download_wrapper_ignores_config_cookies_and_plugins(tmp_path):
    import sys

    from fabu.media.source import YtDlpRunner

    node = Path("D:/devtools/nodejs/node.exe")
    ffmpeg = Path("D:/devtools/idm/video/Fabu-comparison-20261107/tools/ffmpeg.exe")
    runner = YtDlpRunner(python_path=Path(sys.executable), node_path=node, ffmpeg_path=ffmpeg)
    spec = runner.command(
        ["--dump-single-json", "--skip-download", "https://www.youtube.com/watch?v=L6imSiJLd9Q"],
        tmp_path,
    )
    for flag in [
        "--ignore-config",
        "--no-plugin-dirs",
        "--no-playlist",
        "--no-cookies",
        "--no-cookies-from-browser",
        "--no-remote-components",
        "--no-cache-dir",
    ]:
        assert flag in spec.args
    assert "--cookies-from-browser" not in spec.args
    assert "--update" not in spec.args and "--exec" not in spec.args


def test_download_bad_metadata_never_produces_manifest(tmp_path):
    from fabu.assets import AssetStore
    from fabu.media.source import SourceUnavailable, download_source
    from fabu.store import JobStore

    assets = AssetStore(tmp_path / "assets", JobStore(tmp_path / "db.sqlite3"))

    def runner(args, workspace):
        return CommandResult(
            exit_code=0,
            stdout=json.dumps({"id": "AAAAAAAAAAA", "formats": []}).encode(),
            stderr=b"",
        )

    with pytest.raises(SourceUnavailable):
        download_source(
            "L6imSiJLd9Q",
            tmp_path / "job",
            runner,
            assets=assets,
            probe_runner=lambda _: None,
            thumbnail_transport=lambda _: None,
        )
    assert list(assets.root.iterdir()) == []


def test_download_login_failure_is_actionable_and_never_retries_cookies(tmp_path):
    from fabu.assets import AssetStore
    from fabu.media.source import SourceUnavailable, download_source
    from fabu.store import JobStore

    calls = []

    def runner(args, workspace):
        calls.append(args)
        return CommandResult(
            exit_code=1, stdout=b"", stderr=b"Sign in to confirm you are not a bot. token=private"
        )

    assets = AssetStore(tmp_path / "assets", JobStore(tmp_path / "db.sqlite3"))
    with pytest.raises(SourceUnavailable, match="authentication_required") as caught:
        download_source(
            "L6imSiJLd9Q",
            tmp_path / "job",
            runner,
            assets=assets,
            probe_runner=lambda _: None,
            thumbnail_transport=lambda _: None,
        )
    assert len(calls) == 1
    assert "private" not in str(caught.value)


def test_metadata_size_preflight_rejects_before_download(tmp_path, monkeypatch):
    from collections import namedtuple

    import fabu.media.source as module
    from fabu.assets import AssetStore
    from fabu.store import JobStore

    data = format_fixture()
    data["formats"][1]["filesize"] = 2_000_000
    data["formats"][-1]["filesize"] = 100_000
    calls = []

    def runner(args, workspace):
        calls.append(args)
        return CommandResult(exit_code=0, stdout=json.dumps(data).encode(), stderr=b"")

    usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: usage(1_000_000, 999_000, 1000))
    assets = AssetStore(tmp_path / "assets", JobStore(tmp_path / "db.sqlite3"))
    with pytest.raises(module.SourceUnavailable, match="space"):
        module.download_source(
            "L6imSiJLd9Q",
            tmp_path / "job",
            runner,
            assets=assets,
            probe_runner=lambda _: None,
            thumbnail_transport=lambda _: None,
        )
    assert len(calls) == 1


def test_original_audio_wins_over_higher_bitrate_dub():
    from fabu.media.source import choose_formats

    data = format_fixture()
    audio = data["formats"][-1]
    data["formats"] = [
        *data["formats"][:-1],
        {**audio, "format_id": "original", "abr": 128, "language": "hi", "language_preference": 10},
        {**audio, "format_id": "dub", "abr": 192, "language": "en", "language_preference": 5},
    ]
    chosen = choose_formats(data)
    assert chosen.audio_format_id == "original"
    assert chosen.audio_language == "hi" and chosen.audio_selection == "original"


def test_multiple_audio_languages_without_original_marker_pause():
    from fabu.media.source import SourceUnavailable, choose_formats

    data = format_fixture()
    audio = data["formats"][-1]
    data["formats"] = [
        *data["formats"][:-1],
        {**audio, "format_id": "hi", "language": "hi"},
        {**audio, "format_id": "en", "language": "en"},
    ]
    with pytest.raises(SourceUnavailable, match="audio"):
        choose_formats(data)


def test_muxed_dub_does_not_override_original_separate_audio():
    from fabu.media.source import choose_formats

    data = format_fixture()
    audio = data["formats"][-1]
    data["formats"].append(
        {
            "format_id": "muxed-dub",
            "width": 2560,
            "height": 1440,
            "fps": 30,
            "vcodec": "avc1",
            "acodec": "aac",
            "tbr": 99999,
            "language": "en",
            "language_preference": 5,
        }
    )
    audio.update(language="hi", language_preference=10)
    chosen = choose_formats(data)
    assert "muxed-dub" not in chosen.selector
    assert chosen.audio_format_id == audio["format_id"]
