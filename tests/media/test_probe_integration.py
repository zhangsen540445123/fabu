from fractions import Fraction

import pytest

from fabu.media.probe import probe_media


def test_unconfigured_toolchain_never_searches_path(tmp_path):
    from fabu.media.runtime import MediaTools

    with pytest.raises(ValueError):
        MediaTools(ffmpeg_path=tmp_path / "absent.exe", ffprobe_path=tmp_path / "absent.exe")


@pytest.mark.media
def test_real_probe_rotation_sar_and_markers(tmp_path, media_tools):
    from fabu.media.geometry import fit_output

    base = tmp_path / "asymmetric.mp4"
    result = media_tools.ffmpeg(
        [
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=720x576:r=30:d=1,drawbox=x=0:y=0:w=100:h=100:color=red:t=fill,"
            "drawbox=x=620:y=0:w=100:h=100:color=green:t=fill,"
            "drawbox=x=0:y=476:w=100:h=100:color=blue:t=fill,"
            "drawbox=x=620:y=476:w=100:h=100:color=white:t=fill,setsar=16/15,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
            "-c:a",
            "aac",
            "-n",
            str(base),
        ]
    )
    assert result.exit_code == 0
    info = probe_media(base, media_tools.probe)
    from fabu.media.probe import scan_frame_geometry

    assert scan_frame_geometry(info, media_tools.probe) == 30
    assert info.sar == Fraction(16, 15) and info.audio.sample_rate == 48000
    assert (fit_output(info).width, fit_output(info).height) == (1920, 1440)
    for angle in [90, 180, 270]:
        target = tmp_path / f"rotate-{angle}.mp4"
        r = media_tools.ffmpeg(
            [
                "-v",
                "error",
                "-nostdin",
                "-display_rotation",
                str(angle),
                "-i",
                str(base),
                "-c",
                "copy",
                "-n",
                str(target),
            ]
        )
        assert r.exit_code == 0
        rotated = probe_media(target, media_tools.probe)
        assert rotated.rotation_deg == angle
        assert rotated.display_aspect == (Fraction(3, 4) if angle in (90, 270) else Fraction(4, 3))
    versions = media_tools.versions()
    assert versions["ffmpeg"].startswith("ffmpeg version ")
    assert versions["ffprobe"].startswith("ffprobe version ")
