import io

import pytest
from PIL import Image

from fabu.assets import AssetStore
from fabu.store import JobStore


def picture(size=(1280, 720)):
    stream = io.BytesIO()
    Image.new("RGB", size, "#205035").save(stream, format="JPEG")
    return stream.getvalue()


@pytest.fixture
def assets(tmp_path):
    return AssetStore(tmp_path / "assets", JobStore(tmp_path / "db.sqlite3"))


def test_thumbnail_placeholder_falls_back(tmp_path, assets):
    from fabu.media.thumbnail import ImageResponse, fetch_thumbnail

    visited = []

    def transport(url):
        visited.append(url)
        body = picture((120, 90) if "maxresdefault" in url else (640, 480))
        return ImageResponse(status=200, url=url, content_type="image/jpeg", body=body)

    ref = fetch_thumbnail("L6imSiJLd9Q", transport, assets=assets, workspace=tmp_path / "job")
    assert len(visited) == 2 and "sddefault" in visited[-1]
    with Image.open(ref.local_path) as image:
        assert image.size == (640, 480)
    assert assets.verify(ref.id)


def test_thumbnail_all_missing_pauses_without_frame_fallback(tmp_path, assets):
    from fabu.media.thumbnail import ImageResponse, ThumbnailUnavailable, fetch_thumbnail

    with pytest.raises(ThumbnailUnavailable):
        fetch_thumbnail(
            "L6imSiJLd9Q",
            lambda url: ImageResponse(status=404, url=url, content_type="", body=b""),
            assets=assets,
            workspace=tmp_path / "job",
        )
    assert not list(assets.root.iterdir())


def test_thumbnail_rejects_redirect_or_body_attack(tmp_path, assets):
    from fabu.media.thumbnail import ImageResponse, ThumbnailUnavailable, fetch_thumbnail

    for response in [
        ImageResponse(
            status=200, url="http://127.0.0.1/private", content_type="image/jpeg", body=picture()
        ),
        ImageResponse(
            status=200,
            url="https://i.ytimg.com/vi/L6imSiJLd9Q/maxresdefault.jpg",
            content_type="text/html",
            body=b"<script/>",
        ),
    ]:
        with pytest.raises(ThumbnailUnavailable):
            fetch_thumbnail(
                "L6imSiJLd9Q",
                lambda _, result=response: result,
                assets=assets,
                workspace=tmp_path / "job",
            )


def test_thumbnail_invalid_id_never_calls_transport(tmp_path, assets):
    from fabu.media.thumbnail import fetch_thumbnail

    calls = []
    with pytest.raises(ValueError):
        fetch_thumbnail(
            "../../x", lambda url: calls.append(url), assets=assets, workspace=tmp_path / "job"
        )
    assert calls == []


def test_http_image_transport_refuses_untrusted_host():
    from fabu.media.thumbnail import HttpImageTransport, ThumbnailUnavailable

    with pytest.raises(ThumbnailUnavailable):
        HttpImageTransport()("https://example.com/image.jpg")
