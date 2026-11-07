"""Isolated browser test: synthetic local video, real FFmpeg, no external account or publication."""

import os
import tempfile
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from PIL import Image, ImageDraw

from fabu.api.app import create_app
from fabu.media.runtime import MediaTools
from fabu.media.service import MediaService
from fabu.media.source import SelectedFormat, SourceManifest, parse_youtube_url
from fabu.publishing.fake_gateway import FakePublisher
from fabu.settings import load_settings
from fabu.store import JobStore

with tempfile.TemporaryDirectory(prefix="fabu-e2e-") as folder:
    root = Path(folder)
    tools = MediaTools(
        ffmpeg_path=Path(os.environ["FABU_TEST_FFMPEG"]),
        ffprobe_path=Path(os.environ["FABU_TEST_FFPROBE"]),
    )
    source = root / "synthetic.mp4"
    result = tools.ffmpeg(
        [
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=30:d=1,"
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=1",
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
    if result.exit_code:
        raise RuntimeError("Synthetic browser fixture could not be created")
    cover = root / "test-cover.jpg"
    image = Image.new("RGB", (1280, 720), "#345e4b")
    ImageDraw.Draw(image).text((100, 300), "SYNTHETIC LOCAL TEST COVER", fill="white", font_size=42)
    image.save(cover)
    settings = load_settings(
        {"LOCALAPPDATA": str(root), "FABU_DATA_DIR": str(root / "media"), "FABU_PORT": "8766"}
    )
    store = JobStore(settings.db_path)

    def service_factory(cancel):
        def local_source(job):
            return SourceManifest(
                video_id=parse_youtube_url(job.spec.source_url),
                source=source_ref,
                thumbnail=cover_ref,
                selected_format=SelectedFormat(
                    selector="fixture",
                    video_format_id="fixture",
                    audio_format_id=None,
                    width=320,
                    height=180,
                    fps=30,
                ),
                fetched_at=datetime.now(UTC),
            )

        return MediaService(
            store,
            app.state.presets,
            app.state.assets,
            app.state.approvals,
            tools,
            settings.data_dir / "processed",
            local_source,
        )

    app = create_app(settings, store, enable_worker=True, service_factory=service_factory)

    class ScenarioPublisher(FakePublisher):
        def prepare(self, snapshot, permit):
            if "恢复验收" in snapshot.title:
                self.outcomes = deque(["UNKNOWN"])
            elif "自动验收" in snapshot.title:
                self.outcomes = deque()
            return super().prepare(snapshot, permit)

    app.state.publication_queue.publisher_factory = lambda authority, cancel: ScenarioPublisher(
        authority, outcomes=["SUBMITTED", "FAILED", "SUBMITTED"], cancel=cancel
    )
    app.state.media_tool_identity = tools.identity
    source_ref = app.state.assets.import_file(source, "video")
    cover_ref = app.state.assets.import_file(cover, "image")
    app.state.security.bootstrap_token = "synthetic-e2e-bootstrap-not-a-real-credential"
    uvicorn.run(app, host="127.0.0.1", port=8766, access_log=False, log_level="warning")
