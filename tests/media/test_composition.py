from fractions import Fraction
from threading import Event

import pytest
from PIL import Image

from fabu.assets import AssetStore
from fabu.media.models import InputBundle
from fabu.presets import StepSpec
from fabu.store import JobStore


def step(kind, params=None, assets=None, scope="main", identifier=None):
    return StepSpec(
        id=identifier or kind,
        kind=kind,
        params=params or {},
        enabled=True,
        asset_ids=assets or [],
        scope="full" if kind in {"intro", "outro"} else scope,
    )


@pytest.fixture
def assets(tmp_path):
    return AssetStore(tmp_path / "assets", JobStore(tmp_path / "state.sqlite3"))


def picture(tmp_path, assets, name, color, size=(160, 90)):
    path = tmp_path / f"{name}.png"
    Image.new("RGB", size, color).save(path)
    return assets.import_file(path, "image")


def test_total_duration_with_intro_speed_outro(tmp_path, assets):
    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.media.compiler import compile_plan

    source = info_fixture(tmp_path).model_copy(update={"duration": Fraction(30)})
    intro = picture(tmp_path, assets, "intro", "red")
    outro = picture(tmp_path, assets, "outro", "blue")
    steps = [
        step("trim", {"start_ms": 2000, "end_ms": 22000}),
        step("speed", {"rate": 1.25}),
        step("intro", {"duration_ms": 2000}, [intro.id]),
        step("outro", {"duration_ms": 3000}, [outro.id]),
    ]
    plan = compile_plan(
        InputBundle(source=source, assets={intro.id: intro, outro.id: outro}), preset(steps)
    )
    assert plan.expected_duration_ms == 21000
    assert plan.audio_profile.expected_duration == 21
    assert plan.composition_summary[1]["start_ms"] == 2000
    assert plan.composition_summary[1]["duration_ms"] == 16000
    assert "concat=n=3:v=1:a=1" in plan.video_graph
    assert plan.complex_audio


def test_aspect_mismatch_requires_explicit_fit(tmp_path, assets):
    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.media.compiler import compile_plan

    source = info_fixture(tmp_path)
    intro = picture(tmp_path, assets, "portrait", "red", (90, 160))
    bundle = InputBundle(source=source, assets={intro.id: intro})
    with pytest.raises(ValueError, match="aspect"):
        compile_plan(bundle, preset([step("intro", {"duration_ms": 1000}, [intro.id])]))
    plan = compile_plan(
        bundle, preset([step("intro", {"duration_ms": 1000, "fit": "contain"}, [intro.id])])
    )
    assert "pad=2560:1440" in plan.video_graph
    with pytest.raises(ValueError, match="duration"):
        compile_plan(bundle, preset([step("intro", {"fit": "contain"}, [intro.id])]))


def test_auxiliary_requires_probed_matching_identity(tmp_path, assets):
    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.assets import AssetRef
    from fabu.media.compiler import compile_plan

    source = info_fixture(tmp_path)
    auxiliary = AssetRef(
        id="aux",
        media_type="video",
        local_path=source.path,
        sha256=source.sha256,
        size_bytes=source.path.stat().st_size,
    )
    with pytest.raises(ValueError, match="probe"):
        compile_plan(
            InputBundle(source=source, assets={"aux": auxiliary}),
            preset([step("intro", {}, ["aux"])]),
        )


def test_full_layers_include_intro_main_layers_do_not(tmp_path, assets):
    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.media.compiler import compile_plan

    source = info_fixture(tmp_path)
    intro = picture(tmp_path, assets, "intro", "red")
    main = picture(tmp_path, assets, "main", "blue", (10, 10))
    full = picture(tmp_path, assets, "full", "green", (10, 10))
    steps = [
        step("image_layer", {"width": 0.1}, [main.id], identifier="main"),
        step("intro", {"duration_ms": 1000}, [intro.id]),
        step("image_layer", {"width": 0.1, "x": 1}, [full.id], scope="full", identifier="full"),
        step("audio_effects", {"fade_out_ms": 10500}, scope="full"),
    ]
    plan = compile_plan(
        InputBundle(source=source, assets={a.id: a for a in [intro, main, full]}), preset(steps)
    )
    graph = plan.video_graph
    assert graph.index("[layer0]") < graph.index("concat=") < graph.index("[full_layer0]")
    assert "afade=t=out:st=0.5:d=10.5" in graph
    assert plan.expected_duration_ms == 11000


def make_clip(
    path, media_tools, *, color="black", audio=True, duration="1", rate=44100, channels=1
):
    args = [
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color={color}:s=320x180:r=30:d={duration},"
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}:duration={duration}"]
    args += [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-ac",
        str(channels),
        "-n",
        str(path),
    ]
    result = media_tools.ffmpeg(args)
    assert result.exit_code == 0, result.stderr
    return path


@pytest.mark.media
def test_silent_auxiliary_does_not_truncate_audio(tmp_path, assets, media_tools):
    import array
    import io

    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.probe import probe_media
    from fabu.media.renderer import render
    from fabu.media.validator import validate_output

    source = probe_media(make_clip(tmp_path / "source.mp4", media_tools), media_tools.probe)
    intro = assets.import_file(
        make_clip(tmp_path / "intro.mp4", media_tools, color="red", audio=False, duration="0.5"),
        "video",
    )
    intro_info = probe_media(intro.local_path, media_tools.probe)
    outro = picture(tmp_path, assets, "outro", "blue")
    green = picture(tmp_path, assets, "main_layer", "green", (10, 10))
    white = picture(tmp_path, assets, "full_layer", "white", (10, 10))
    refs = {a.id: a for a in [intro, outro, green, white]}
    steps = [
        step("image_layer", {"width": 0.1}, [green.id], identifier="main-green"),
        step("intro", {}, [intro.id]),
        step("outro", {"duration_ms": 500}, [outro.id]),
        step(
            "image_layer", {"width": 0.1, "x": 1}, [white.id], scope="full", identifier="full-white"
        ),
    ]
    plan = compile_plan(
        InputBundle(source=source, assets=refs, asset_media={intro.id: intro_info}), preset(steps)
    )
    assert plan.expected_duration_ms == 2000
    output = validate_output(
        render(plan, tmp_path / "render", Event(), tools=media_tools), plan, tools=media_tools
    )
    pixels = []
    for t in [0.46, 0.5, 1.46, 1.5]:
        frame = media_tools.ffmpeg(
            [
                "-v",
                "error",
                "-ss",
                str(t),
                "-i",
                str(output.path),
                "-frames:v",
                "1",
                "-vf",
                "scale=640:360",
                "-f",
                "image2pipe",
                "-c:v",
                "png",
                "-",
            ]
        )
        with Image.open(io.BytesIO(frame.stdout)) as image:
            im = image.convert("RGB")
            pixels.append(im.getpixel((10, 10)))
            assert min(im.getpixel((630, 10))) > 200
    # The tagged lavfi red source decodes as (255, 23, 0), not ideal RGB red.
    assert pixels[0][0] > 200 and pixels[0][1] < 40
    assert pixels[1][1] > 75 and pixels[2][1] > 75
    assert pixels[3][2] > 200 and pixels[3][1] < 20
    pcm = media_tools.ffmpeg(
        ["-v", "error", "-i", str(output.path), "-vn", "-ac", "1", "-f", "f32le", "-"]
    )
    values = array.array("f", pcm.stdout)

    def power(start, end):
        v = values[int(start * 44100) : int(end * 44100)]
        return sum(x * x for x in v) / len(v)

    assert power(0.1, 0.4) < 0.00001
    assert power(0.6, 1.4) > 0.002
    assert power(1.6, 1.9) < 0.00001


def test_fractional_frame_duration_is_not_rounded_twice(tmp_path, assets):
    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.media.compiler import compile_plan

    source = info_fixture(tmp_path).model_copy(update={"duration": Fraction(2, 3)})
    intro = picture(tmp_path, assets, "intro", "red")
    plan = compile_plan(
        InputBundle(source=source, assets={intro.id: intro}),
        preset([step("intro", {"duration_ms": 1000}, [intro.id])]),
    )
    assert plan.composition_summary[1]["duration"] == "2/3"
    assert "trim=end_frame=20" in plan.video_graph
    assert plan.expected_duration_ms == 1667


@pytest.mark.media
@pytest.mark.parametrize("keep_audio", [False, True])
def test_aux_audio_normalized_and_main_full_effects_scoped(
    tmp_path, assets, media_tools, keep_audio
):
    import array

    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.probe import probe_media
    from fabu.media.renderer import render
    from fabu.media.validator import validate_output

    source = probe_media(make_clip(tmp_path / "main.mp4", media_tools), media_tools.probe)
    intro = assets.import_file(
        make_clip(tmp_path / "intro.mp4", media_tools, duration="0.5", rate=48000, channels=2),
        "video",
    )
    intro_info = probe_media(intro.local_path, media_tools.probe)
    assert source.audio.channels == 1 and intro_info.audio.channels == 2
    steps = [
        step("audio_effects", {"gain_db": -6}, identifier="main_gain"),
        step("intro", {"keep_audio": keep_audio}, [intro.id]),
        step(
            "audio_effects",
            {"gain_db": -6, "fade_out_ms": 500},
            scope="full",
            identifier="full_gain",
        ),
    ]
    plan = compile_plan(
        InputBundle(source=source, assets={intro.id: intro}, asset_media={intro.id: intro_info}),
        preset(steps),
    )
    output = validate_output(
        render(plan, tmp_path / "render", Event(), tools=media_tools), plan, tools=media_tools
    )
    assert output.media_info.audio.sample_rate == 44100
    assert output.media_info.audio.channels == 1
    pcm = media_tools.ffmpeg(["-v", "error", "-i", str(output.path), "-vn", "-f", "f32le", "-"])
    values = array.array("f", pcm.stdout)

    def power(start, end):
        v = values[int(start * 44100) : int(end * 44100)]
        return sum(x * x for x in v) / len(v)

    assert 0.0004 < power(0.6, 0.9) < 0.0006
    assert power(1.3, 1.45) < power(0.6, 0.9) * 0.2
    if keep_audio:
        assert power(0.1, 0.4) > power(0.6, 0.9) * 3.5
    else:
        assert power(0.1, 0.4) < 0.00001


def test_auxiliary_unsupported_metadata_and_duration_rejected(tmp_path, assets):
    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.assets import AssetRef
    from fabu.media.compiler import compile_plan

    source = info_fixture(tmp_path)
    ref = AssetRef(
        id="aux",
        media_type="video",
        local_path=source.path,
        sha256=source.sha256,
        size_bytes=source.path.stat().st_size,
    )
    for update, params in [
        ({"pixel_format": "yuv420p10le"}, {}),
        ({"sha256": "0" * 64}, {}),
        ({}, {"duration_ms": 11000}),
    ]:
        auxiliary = source.model_copy(update=update)
        with pytest.raises(ValueError):
            compile_plan(
                InputBundle(source=source, assets={"aux": ref}, asset_media={"aux": auxiliary}),
                preset([step("intro", params, ["aux"])]),
            )


def test_composition_interface_returns_valid_plan_with_all_refs(tmp_path, assets):
    from test_baseline import info_fixture

    from fabu.media.baseline import build_baseline_plan
    from fabu.media.composition import compose_segments
    from fabu.media.models import verify_plan_digest

    main = build_baseline_plan(info_fixture(tmp_path))
    intro = picture(tmp_path, assets, "intro", "red")
    plan = compose_segments(main, intro, None, {"intro": {"duration_ms": 1000}}, [])
    verify_plan_digest(plan)
    assert plan.asset_hashes[intro.id] == intro.sha256
    assert plan.asset_refs[intro.id] == intro
    with pytest.raises(ValueError):
        compose_segments(
            main.model_copy(update={"expected_duration_ms": 9000}),
            intro,
            None,
            {"intro": {"duration_ms": 1000}},
            [],
        )
