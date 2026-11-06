import copy
import json
from fractions import Fraction
from pathlib import Path

import pytest

from fabu.tools import CommandResult


@pytest.fixture
def payload():
    return {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "sample_aspect_ratio": "1:1",
                "avg_frame_rate": "30/1",
                "r_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
                "color_space": "bt709",
                "color_transfer": "bt709",
                "color_primaries": "bt709",
                "color_range": "tv",
                "duration": "10.0",
                "start_time": "0.0",
                "disposition": {"attached_pic": 0, "default": 1},
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "44100",
                "channels": 2,
                "channel_layout": "stereo",
                "duration": "10.0",
                "start_time": "0.0",
            },
        ],
        "format": {"duration": "10.0", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }


def probe(tmp_path, payload):
    from fabu.media.probe import probe_media

    path = tmp_path / "source [1].mp4"
    path.write_bytes(b"fake-input-for-injected-probe")

    def runner(args):
        assert args[-1] == str(path)
        assert args[args.index("-protocol_whitelist") + 1] == "file,pipe"
        return CommandResult(exit_code=0, stdout=json.dumps(payload).encode(), stderr=b"")

    return probe_media(path, runner)


@pytest.mark.parametrize(
    ("w", "h", "sar", "rot", "expected"),
    [
        (1280, 720, "1:1", 0, (2560, 1440)),
        (640, 480, "1:1", 0, (1920, 1440)),
        (720, 1280, "1:1", 0, (1440, 2560)),
        (1000, 1000, "1:1", 0, (1440, 1440)),
        (1280, 720, "1:1", 90, (1440, 2560)),
        (1280, 720, "1:1", -90, (1440, 2560)),
        (1280, 720, "1:1", 180, (2560, 1440)),
        (720, 576, "16:15", 0, (1920, 1440)),
        (720, 576, "16:15", 90, (1440, 1920)),
        (3840, 1080, "1:1", 0, (2560, 720)),
    ],
)
def test_geometry_matrix(tmp_path, payload, w, h, sar, rot, expected):
    from fabu.media.geometry import fit_output

    video = payload["streams"][0]
    video.update(width=w, height=h, sample_aspect_ratio=sar, side_data_list=[{"rotation": rot}])
    info = probe(tmp_path, payload)
    size = fit_output(info)
    assert (size.width, size.height) == expected
    assert info.duration == Fraction(10)
    assert info.fps == Fraction(30)
    assert info.duration_ms == 10000
    assert len(info.sha256) == 64


def test_odd_size_rounding_and_explicit_crop_ratio(tmp_path, payload):
    from fabu.media.geometry import fit_output

    payload["streams"][0].update(width=1279, height=719)
    info = probe(tmp_path, payload)
    size = fit_output(info)
    assert size.width % 2 == size.height % 2 == 0
    assert size.width <= 2560 and size.height <= 1440
    assert abs(Fraction(size.width, size.height) - info.display_aspect) < Fraction(1, 500)
    square = fit_output(info, crop_ratio=Fraction(1))
    assert (square.width, square.height) == (1440, 1440)


@pytest.mark.parametrize(
    "mutation",
    [
        "hdr",
        "ten_bit",
        "unknown_color",
        "two_audio",
        "two_video",
        "non_right_angle",
        "bad_sar",
        "bad_fps",
        "bad_duration",
        "surround",
        "sample_rate",
        "reflected_matrix",
    ],
)
def test_hdr_or_ambiguous_tracks_pause(tmp_path, payload, mutation):
    from fabu.media.probe import UnsupportedMedia

    video, audio = payload["streams"]
    if mutation == "hdr":
        video["color_transfer"] = "smpte2084"
    elif mutation == "ten_bit":
        video["pix_fmt"] = "yuv420p10le"
    elif mutation == "unknown_color":
        video.pop("color_primaries")
    elif mutation == "two_audio":
        payload["streams"].append({**audio, "index": 2})
    elif mutation == "two_video":
        payload["streams"].append({**video, "index": 2})
    elif mutation == "non_right_angle":
        video["side_data_list"] = [{"rotation": 12.5}]
    elif mutation == "bad_sar":
        video["sample_aspect_ratio"] = "-1:1"
    elif mutation == "bad_fps":
        video["avg_frame_rate"] = "0/0"
    elif mutation == "bad_duration":
        video["duration"] = "N/A"
        payload["format"]["duration"] = "N/A"
    elif mutation == "surround":
        audio.update(channels=6, channel_layout="5.1")
    elif mutation == "sample_rate":
        audio["sample_rate"] = "22050"
    elif mutation == "reflected_matrix":
        video["side_data_list"] = [
            {
                "rotation": 0,
                "displaymatrix": (
                    "00000000: -65536 0 0\n00000001: 0 65536 0\n00000002: 0 0 1073741824"
                ),
            }
        ]
    with pytest.raises(UnsupportedMedia):
        probe(tmp_path, payload)


def test_attached_cover_not_main_video(tmp_path, payload):
    cover = copy.deepcopy(payload["streams"][0])
    cover.update(index=2, width=600, height=600)
    cover["disposition"] = {"attached_pic": 1}
    payload["streams"].append(cover)
    assert probe(tmp_path, payload).video_stream_index == 0


def test_silent_auxiliary_reported_but_main_pauses(tmp_path, payload):
    from fabu.media.probe import UnsupportedMedia, validate_supported_input

    payload["streams"] = payload["streams"][:1]
    info = probe(tmp_path, payload)
    assert info.audio is None
    assert "missing_audio" in info.warnings
    with pytest.raises(UnsupportedMedia, match="audio"):
        validate_supported_input(info)
    validate_supported_input(info, require_audio=False)


def test_mono_48khz_preserved(tmp_path, payload):
    payload["streams"][1].update(channels=1, channel_layout="mono", sample_rate="48000")
    info = probe(tmp_path, payload)
    assert info.audio.channels == 1 and info.audio.sample_rate == 48000


def test_zero_unknown_sar_is_explicit_square_assumption(tmp_path, payload):
    payload["streams"][0]["sample_aspect_ratio"] = "0:1"
    info = probe(tmp_path, payload)
    assert info.sar == Fraction(1)
    assert "unspecified_sar_assumed_square" in info.warnings


def test_extreme_aspect_and_rotation_conflict_refused(tmp_path, payload):
    from fabu.media.geometry import fit_output
    from fabu.media.probe import UnsupportedMedia

    payload["streams"][0].update(width=50000, height=2)
    with pytest.raises(UnsupportedMedia):
        fit_output(probe(tmp_path, payload))
    payload["streams"][0].update(
        width=1280, height=720, tags={"rotate": "180"}, side_data_list=[{"rotation": 90}]
    )
    with pytest.raises(UnsupportedMedia):
        probe(tmp_path, payload)


def test_rational_times_roundtrip_without_drift(tmp_path, payload):
    from fabu.media.probe import MediaInfo

    payload["streams"][0].update(duration="1.001", avg_frame_rate="30000/1001")
    info = probe(tmp_path, payload)
    assert info.duration == Fraction(1001, 1000)
    restored = MediaInfo.model_validate_json(info.model_dump_json())
    assert restored == info


def test_reject_nonlocal_or_nonmedia_input(tmp_path):
    from fabu.media.probe import UnsupportedMedia, probe_media

    with pytest.raises(UnsupportedMedia):
        probe_media(Path("https://example.test/video"), lambda _: None)
    path = tmp_path / "playlist.m3u8"
    path.write_text("#EXTM3U\nfile:///private")
    with pytest.raises(UnsupportedMedia):
        probe_media(path, lambda _: None)


def test_failed_probe_sanitizes_stderr(tmp_path):
    from fabu.media.probe import UnsupportedMedia, probe_media

    path = tmp_path / "source.mp4"
    path.write_bytes(b"test")
    with pytest.raises(UnsupportedMedia) as caught:
        probe_media(path, lambda _: CommandResult(exit_code=1, stdout=b"", stderr=b"secret-token"))
    assert "secret-token" not in str(caught.value)


def test_frame_scan_rejects_dynamic_geometry(tmp_path, payload):
    from fabu.media.probe import UnsupportedMedia, scan_frame_geometry

    info = probe(tmp_path, payload)

    def runner(args):
        return CommandResult(
            exit_code=0, stdout=b"width=1280|height=720\nwidth=640|height=480\n", stderr=b""
        )

    with pytest.raises(UnsupportedMedia, match="changes"):
        scan_frame_geometry(info, runner)


def test_frame_scan_requires_frames_and_handles_metadata(tmp_path, payload):
    from fabu.media.probe import UnsupportedMedia, scan_frame_geometry

    info = probe(tmp_path, payload)
    result = CommandResult(
        exit_code=0,
        stdout=b"width=1280|height=720|side_data_type=SEI\n\nwidth=1280|height=720\n",
        stderr=b"",
    )
    assert scan_frame_geometry(info, lambda _: result) == 2
    with pytest.raises(UnsupportedMedia):
        scan_frame_geometry(info, lambda _: CommandResult(exit_code=0, stdout=b"", stderr=b""))


def test_matroska_duration_tags_preserve_video_audio_timing(tmp_path, payload):
    video, audio = payload["streams"]
    video.pop("duration")
    audio.pop("duration")
    video["tags"] = {"DURATION": "00:00:10.000000000"}
    audio["tags"] = {"DURATION": "00:00:10.064000000"}
    payload["format"]["duration"] = "10.064"
    info = probe(tmp_path, payload)
    assert info.duration == Fraction(10)
    assert info.audio.duration == Fraction(1258, 125)
