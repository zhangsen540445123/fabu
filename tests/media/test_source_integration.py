import io
import json
from pathlib import Path

import pytest
from PIL import Image

from fabu.assets import AssetStore
from fabu.store import JobStore
from fabu.tools import CommandResult


@pytest.mark.media
def test_download_real_file_verify_manifest_and_resume_after_thumbnail_failure(
    tmp_path, media_tools
):
    from fabu.media.source import download_source
    from fabu.media.thumbnail import ImageResponse, ThumbnailUnavailable

    assets = AssetStore(tmp_path / "assets", JobStore(tmp_path / "db.sqlite3"))
    calls = []

    def runner(args, workspace):
        calls.append(args)
        if "--dump-single-json" in args:
            data = {
                "id": "L6imSiJLd9Q",
                "duration": 1,
                "formats": [
                    {
                        "format_id": "muxed",
                        "width": 320,
                        "height": 180,
                        "fps": 30,
                        "vcodec": "h264",
                        "acodec": "aac",
                        "ext": "mp4",
                        "filesize": 100000,
                    }
                ],
            }
            return CommandResult(exit_code=0, stdout=json.dumps(data).encode(), stderr=b"")
        out = Path(args[args.index("--output") + 1].replace("%(ext)s", "mkv"))
        result = media_tools.ffmpeg(
            [
                "-v",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "color=c=green:s=320x180:r=30:d=1,setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
                "-f",
                "lavfi",
                "-i",
                "sine=sample_rate=44100:duration=1",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-n",
                str(out),
            ]
        )
        assert result.exit_code == 0
        return CommandResult(exit_code=0, stdout=str(out).encode("utf-8"), stderr=b"")

    def missing(url):
        return ImageResponse(status=404, url=url, content_type="", body=b"")

    with pytest.raises(ThumbnailUnavailable):
        download_source(
            "L6imSiJLd9Q",
            tmp_path / "job",
            runner,
            assets=assets,
            probe_runner=media_tools.probe,
            thumbnail_transport=missing,
        )
    assert len(calls) == 2
    image = io.BytesIO()
    Image.new("RGB", (1280, 720), "green").save(image, format="JPEG")

    def cover(url):
        return ImageResponse(status=200, url=url, content_type="image/jpeg", body=image.getvalue())

    manifest = download_source(
        "L6imSiJLd9Q",
        tmp_path / "job",
        runner,
        assets=assets,
        probe_runner=media_tools.probe,
        thumbnail_transport=cover,
    )
    assert len(calls) == 2, "Verified source should be reused when only its thumbnail failed"
    assert manifest.video_id == "L6imSiJLd9Q" and assets.verify(manifest.source.id)
    assert assets.verify(manifest.thumbnail.id)
    assert "https://" not in manifest.model_dump_json()
