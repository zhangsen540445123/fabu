from pathlib import Path

import pytest


def test_srt_time_uses_edited_scope(tmp_path):
    from fabu.media.subtitles import parse_srt

    path = tmp_path / "captions.srt"
    path.write_text(
        "1\n00:00:00,200 --> 00:00:00,800\n第一行\n第二行\n\n"
        "2\n00:00:01,000 --> 00:00:01,500\nnext\n",
        encoding="utf-8",
    )
    cues = parse_srt(path, 2000)
    assert [(c.start_ms, c.end_ms) for c in cues] == [(200, 800), (1000, 1500)]
    assert cues[0].text == "第一行\n第二行"
    with pytest.raises(ValueError, match="scope"):
        parse_srt(path, 1000)


@pytest.mark.parametrize(
    "content",
    [
        "1\n00:00:02,000 --> 00:00:01,000\nbackwards",
        "1\n00:00:00,000 --> 00:99:00,000\nbad minute",
        "1\n00:00:00,000 --> 00:00:01,000\n\x00unsafe",
        "1\n00:00:00,000 --> 00:00:01,000\none\n\n2\n00:00:00,500 --> 00:00:01,200\noverlap",
        "arbitrary text without timing",
    ],
)
def test_bad_subtitle_intervals_rejected(tmp_path, content):
    from fabu.media.subtitles import parse_srt

    path = tmp_path / "caption.srt"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        parse_srt(path, 2000)


def test_missing_font_and_bad_utf8_fail_preflight(tmp_path):
    from fabu.media.subtitles import parse_srt, validate_font

    path = tmp_path / "bad.srt"
    path.write_bytes(b"\xff\xfe\xff")
    with pytest.raises(ValueError):
        parse_srt(path, 1000)
    with pytest.raises(ValueError):
        validate_font(tmp_path / "missing.ttf", "hello")


def test_font_coverage_does_not_silently_substitute():
    from fabu.media.subtitles import validate_font

    validate_font(Path("C:/Windows/Fonts/simhei.ttf"), "中文 ABC [] : '")
    with pytest.raises(ValueError, match="glyph"):
        validate_font(Path("C:/Windows/Fonts/arial.ttf"), "中文")
