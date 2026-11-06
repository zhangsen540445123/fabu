import io
from fractions import Fraction
from pathlib import Path
from threading import Event

import pytest
from PIL import Image

from fabu.assets import AssetStore
from fabu.media.geometry import Size
from fabu.presets import StepSpec
from fabu.store import JobStore


def step(kind, params=None, asset_ids=None, identifier=None, scope="main"):
    return StepSpec(
        id=identifier or kind,
        kind=kind,
        enabled=True,
        scope=scope,
        params=params or {},
        asset_ids=asset_ids or [],
    )


@pytest.fixture
def library(tmp_path):
    assets = AssetStore(tmp_path / "assets", JobStore(tmp_path / "state.sqlite3"))
    font = assets.import_file(Path("C:/Windows/Fonts/simhei.ttf"), "font")
    refs = {font.id: font}
    return assets, font, refs


def test_literal_text_cannot_inject_filter(tmp_path, library):
    from fabu.media.filters_layers import compile_layers

    _, font, refs = library
    text = "中文 ':;[]\\ %{eif:1}"
    result = compile_layers(
        [step("text_layer", {"text": text, "font_id": font.id}, [font.id])],
        Size(width=2560, height=1440),
        1000,
        refs,
    )
    assert text not in result.graph
    assert "expansion=none" in result.graph
    assert text in result.text_files.values()
    assert all("/" not in n and "\\" not in n and ":" not in n for n in result.text_files)
    assert font.id in result.font_copies.values()


def test_layer_order_and_scope(tmp_path, library):
    from fabu.media.filters_layers import compile_layers

    assets, _, refs = library
    ids = []
    for name, color in [("red", "red"), ("blue", "blue")]:
        p = tmp_path / f"{name}.png"
        Image.new("RGB", (100, 100), color).save(p)
        ref = assets.import_file(p, "image")
        refs[ref.id] = ref
        ids.append(ref.id)
    steps = [
        step("image_layer", {"width": 0.2}, [ids[0]], identifier="red"),
        step("image_layer", {"width": 0.2}, [ids[1]], identifier="blue"),
    ]
    result = compile_layers(steps, Size(width=2560, height=1440), 1000, refs)
    assert [r.id for r in result.image_inputs] == ids
    assert result.graph.index("[layer0]") < result.graph.index("[layer1]")


def test_layer_out_of_scope_and_wrong_asset_type_rejected(library):
    from fabu.media.filters_layers import compile_layers

    _, font, refs = library
    with pytest.raises(ValueError):
        compile_layers(
            [step("image_layer", {}, [font.id])], Size(width=100, height=100), 1000, refs
        )
    with pytest.raises(ValueError, match="scope"):
        compile_layers(
            [step("text_layer", {"text": "hello", "font_id": font.id, "end_ms": 2000}, [font.id])],
            Size(width=100, height=100),
            1000,
            refs,
        )


def test_subtitles_are_rendered_as_literal_text_not_ass_code(tmp_path, library):
    from fabu.media.filters_layers import compile_layers

    assets, font, refs = library
    path = tmp_path / "literal.srt"
    path.write_text("1\n00:00:00,100 --> 00:00:00,600\n{\\pos(0,0)} 中文 <tag>\n", encoding="utf-8")
    sub = assets.import_file(path, "subtitle")
    refs[sub.id] = sub
    result = compile_layers(
        [step("subtitles", {"subtitle_id": sub.id, "font_id": font.id}, [sub.id, font.id])],
        Size(width=1440, height=1440),
        1000,
        refs,
    )
    assert "pos(0,0)" not in result.graph and "<tag>" not in result.graph
    assert "{\\pos(0,0)}" in next(iter(result.text_files.values()))


@pytest.mark.media
def test_real_timed_image_overlay_opacity_and_text(tmp_path, library, media_tools):
    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle
    from fabu.media.probe import probe_media
    from fabu.media.renderer import render
    from fabu.media.validator import validate_output

    assets, font, refs = library
    image = tmp_path / "overlay.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image)
    ref = assets.import_file(image, "image")
    refs[ref.id] = ref
    source = tmp_path / "black.mp4"
    vf = (
        "color=black:s=320x180:r=30:d=1,"
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
                "sine=sample_rate=44100:duration=1",
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
        step(
            "image_layer", {"width": 0.3, "opacity": 0.5, "start_ms": 200, "end_ms": 600}, [ref.id]
        ),
        step(
            "text_layer",
            {
                "text": "中文 ':;[]\\ %{hello}",
                "font_id": font.id,
                "font_size": 64,
                "x": 0.5,
                "y": 0.5,
                "start_ms": 200,
                "end_ms": 600,
            },
            [font.id],
        ),
    ]
    plan = compile_plan(
        InputBundle(source=probe_media(source, media_tools.probe), assets=refs), preset(steps)
    )
    output = validate_output(
        render(plan, tmp_path / "render", Event(), tools=media_tools), plan, tools=media_tools
    )
    pixels = []
    white_counts = []
    for moment in [0.1, 0.4, 0.8]:
        result = media_tools.ffmpeg(
            [
                "-v",
                "error",
                "-ss",
                str(moment),
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
        with Image.open(io.BytesIO(result.stdout)) as im:
            rgb = im.convert("RGB")
            pixels.append(rgb.getpixel((10, 10)))
            raw = rgb.tobytes()
            white_counts.append(sum(min(raw[i : i + 3]) > 150 for i in range(0, len(raw), 3)))
    assert max(pixels[0]) < 20 and max(pixels[2]) < 20
    assert 90 < pixels[1][0] < 170 and pixels[1][1] < 25
    assert white_counts[1] > 50 and white_counts[0] == white_counts[2] == 0
    assert output.media_info.duration == Fraction(1)


def test_resources_use_controlled_filenames_and_verified_copies(tmp_path, library):
    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.assets import sha256_file
    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle, plan_digest
    from fabu.media.resources import materialize_resources

    _, font, refs = library
    plan = compile_plan(
        InputBundle(source=info_fixture(tmp_path), assets=refs),
        preset([step("text_layer", {"text": "hello 中文", "font_id": font.id}, [font.id])]),
    )
    folder = tmp_path / "files with spaces ' 中文"
    graph = materialize_resources(plan, folder)
    assert graph.read_text(encoding="utf-8") == plan.video_graph
    for name, text in plan.text_files.items():
        assert (folder / name).read_text(encoding="utf-8") == text
    for name in plan.font_copies:
        assert sha256_file(folder / name) == font.sha256
    with pytest.raises(FileExistsError):
        materialize_resources(plan, folder)
    bad = plan.model_copy(update={"text_files": {"../escape.txt": "bad"}})
    bad = bad.model_copy(update={"digest": plan_digest(bad)})
    with pytest.raises(ValueError, match="filename"):
        materialize_resources(bad, tmp_path / "bad")
    assert not (tmp_path / "escape.txt").exists()


def test_layer_asset_changes_after_compile_rejected(tmp_path, library, media_tools):
    import os
    import stat

    from test_baseline import info_fixture
    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle
    from fabu.media.renderer import render

    _, font, refs = library
    # info_fixture has a synthetic source hash; use matching bytes for the source identity gate.
    from fabu.assets import sha256_file

    info = info_fixture(tmp_path)
    info.path.write_bytes(b"source")
    info = info.model_copy(update={"sha256": sha256_file(info.path)})
    plan = compile_plan(
        InputBundle(source=info, assets=refs),
        preset([step("text_layer", {"text": "hello", "font_id": font.id}, [font.id])]),
    )
    os.chmod(font.local_path, stat.S_IWRITE | stat.S_IREAD)
    font.local_path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Auxiliary asset"):
        render(plan, tmp_path / "render", Event(), tools=media_tools)


def test_unsafe_layer_namespace_and_control_text_rejected(library):
    from fabu.media.filters_layers import compile_layers

    _, font, refs = library
    good = step("text_layer", {"text": "hello", "font_id": font.id}, [font.id])
    for options in [{"prefix": "../bad"}, {"input_label": "a];movie=evil"}]:
        with pytest.raises(ValueError):
            compile_layers([good], Size(width=2560, height=1440), 1000, refs, **options)
    for text in ["nul\0hello", "control\x07", "😀"]:
        with pytest.raises(ValueError):
            compile_layers(
                [step("text_layer", {"text": text, "font_id": font.id}, [font.id])],
                Size(width=2560, height=1440),
                1000,
                refs,
            )


@pytest.mark.media
def test_real_srt_edited_timeline_and_layer_order(tmp_path, library, media_tools):
    from test_compiler import preset

    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle
    from fabu.media.probe import probe_media
    from fabu.media.renderer import render
    from fabu.media.validator import validate_output

    assets, font, refs = library
    source = tmp_path / "three.mp4"
    result = media_tools.ffmpeg(
        [
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=320x180:r=30:d=3,"
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-f",
            "lavfi",
            "-i",
            "sine=sample_rate=44100:duration=3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-n",
            str(source),
        ]
    )
    assert result.exit_code == 0
    caption = tmp_path / "两行.srt"
    caption.write_text(
        "1\n00:00:00,200 --> 00:00:00,600\n中文字幕\nSecond line\n", encoding="utf-8"
    )
    sub = assets.import_file(caption, "subtitle")
    refs[sub.id] = sub
    colors = []
    for color in ["red", "blue"]:
        file = tmp_path / f"{color}.png"
        Image.new("RGB", (50, 50), color).save(file)
        ref = assets.import_file(file, "image")
        refs[ref.id] = ref
        colors.append(ref)
    steps = [
        step("trim", {"start_ms": 1000, "end_ms": 3000}),
        step("speed", {"rate": 2}),
        step(
            "subtitles",
            {"font_id": font.id, "subtitle_id": sub.id, "font_size": 80},
            [font.id, sub.id],
        ),
        step("image_layer", {"width": 0.1}, [colors[0].id], identifier="red"),
        step("image_layer", {"width": 0.1}, [colors[1].id], identifier="blue", scope="full"),
    ]
    plan = compile_plan(
        InputBundle(source=probe_media(source, media_tools.probe), assets=refs), preset(steps)
    )
    assert len(plan.font_copies) == 1
    assert plan.asset_hashes[sub.id] == sub.sha256
    output = validate_output(
        render(plan, tmp_path / "render", Event(), tools=media_tools), plan, tools=media_tools
    )
    counts = []
    for moment in [0.1, 0.4, 0.8]:
        frame = media_tools.ffmpeg(
            [
                "-v",
                "error",
                "-ss",
                str(moment),
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
            rgb = image.convert("RGB")
            pixel = rgb.getpixel((10, 10))
            assert pixel[2] > 150 and max(pixel[:2]) < 30
            raw = rgb.tobytes()
            counts.append(sum(min(raw[i : i + 3]) > 150 for i in range(0, len(raw), 3)))
    assert counts[0] == counts[2] == 0 and counts[1] > 100
    assert output.media_info.duration == Fraction(1)
