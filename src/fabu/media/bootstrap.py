"""Bind explicit user-configured tools; no executable PATH search or paid fallback."""

import sys
from pathlib import Path
from threading import Event

from fabu.browser_auth.bridge import BrowserSessionClient
from fabu.media.runtime import MediaTools
from fabu.media.service import MediaService, ProcessingPaused
from fabu.media.source import YtDlpRunner, download_source, parse_youtube_url
from fabu.media.thumbnail import HttpImageTransport


def configured_service(app, cancel: Event) -> MediaService:
    with app.state.settings_lock:
        settings = app.state.settings
    tools = MediaTools(
        ffmpeg_path=settings.tools_dir / "ffmpeg.exe",
        ffprobe_path=settings.tools_dir / "ffprobe.exe",
    )

    def load(job):
        if settings.node_path is None:
            raise ProcessingPaused("node_not_configured")
        runner = YtDlpRunner(
            Path(sys.executable),
            settings.node_path,
            tools.ffmpeg_path,
            cancel,
            browser_session=BrowserSessionClient(settings.state_dir / "browser-auth"),
        )
        return download_source(
            parse_youtube_url(job.spec.source_url),
            settings.data_dir / "downloads" / job.id / parse_youtube_url(job.spec.source_url),
            runner,
            assets=app.state.assets,
            probe_runner=lambda args: tools.probe(args, cancel),
            thumbnail_transport=HttpImageTransport(cancel),
        )

    return MediaService(
        app.state.store,
        app.state.presets,
        app.state.assets,
        app.state.approvals,
        tools,
        settings.data_dir / "processed",
        load,
    )
