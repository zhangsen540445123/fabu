import os
from pathlib import Path

import pytest


@pytest.fixture
def media_tools():
    from fabu.media.runtime import MediaTools

    paths = [os.environ.get("FABU_TEST_FFMPEG"), os.environ.get("FABU_TEST_FFPROBE")]
    if not all(paths):
        pytest.fail("Real media acceptance requires FABU_TEST_FFMPEG/FABU_TEST_FFPROBE; no skip")
    return MediaTools(ffmpeg_path=Path(paths[0]), ffprobe_path=Path(paths[1]))


@pytest.fixture
def context(tmp_path, media_tools):
    from datetime import UTC, datetime

    from PIL import Image
    from test_composition import make_clip

    from fabu.assets import AssetStore
    from fabu.domain import JobSpec
    from fabu.media.approvals import PreviewApprovalStore
    from fabu.media.service import MediaService
    from fabu.media.source import SelectedFormat, SourceManifest
    from fabu.presets import PresetStore
    from fabu.store import JobStore

    store = JobStore(tmp_path / "state.sqlite3")
    assets = AssetStore(tmp_path / "assets", store)
    presets = PresetStore(store, assets)
    preset = presets.save_draft(None, {"name": "baseline", "steps": [], "output": {}})
    source = assets.import_file(
        make_clip(tmp_path / "source.mp4", media_tools, duration="0.5"), "video"
    )
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (320, 180), "red").save(cover)
    thumb = assets.import_file(cover, "image")
    manifest = SourceManifest(
        video_id="L6imSiJLd9Q",
        source=source,
        thumbnail=thumb,
        selected_format=SelectedFormat(
            selector="test",
            video_format_id="test",
            audio_format_id=None,
            width=320,
            height=180,
            fps=30,
        ),
        fetched_at=datetime.now(UTC),
    )
    calls = []

    def load(job):
        calls.append(job.id)
        return manifest

    approvals = PreviewApprovalStore(store, presets, assets)
    service = MediaService(store, presets, assets, approvals, media_tools, tmp_path / "jobs", load)
    job = store.create(
        JobSpec(
            source_url="https://youtu.be/L6imSiJLd9Q", title="剪发", preset_version_id=preset.id
        )
    )
    return service, store, presets, approvals, preset, job, calls
