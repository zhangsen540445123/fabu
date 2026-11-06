import json
from fractions import Fraction
from threading import Event

import pytest

from fabu.assets import sha256_file
from fabu.media.probe import probe_media


@pytest.fixture
def source_clip(tmp_path, media_tools):
    source = tmp_path / "signal-source.mp4"
    video = (
        "color=c=black:s=320x180:r=30:d=1,drawbox=x=0:y=0:w=iw:h=ih:"
        "color=white:t=fill:enable='between(t,0.4,0.499)',"
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )
    audio = "aevalsrc='if(between(t,0.4,0.5),0.5*sin(2*PI*880*t),0)':s=44100:d=1"
    result = media_tools.ffmpeg(
        [
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            video,
            "-f",
            "lavfi",
            "-i",
            audio,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-n",
            str(source),
        ]
    )
    assert result.exit_code == 0
    return source


@pytest.mark.media
def test_real_baseline_full_decode_and_source_unchanged(tmp_path, media_tools, source_clip):
    from fabu.media.baseline import build_baseline_plan
    from fabu.media.renderer import render
    from fabu.media.validator import OutputInvalid, promote_output, validate_output

    before = sha256_file(source_clip)
    plan = build_baseline_plan(probe_media(source_clip, media_tools.probe))
    progress = []
    candidate = render(
        plan, tmp_path / "render", Event(), tools=media_tools, on_progress=progress.append
    )
    manifest = validate_output(candidate, plan, tools=media_tools)
    assert manifest.media_info.codec == "hevc" and manifest.media_info.duration == Fraction(1)
    final = promote_output(manifest, tmp_path / "final.mp4")
    assert final.path.exists() and not candidate.exists()
    assert sha256_file(source_clip) == before
    assert not progress or max(progress) <= 99
    with pytest.raises(OutputInvalid, match="overwritten"):
        promote_output(final, source_clip)
    # Decode small raw frames and PCM; assert the flash and beep stay aligned after full pipeline.
    raw = media_tools.ffmpeg(
        [
            "-v",
            "error",
            "-i",
            str(final.path),
            "-vf",
            "scale=16:16,format=gray",
            "-an",
            "-f",
            "rawvideo",
            "-",
        ]
    ).stdout
    frames = [raw[i : i + 256] for i in range(0, len(raw), 256)]
    first_flash = next(i / 30 for i, f in enumerate(frames) if sum(f) / len(f) > 150)
    pcm = media_tools.ffmpeg(
        [
            "-v",
            "error",
            "-i",
            str(final.path),
            "-vn",
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-",
        ]
    ).stdout
    import array

    samples = array.array("f")
    samples.frombytes(pcm)
    first_beep = next(i / 44100 for i, v in enumerate(samples) if abs(v) > 0.1)
    assert abs(first_flash - first_beep) < (1 / 30 + 1024 / 44100)
    damaged = tmp_path / "broken.mp4"
    damaged.write_bytes(final.path.read_bytes()[:500])
    with pytest.raises(ValueError):
        validate_output(damaged, plan, tools=media_tools)


@pytest.mark.media
def test_rotation_normalized_once(tmp_path, media_tools, source_clip):
    from fabu.media.baseline import build_baseline_plan
    from fabu.media.renderer import render
    from fabu.media.validator import validate_output

    rotated = tmp_path / "rotated.mp4"
    result = media_tools.ffmpeg(
        [
            "-v",
            "error",
            "-display_rotation",
            "90",
            "-i",
            str(source_clip),
            "-c",
            "copy",
            "-n",
            str(rotated),
        ]
    )
    assert result.exit_code == 0
    plan = build_baseline_plan(probe_media(rotated, media_tools.probe))
    output = validate_output(
        render(plan, tmp_path / "render", Event(), tools=media_tools), plan, tools=media_tools
    )
    assert output.media_info.rotation_deg == 0
    assert (output.media_info.coded_width, output.media_info.coded_height) == (1440, 2560)


def test_renderer_failure_leaves_failed_record_and_no_approved_output(tmp_path, monkeypatch):
    from test_baseline import info_fixture

    import fabu.media.renderer as module
    from fabu.media.baseline import build_baseline_plan
    from fabu.media.renderer import RenderFailed, render
    from fabu.tools import CommandResult

    monkeypatch.setattr(module, "scan_frame_geometry", lambda *_: 30)

    class BrokenTools:
        def probe(self, args, cancel=None):
            raise AssertionError("Should use patched scan")

        def ffmpeg(self, args, cancel=None):
            from pathlib import Path

            Path(args[-1]).write_bytes(b"partial")
            return CommandResult(exit_code=1, stdout=b"", stderr=b"secret")

    with pytest.raises(RenderFailed):
        render(
            build_baseline_plan(info_fixture(tmp_path)),
            tmp_path / "render",
            Event(),
            tools=BrokenTools(),
        )
    records = list((tmp_path / "render").glob("render-*.json"))
    assert len(records) == 1 and json.loads(records[0].read_text())["state"] == "FAILED"
    assert not (tmp_path / "final.mp4").exists()


def test_promotion_refuses_changed_output(tmp_path):
    from datetime import UTC, datetime

    from test_baseline import info_fixture

    from fabu.media.models import OutputManifest
    from fabu.media.validator import OutputInvalid, promote_output

    info = info_fixture(tmp_path)
    manifest = OutputManifest(
        path=info.path,
        sha256=info.sha256,
        size_bytes=info.path.stat().st_size,
        media_info=info,
        plan_digest="a" * 64,
        verified_at=datetime.now(UTC),
    )
    info.path.write_bytes(b"changed")
    with pytest.raises(OutputInvalid, match="changed"):
        promote_output(manifest, tmp_path / "final.mp4")


@pytest.mark.media
def test_asymmetric_sar_rotation_is_counterclockwise_once(tmp_path, media_tools):
    import io

    from PIL import Image

    from fabu.media.baseline import build_baseline_plan
    from fabu.media.renderer import render
    from fabu.media.validator import validate_output

    source = tmp_path / "marked.mp4"
    vf = (
        "color=black:s=720x576:r=30:d=0.3,drawbox=x=0:y=0:w=120:h=120:color=red:t=fill,"
        "drawbox=x=600:y=0:w=120:h=120:color=lime:t=fill,"
        "drawbox=x=0:y=456:w=120:h=120:color=blue:t=fill,"
        "drawbox=x=600:y=456:w=120:h=120:color=white:t=fill,setsar=16/15,"
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )
    r = media_tools.ffmpeg(
        [
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            vf,
            "-f",
            "lavfi",
            "-i",
            "sine=sample_rate=48000:duration=0.3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-n",
            str(source),
        ]
    )
    assert r.exit_code == 0
    rot = tmp_path / "marked-rotated.mp4"
    assert (
        media_tools.ffmpeg(
            [
                "-v",
                "error",
                "-display_rotation",
                "90",
                "-i",
                str(source),
                "-c",
                "copy",
                "-n",
                str(rot),
            ]
        ).exit_code
        == 0
    )
    plan = build_baseline_plan(probe_media(rot, media_tools.probe))
    output = validate_output(
        render(plan, tmp_path / "render", Event(), tools=media_tools), plan, tools=media_tools
    )
    assert (output.media_info.coded_width, output.media_info.coded_height) == (1440, 1920)
    result = media_tools.ffmpeg(
        [
            "-v",
            "error",
            "-i",
            str(output.path),
            "-frames:v",
            "1",
            "-vf",
            "scale=144:192",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-",
        ]
    )
    with Image.open(io.BytesIO(result.stdout)) as im:
        tl, tr, bl, br = [
            im.convert("RGB").getpixel(p) for p in [(8, 8), (136, 8), (8, 184), (136, 184)]
        ]
    assert tl[1] > 200 and tl[0] < 40 and tl[2] < 40  # top-right source -> top-left CCW
    assert min(tr) > 200
    assert bl[0] > 200 and bl[1] < 40
    assert br[2] > 200 and br[0] < 40


def test_disk_monitor_cancels_only_candidate(tmp_path, monkeypatch):
    import time
    from collections import namedtuple
    from pathlib import Path

    from test_baseline import info_fixture

    import fabu.media.renderer as module
    from fabu.media.baseline import build_baseline_plan
    from fabu.tools import CommandResult

    monkeypatch.setattr(module, "scan_frame_geometry", lambda *_: 30)
    usage = namedtuple("Usage", "total used free")
    space = [10**12]
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: usage(10**12, 0, space[0]))

    class FillingTools:
        def probe(self, *args):
            raise AssertionError("scan mocked")

        def ffmpeg(self, args, cancel):
            Path(args[-1]).write_bytes(b"partial")
            space[0] = 0
            deadline = time.monotonic() + 3
            while not cancel.is_set() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert cancel.is_set()
            return CommandResult(exit_code=1, stdout=b"", stderr=b"", canceled=True)

    info = info_fixture(tmp_path)
    before = sha256_file(info.path)
    with pytest.raises(module.RenderFailed, match="space"):
        module.render(build_baseline_plan(info), tmp_path / "render", Event(), tools=FillingTools())
    assert sha256_file(info.path) == before
    assert all(
        json.loads(p.read_text())["state"] == "FAILED"
        for p in (tmp_path / "render").glob("render-*.json")
    )


def test_validator_rejects_hardlink_to_source_before_running_tools(tmp_path):
    import os

    from test_baseline import info_fixture

    from fabu.media.baseline import build_baseline_plan
    from fabu.media.validator import OutputInvalid, validate_output

    info = info_fixture(tmp_path)
    link = tmp_path / "alias.mp4"
    os.link(info.path, link)
    with pytest.raises(OutputInvalid, match="source"):
        validate_output(link, build_baseline_plan(info), tools=None)
