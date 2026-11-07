import json
from pathlib import Path
from threading import Event

import pytest

from fabu.tools import CommandResult


def result(stdout=b"", stderr=b"", **kw):
    return CommandResult(exit_code=kw.pop("exit_code", 0), stdout=stdout, stderr=stderr, **kw)


class FakeTools:
    def __init__(self):
        self.calls = []
        self.hash = "a" * 64
        self.encode_result = result()
        self.decode_result = result()
        self.metadata = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 2560,
                    "height": 1440,
                    "pix_fmt": "yuv420p",
                    "avg_frame_rate": "30/1",
                    "nb_read_frames": "6",
                    "duration": "0.2",
                }
            ]
        }

    def identity(self, **kw):
        return {"ffmpeg": self.hash, "ffprobe": "b" * 64}

    def ffmpeg(self, args, cancel=None, timeout_s=0, **kw):
        self.calls.append(args)
        assert 0 < timeout_s <= 30
        if "-encoders" in args:
            return result(b" V..... hevc_nvenc NVIDIA encoder")
        if "lavfi" in args:
            Path(args[-1]).write_bytes(b"synthetic encoding fixture")
            return self.encode_result
        return self.decode_result

    def probe(self, args, cancel=None, **kw):
        assert kw["timeout_s"] <= 30
        return result(json.dumps(self.metadata).encode())


def detector(tmp_path, **kw):
    import fabu.media.hardware as h

    assert hasattr(h, "HardwareProbe"), "Bounded hardware probe is missing"
    return h.HardwareProbe(
        tmp_path, inventory=lambda: [{"name": "NVIDIA Test", "driver": "test1", "id": "GPU0"}], **kw
    )


def test_bounded_success_cache_and_cancel(tmp_path):
    now = [0.0]
    probe = detector(tmp_path, clock=lambda: now[0])
    tools = FakeTools()
    assert probe.cached_report(tools)["status"] == "unchecked" and tools.calls == []
    r = probe.check(tools, Event())
    assert r.available and r.reason == "nvenc_verified" and not r.cuda_fast_path
    n = len(tools.calls)
    assert probe.check(tools, Event()) == r and len(tools.calls) == n
    now[0] = 1000
    assert probe.cached_report(tools)["stale"]
    assert probe.check(tools, Event()).available and len(tools.calls) > n
    cancel = Event()
    cancel.set()
    with pytest.raises(ValueError, match="processing_canceled"):
        probe.check(tools, cancel)
    assert not list(tmp_path.glob("probe-*"))


@pytest.mark.parametrize("failure", ["driver", "partial", "decode", "format", "timeout"])
def test_never_accept_listing_or_bad_media(tmp_path, failure):
    probe = detector(tmp_path)
    tools = FakeTools()
    if failure == "driver":
        tools.encode_result = result(
            stderr=b"Driver does not support the required nvenc API version SECRET", exit_code=1
        )
    if failure == "partial":
        tools.metadata["streams"][0]["nb_read_frames"] = "2"
    if failure == "decode":
        tools.decode_result = result(stderr=b"bad frame", exit_code=1)
    if failure == "format":
        tools.metadata["streams"][0]["codec_name"] = "h264"
    if failure == "timeout":
        tools.encode_result = result(timed_out=True)
    r = probe.check(tools, Event())
    assert not r.available
    assert r.reason == ("driver_incompatible" if failure == "driver" else "probe_failed")
    assert "SECRET" not in r.model_dump_json()


def test_tool_and_driver_changes_invalidate_cache(tmp_path):
    p = detector(tmp_path)
    t = FakeTools()
    r = p.check(t, Event())
    n = len(t.calls)
    t.hash = "c" * 64
    assert p.cached_report(t)["status"] == "unchecked"
    changed = p.check(t, Event())
    assert changed.fingerprint != r.fingerprint and len(t.calls) > n
    p.inventory = lambda: [{"name": "NVIDIA Test", "driver": "test2", "id": "GPU0"}]
    assert p.check(t, Event()).fingerprint != changed.fingerprint


def test_cancel_during_probe_not_cached(tmp_path):
    p = detector(tmp_path)
    t = FakeTools()
    cancel = Event()
    orig = t.ffmpeg

    def run(args, *a, **kw):
        r = orig(args, *a, **kw)
        if "lavfi" in args:
            cancel.set()
        return r

    t.ffmpeg = run
    with pytest.raises(ValueError, match="processing_canceled"):
        p.check(t, cancel)
    assert p.cached_report(t)["status"] == "unchecked"


def test_persisted_evidence_is_not_authorization(tmp_path):
    p = detector(tmp_path)
    t = FakeTools()
    assert p.check(t, Event()).available
    another = detector(tmp_path)
    assert another.cached_report(t)["stale"]
    t.encode_result = result(stderr=b"required nvenc API version", exit_code=1)
    assert another.check(t, Event()).reason == "driver_incompatible"


def test_no_device_does_not_start_ffmpeg(tmp_path):
    p = detector(tmp_path)
    p.inventory = lambda: []
    t = FakeTools()
    assert p.check(t, Event()).reason == "no_device"
    assert not t.calls


@pytest.mark.parametrize("field", ["avg_frame_rate", "duration"])
def test_invalid_rational_probe_falls_back_only_in_auto_mode(tmp_path, field):
    from test_baseline import info_fixture

    from fabu.media.baseline import build_baseline_plan
    from fabu.media.encoders import bind_execution
    from fabu.media.models import OutputProfile, plan_digest

    p = detector(tmp_path)
    t = FakeTools()
    t.metadata["streams"][0][field] = "0/0"
    report = p.check(t, Event())
    assert not report.available and report.reason == "probe_failed"
    base = build_baseline_plan(info_fixture(tmp_path))
    for mode in ("auto", "gpu"):
        plan = base.model_copy(
            update={"output_profile": OutputProfile(acceleration={"mode": mode})}
        )
        plan = plan.model_copy(update={"digest": plan_digest(plan)})
        if mode == "auto":
            assert bind_execution(plan, report).execution.path == "cpu"
        else:
            with pytest.raises(ValueError, match="gpu_probe_failed"):
                bind_execution(plan, report)
