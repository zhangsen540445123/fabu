import array
from fractions import Fraction
from threading import Event

import pytest

from fabu.presets import StepSpec


def step(kind, params=None, scope="main"):
    return StepSpec(id=f"{kind}-{scope}", kind=kind, enabled=True, scope=scope, params=params or {})


def info(tmp_path):
    from test_baseline import info_fixture

    original = info_fixture(tmp_path)
    return original.model_copy(
        update={
            "duration": Fraction(40),
            "audio": original.audio.model_copy(update={"duration": Fraction(40)}),
        }
    )


def test_trim_speed_expected_duration(tmp_path):
    from fabu.media.filters_time import compile_timing, map_main_time

    result = compile_timing(
        info(tmp_path),
        [step("trim", {"start_ms": 10000, "end_ms": 30000}), step("speed", {"rate": 1.25})],
    )
    assert result.main_duration_ms == 16000
    assert result.audio_duration == Fraction(16)
    assert map_main_time(15000, 10000, Fraction(5, 4)) == 4000
    assert "trim=start=10:end=30" in result.video_filters
    assert any("atempo=1.25" == f for f in result.audio_filters)


def test_default_timing_is_identity(tmp_path):
    from fabu.media.filters_time import compile_timing

    result = compile_timing(info(tmp_path), [])
    assert result.video_filters == [] and result.audio_filters == []
    assert result.main_duration_ms == 40000


@pytest.mark.parametrize(
    "steps",
    [
        [step("trim", {"start_ms": 0, "end_ms": 41000})],
        [step("trim", {"start_ms": 1000, "end_ms": 1000})],
        [step("audio_effects", {"fade_in_ms": 41000})],
        [step("speed", {"rate": 0})],
    ],
)
def test_bad_time_windows_rejected(tmp_path, steps):
    from fabu.media.filters_time import compile_timing
    from fabu.media.models import PlanInvalid

    with pytest.raises((ValueError, PlanInvalid)):
        compile_timing(info(tmp_path), steps)


def test_audio_effects_scopes_and_no_stereo_swap(tmp_path):
    from fabu.media.filters_time import compile_timing

    result = compile_timing(
        info(tmp_path),
        [
            step("audio_effects", {"gain_db": -6, "fade_in_ms": 1000}),
            step("audio_effects", {"gain_db": -3, "fade_out_ms": 2000}, "full"),
        ],
    )
    assert "volume=-6.0dB" in result.main_audio_effects
    assert "volume=-3.0dB" in result.full_audio_effects
    assert "afade=t=out:st=38:d=2" in result.full_audio_effects
    assert not any("pan" in f for f in result.audio_filters)


def test_compiler_timing_preserves_dimensions_and_adjusts_expected_duration(tmp_path):
    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle

    source = info(tmp_path)
    plan = compile_plan(
        InputBundle(source=source),
        preset([step("trim", {"start_ms": 10000, "end_ms": 30000}), step("speed", {"rate": 1.25})]),
    )
    assert plan.expected_duration_ms == 16000 and plan.audio_profile.expected_duration == 16
    assert plan.output_size.width == 2560 and plan.output_size.height == 1440
    assert plan.timing_summary["speed"] == "5/4"


@pytest.fixture
def pulse_source(tmp_path, media_tools, request):
    from fabu.media.probe import probe_media

    sample_rate, channels = request.param
    path = tmp_path / f"pulse-{sample_rate}-{channels}.mp4"
    vf = (
        "color=black:s=320x180:r=30:d=2,drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:"
        "enable='between(t,0.8,1.19)',setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )
    expr = "if(between(t,0.8,1.2),0.5*sin(2*PI*880*t),0)"
    af = f"aevalsrc='{expr}':s={sample_rate}:d=2"
    result = media_tools.ffmpeg(
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
            af,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ac",
            str(channels),
            "-n",
            str(path),
        ]
    )
    assert result.exit_code == 0
    return probe_media(path, media_tools.probe)


@pytest.mark.media
@pytest.mark.parametrize("rate", [0.5, 1, 2])
@pytest.mark.parametrize("pulse_source", [(44100, 1), (48000, 2)], indirect=True)
def test_audio_and_video_share_timeline(tmp_path, media_tools, pulse_source, rate):
    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle
    from fabu.media.renderer import render
    from fabu.media.validator import validate_output

    steps = [step("trim", {"start_ms": 400, "end_ms": 1600}), step("speed", {"rate": rate})]
    plan = compile_plan(InputBundle(source=pulse_source), preset(steps))
    output = validate_output(
        render(plan, tmp_path / "render", Event(), tools=media_tools), plan, tools=media_tools
    )
    assert abs(float(output.media_info.duration) - 1.2 / rate) < 0.07
    assert output.media_info.audio.sample_rate == pulse_source.audio.sample_rate
    assert output.media_info.audio.channels == pulse_source.audio.channels
    raw = media_tools.ffmpeg(
        [
            "-v",
            "error",
            "-i",
            str(output.path),
            "-vf",
            "scale=8:8,format=gray",
            "-an",
            "-f",
            "rawvideo",
            "-",
        ]
    ).stdout
    frames = [raw[i : i + 64] for i in range(0, len(raw), 64)]
    first_flash = next(i / 30 for i, f in enumerate(frames) if sum(f) / len(f) > 150)
    pcm = media_tools.ffmpeg(
        [
            "-v",
            "error",
            "-i",
            str(output.path),
            "-vn",
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-",
        ]
    ).stdout
    samples = array.array("f")
    samples.frombytes(pcm)
    first_beep = next(i / 48000 for i, v in enumerate(samples) if abs(v) > 0.12)
    assert abs(first_beep - first_flash) < 1 / 30 + 1024 / pulse_source.audio.sample_rate
    # Pitch stays near the original tone, not multiplied by the video speed.
    center = round((0.6 / rate) * 48000)
    window = samples[center : center + 2400]
    crossings = sum(window[i] < 0 <= window[i + 1] for i in range(len(window) - 1))
    hz = crossings / (len(window) / 48000)
    assert 800 < hz < 960


@pytest.mark.media
def test_audio_gain_fades_and_flip_preserve_stereo_channels(tmp_path, media_tools):
    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle
    from fabu.media.probe import probe_media
    from fabu.media.renderer import render
    from fabu.media.validator import validate_output

    source = tmp_path / "stereo.mp4"
    audio = "aevalsrc='0.5*sin(2*PI*880*t)|0.5*sin(2*PI*440*t)':s=48000:d=1"
    vf = (
        "color=gray:s=320x180:r=30:d=1,"
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )
    assert (
        media_tools.ffmpeg(
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
                audio,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-c:a",
                "aac",
                "-n",
                str(source),
            ]
        ).exit_code
        == 0
    )
    steps = [
        step("flip"),
        step("audio_effects", {"gain_db": -6, "fade_in_ms": 400, "fade_out_ms": 400}),
        step("audio_effects", {"gain_db": -3}, scope="full"),
    ]
    plan = compile_plan(InputBundle(source=probe_media(source, media_tools.probe)), preset(steps))
    output = validate_output(
        render(plan, tmp_path / "render", Event(), tools=media_tools), plan, tools=media_tools
    )
    pcm = media_tools.ffmpeg(
        [
            "-v",
            "error",
            "-i",
            str(output.path),
            "-vn",
            "-f",
            "f32le",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-",
        ]
    ).stdout
    samples = array.array("f")
    samples.frombytes(pcm)
    channels = [samples[0::2], samples[1::2]]
    for channel, frequency in zip(channels, [880, 440], strict=True):
        middle = channel[24000:28800]
        crossings = sum(middle[i] < 0 <= middle[i + 1] for i in range(len(middle) - 1))
        assert abs(crossings / 0.1 - frequency) < 25
        peak = max(abs(v) for v in middle)
        assert 0.12 < peak < 0.22
        first = max(abs(v) for v in channel[:1000])
        last = max(abs(v) for v in channel[-1000:])
        assert first < peak * 0.2 and last < peak * 0.2


def test_long_fractional_timeline_has_no_float_accumulation(tmp_path):
    from fabu.media.filters_time import compile_timing, map_main_time

    original = info(tmp_path)
    original = original.model_copy(
        update={
            "duration": Fraction(36001, 10),
            "audio": original.audio.model_copy(update={"duration": Fraction(36001, 10)}),
        }
    )
    timing = compile_timing(
        original, [step("trim", {"start_ms": 1, "end_ms": 3600100}), step("speed", {"rate": 1.25})]
    )
    assert timing.main_duration == Fraction(3600099, 1250)
    assert map_main_time(3600100, 1, Fraction(5, 4)) == round(Fraction(3600099, 1) / Fraction(5, 4))
