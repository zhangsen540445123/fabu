"""Actual FFmpeg + downloader validation + fake remote boundaries, never real posts."""

import io
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
import test_gateway as gateway_fixtures
import test_local_policy as policy_fixtures
from media import conftest as media_fixtures
from PIL import Image
from test_api_security import login
from test_live_coordinator import transport

gateway_setup = gateway_fixtures.gateway_setup
local_setup = policy_fixtures.local_setup
media_tools = media_fixtures.media_tools


@pytest.mark.media
def test_link_title_download_render_upload_three_targets_and_restart(
    local_setup, publishing_context, media_tools, tmp_path
):
    from datetime import timedelta

    from fabu.api.app import create_app
    from fabu.media.service import MediaService
    from fabu.media.source import download_source
    from fabu.media.thumbnail import ImageResponse
    from fabu.publishing.journal import SqliteUploadJournal
    from fabu.publishing.provider_tracking import ProviderTracker
    from fabu.tools import CommandResult
    from fabu.worker import Worker

    _, previous, _, gateway = local_setup
    store = publishing_context[0]
    app = create_app(
        previous.state.settings.model_copy(update={"tools_dir": media_tools.ffmpeg_path.parent}),
        store,
    )
    app.state.credentials = gateway.credentials
    gateway.media_tools = media_tools.ffmpeg_path.parent
    gateway.authority = app.state.publication_authority
    policy = app.state.live_policy
    policy.enable()
    config = publishing_context[6]
    with store.transaction() as db:
        # Keep fixture READY task out of processing; no source/job duplicate publication exists.
        for item in config.accounts:
            db.execute(
                "INSERT INTO publication_catalog VALUES ('live',?,?,?,?)",
                (
                    item.account.account_id,
                    item.account.model_dump_json(),
                    item.category.model_dump_json() if item.category else None,
                    datetime.now(UTC).isoformat(),
                ),
            )
    client, headers = login(app)
    assert (
        client.put(
            "/api/publishing/defaults",
            headers=headers,
            json={
                "scope": "live",
                "accounts": [
                    {
                        "account_id": x.account.account_id,
                        "declaration": x.declaration.model_dump(mode="json"),
                    }
                    for x in config.accounts
                ],
            },
        ).status_code
        == 200
    )
    response = client.post(
        "/api/workflow/jobs",
        headers=headers,
        json={"source_url": "https://youtu.be/Z6imSiJLd9Q", "title": "沉浸式剪发"},
    )
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    boundary_calls = []
    image = io.BytesIO()
    Image.new("RGB", (1280, 720), "green").save(image, format="JPEG")

    def downloader(args, workspace):
        boundary_calls.append("inspect" if "--dump-single-json" in args else "download")
        if "--dump-single-json" in args:
            return CommandResult(
                exit_code=0,
                stderr=b"",
                stdout=json.dumps(
                    {
                        "id": "Z6imSiJLd9Q",
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
                ).encode(),
            )
        path = Path(args[args.index("--output") + 1].replace("%(ext)s", "mkv"))
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
                str(path),
            ]
        )
        assert result.exit_code == 0
        return CommandResult(exit_code=0, stderr=b"", stdout=str(path).encode())

    def loader(job):
        return download_source(
            "Z6imSiJLd9Q",
            tmp_path / "download",
            downloader,
            assets=app.state.assets,
            probe_runner=media_tools.probe,
            thumbnail_transport=lambda url: ImageResponse(
                status=200, url=url, content_type="image/jpeg", body=image.getvalue()
            ),
        )

    service = MediaService(
        store,
        app.state.presets,
        app.state.assets,
        app.state.approvals,
        media_tools,
        tmp_path / "rendered",
        loader,
    )

    def factory(snapshot, permit, cancel):
        transport(gateway, snapshot)
        gateway.journal = SqliteUploadJournal(store, snapshot, scope="live")
        gateway.bind_user_job(policy, permit, snapshot)
        return gateway

    app.state.publication_queue.live_factory = factory
    with Worker(store) as worker:
        assert worker.run_once(service.process_job)
        ready = store.get(identifier)
        assert ready.state == "READY"
        assert ready.output_manifest["media_info"]["codec"] == "hevc"
        assert app.state.publication_profiles.process_ready(worker, threading.Event())
        assert app.state.publication_queue.process_next(
            worker, threading.Event(), lambda **kw: None
        )
    assert boundary_calls == ["inspect", "download"]
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM preview_approvals").fetchone()[0] == 0
        assert (
            db.execute(
                "SELECT COUNT(*) FROM upload_attempts WHERE scope='live' AND state='COMPLETE'"
            ).fetchone()[0]
            == 2
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM target_attempts WHERE job_id=? AND state='PROVIDER_ACCEPTED'",
                (identifier,),
            ).fetchone()[0]
            == 3
        )
    assert store.get(identifier).content_frozen
    app.state.publication_queue.recover_interrupted()
    app.state.publication_profiles.recover_interrupted()
    with Worker(store) as worker:
        assert not app.state.publication_profiles.process_ready(worker, threading.Event())
        assert not app.state.publication_queue.process_next(
            worker, threading.Event(), lambda **kw: None
        )
    assert (
        client.get(f"/api/jobs/{identifier}/targets?scope=live").json()["reason"]
        == "provider_tracking_pending"
    )
    # Exercise the real read-only tracker and API projection, not a platform-success claim.
    now = [datetime.now(UTC)]
    queried = []

    class ReadOnly:
        def query(self, operation):
            assert operation == "version"
            return {"version": "3.2.32"}

        def details(self, task_set):
            queried.append(task_set)
            account = task_set.removeprefix("task-")
            platform = next(
                x.account.platform for x in config.accounts if x.account.account_id == account
            )
            return {
                "id": task_set,
                "tasks": [
                    {
                        "platform": platform,
                        "platformAccountId": account,
                        "stageStatus": "success",
                        "publishId": "synthetic-content-" + platform,
                    }
                ],
                "page": 1,
                "totalPage": 1,
                "totalSize": 1,
            }

    app.state.readonly_cli_factory = lambda *args: ReadOnly()
    tracker = ProviderTracker(app, clock=lambda: now[0])
    assert tracker.process_due(threading.Event())
    assert queried == []
    now[0] += timedelta(seconds=31)
    for _ in range(3):
        assert tracker.process_due(threading.Event())
    status = client.get(f"/api/jobs/{identifier}/targets?scope=live").json()
    assert len(set(queried)) == 3
    assert [t["provider_observation"]["status"] for t in status["targets"]] == ["success"] * 3
    assert all(
        t["state"] == "PROVIDER_ACCEPTED"
        and t["provider_observation"]["quality_status"] == "unverified"
        for t in status["targets"]
    )
    assert store.get(identifier).state == "PAUSED"
    assert status["reason"] == "provider_reported_success"
    assert (
        client.get(f"/api/jobs/{identifier}").json()["pause_reason"] == "provider_reported_success"
    )
    assert store.get(identifier).pause_reason == "provider_tracking_pending"
