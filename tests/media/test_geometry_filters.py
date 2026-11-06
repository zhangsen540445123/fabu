import io
from fractions import Fraction
from threading import Event

import pytest
from PIL import Image

from fabu.media.geometry import Size
from fabu.presets import StepSpec


def step(kind, params=None, assets=None):
    return StepSpec(id=kind, kind=kind, enabled=True, params=params or {}, asset_ids=assets or [])


def test_crop_uses_post_rotation_coordinates():
    from fabu.media.filters_geometry import compile_geometry

    result = compile_geometry(
        [step("crop", {"x": 0, "y": 0, "width": 0.5, "height": 1})], Size(width=720, height=1280)
    )
    assert result.coordinate_map[0]["input"] == [720, 1280]
    assert result.coordinate_map[0]["crop"] == [360, 1280, 0, 0]
    assert result.output_size == Size(width=720, height=2560)


def test_explicit_crop_changes_output_ratio():
    from fabu.media.filters_geometry import compile_geometry

    result = compile_geometry(
        [step("crop", {"x": 0, "y": 0, "width": 0.5, "height": 1})], Size(width=1280, height=720)
    )
    assert result.output_size == Size(width=1440, height=1620)
    assert result.video_filters[0] == "crop=640:720:0:0"


def test_layout_does_not_stretch_subject():
    from fabu.media.filters_geometry import compile_geometry

    result = compile_geometry(
        [step("layout", {"aspect": "9:16", "top_fraction": 0.2, "bottom_fraction": 0.2})],
        Size(width=1920, height=1080),
    )
    assert result.output_size == Size(width=1440, height=2560)
    assert result.subject_size == Size(width=1440, height=810)
    assert result.subject_size.width / result.subject_size.height == 1920 / 1080
    assert result.subject_offset == (0, 874)


def test_color_defaults_are_identity():
    from fabu.media.filters_geometry import compile_geometry

    result = compile_geometry([step("color")], Size(width=1280, height=720))
    assert not any(f.startswith("eq=") for f in result.video_filters)


def test_geometry_preserves_step_order_and_rejects_tiny_crop():
    from fabu.media.filters_geometry import compile_geometry
    from fabu.media.models import PlanInvalid

    size = Size(width=1280, height=720)
    crop = step("crop", {"x": 0, "y": 0, "width": 0.5, "height": 1})
    assert compile_geometry([step("flip"), crop], size).video_filters[:2] == [
        "hflip",
        "crop=640:720:0:0",
    ]
    assert compile_geometry([crop, step("flip")], size).video_filters[:2] == [
        "crop=640:720:0:0",
        "hflip",
    ]
    with pytest.raises(PlanInvalid):
        compile_geometry([step("crop", {"x": 0, "y": 0, "width": 0.0001, "height": 1})], size)


def test_layout_image_requires_a_nonempty_reserved_band():
    from fabu.media.registry import validate_steps

    assert validate_steps([step("layout", {"top_image_id": "a", "top_fraction": 0}, ["a"])])


def test_compiler_supports_geometry_and_checks_assets(tmp_path):
    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle, PlanInvalid

    plan = compile_plan(InputBundle(source=info_fixture(tmp_path)), preset([step("flip")]))
    assert any("hflip" in s.video_filters for s in plan.stages)
    with pytest.raises(PlanInvalid, match="missing"):
        compile_plan(
            InputBundle(source=info_fixture(tmp_path)),
            preset([step("layout", {"top_image_id": "a", "top_fraction": 0.2}, ["a"])]),
        )


@pytest.fixture
def marked_source(tmp_path, media_tools):
    from fabu.media.probe import probe_media

    path = tmp_path / "marked.mp4"
    vf = (
        "color=black:s=320x240:r=30:d=0.3,"
        "drawbox=x=0:y=0:w=160:h=240:color=red:t=fill,"
        "drawbox=x=160:y=0:w=160:h=240:color=blue:t=fill,setsar=1,"
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )
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
            str(path),
        ]
    )
    assert result.exit_code == 0
    return probe_media(path, media_tools.probe)


@pytest.mark.media
def test_real_flip_crop_order_changes_visible_subject(tmp_path, media_tools, marked_source):
    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle
    from fabu.media.renderer import render
    from fabu.media.validator import validate_output

    crop = step("crop", {"x": 0, "y": 0, "width": 0.5, "height": 1})
    colors = []
    for index, steps in enumerate([[crop, step("flip")], [step("flip"), crop]]):
        plan = compile_plan(InputBundle(source=marked_source), preset(steps))
        output = validate_output(
            render(plan, tmp_path / f"render-{index}", Event(), tools=media_tools),
            plan,
            tools=media_tools,
        )
        assert output.media_info.display_aspect == Fraction(2, 3)
        frame = media_tools.ffmpeg(
            [
                "-v",
                "error",
                "-i",
                str(output.path),
                "-frames:v",
                "1",
                "-vf",
                "scale=16:16",
                "-f",
                "image2pipe",
                "-c:v",
                "png",
                "-",
            ]
        )
        with Image.open(io.BytesIO(frame.stdout)) as image:
            colors.append(image.convert("RGB").getpixel((8, 8)))
    assert colors[0][0] > 200 and colors[0][2] < 40
    assert colors[1][2] > 200 and colors[1][0] < 40


@pytest.mark.media
def test_real_layout_decorations_and_color_controls(tmp_path, media_tools, marked_source):
    from test_compiler import preset

    from fabu.assets import AssetStore
    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle
    from fabu.media.renderer import render
    from fabu.media.validator import validate_output
    from fabu.store import JobStore

    assets = AssetStore(tmp_path / "assets", JobStore(tmp_path / "db.sqlite3"))
    refs = []
    for name, color in [("top", "lime"), ("bottom", "white")]:
        path = tmp_path / f"{name}.png"
        Image.new("RGB", (200, 40), color).save(path)
        refs.append(assets.import_file(path, "image"))
    params = {
        "aspect": "1:1",
        "top_fraction": 0.2,
        "bottom_fraction": 0.2,
        "top_image_id": refs[0].id,
        "bottom_image_id": refs[1].id,
    }
    plan = compile_plan(
        InputBundle(source=marked_source, assets={r.id: r for r in refs}),
        preset([step("color", {"saturation": 0}), step("layout", params, [r.id for r in refs])]),
    )
    output = validate_output(
        render(plan, tmp_path / "layout", Event(), tools=media_tools), plan, tools=media_tools
    )
    assert output.media_info.display_aspect == 1
    frame = media_tools.ffmpeg(
        [
            "-v",
            "error",
            "-i",
            str(output.path),
            "-frames:v",
            "1",
            "-vf",
            "scale=144:144",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-",
        ]
    )
    with Image.open(io.BytesIO(frame.stdout)) as image:
        top = image.convert("RGB").getpixel((72, 14))
        bottom = image.convert("RGB").getpixel((72, 130))
        center = image.convert("RGB").getpixel((60, 72))
    assert top[1] > 200 and top[0] < 50 and top[2] < 50
    assert min(bottom) > 200
    assert max(center) - min(center) < 10


@pytest.mark.media
def test_compiled_crop_is_after_display_rotation(tmp_path, media_tools, marked_source):
    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle
    from fabu.media.probe import probe_media
    from fabu.media.renderer import render
    from fabu.media.validator import validate_output

    rotated = tmp_path / "rotated.mp4"
    assert (
        media_tools.ffmpeg(
            [
                "-v",
                "error",
                "-display_rotation",
                "90",
                "-i",
                str(marked_source.path),
                "-c",
                "copy",
                "-n",
                str(rotated),
            ]
        ).exit_code
        == 0
    )
    info = probe_media(rotated, media_tools.probe)
    plan = compile_plan(
        InputBundle(source=info),
        preset([step("crop", {"x": 0, "y": 0, "width": 1, "height": 0.5})]),
    )
    assert plan.geometry_summary["coordinate_map"][0]["input"] == [240, 320]
    output = validate_output(
        render(plan, tmp_path / "rotcrop", Event(), tools=media_tools), plan, tools=media_tools
    )
    frame = media_tools.ffmpeg(
        [
            "-v",
            "error",
            "-i",
            str(output.path),
            "-frames:v",
            "1",
            "-vf",
            "scale=16:16",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-",
        ]
    )
    with Image.open(io.BytesIO(frame.stdout)) as image:
        pixel = image.convert("RGB").getpixel((8, 8))
    assert pixel[2] > 200 and pixel[0] < 40


def test_compiler_rejects_tampered_decoration_before_render(tmp_path):
    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.assets import AssetStore
    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle, PlanInvalid
    from fabu.store import JobStore

    assets = AssetStore(tmp_path / "assets", JobStore(tmp_path / "db.sqlite3"))
    image = tmp_path / "logo.png"
    Image.new("RGB", (100, 20), "green").save(image)
    ref = assets.import_file(image, "image")
    ref.local_path.write_bytes(b"changed")
    with pytest.raises(PlanInvalid, match="integrity"):
        compile_plan(
            InputBundle(source=info_fixture(tmp_path), assets={ref.id: ref}),
            preset([step("layout", {"top_fraction": 0.2, "top_image_id": ref.id}, [ref.id])]),
        )
