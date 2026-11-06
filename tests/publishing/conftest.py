import pytest


@pytest.fixture
def publishing_context(tmp_path, monkeypatch):
    # These structural fixtures do not prove media correctness; real-media tests do that.
    import hashlib
    from datetime import UTC, datetime
    from fractions import Fraction

    from PIL import Image

    from fabu.assets import AssetStore
    from fabu.domain import JobSpec
    from fabu.media.approvals import PreviewApprovalStore
    from fabu.media.compiler import compile_plan
    from fabu.media.models import InputBundle, OutputManifest
    from fabu.media.probe import AudioInfo, ColorInfo, MediaInfo
    from fabu.media.source import SelectedFormat, SourceManifest
    from fabu.presets import PresetStore
    from fabu.publishing.models import (
        AccountBinding,
        AccountPublishSettings,
        CategoryEvidence,
        PublicationConfig,
        SourceDeclaration,
    )
    from fabu.store import JobStore

    store = JobStore(tmp_path / "state.sqlite3")
    assets = AssetStore(tmp_path / "assets", store)
    presets = PresetStore(store, assets)
    preset = presets.save_draft(None, {"name": "test", "steps": [], "output": {}})
    p = tmp_path / "source.mp4"
    p.write_bytes(b"0000ftyp" + b"media" * 20)
    source = assets.import_file(p, "video")
    image = tmp_path / "cover.png"
    Image.new("RGB", (1280, 720), "red").save(image)
    cover = assets.import_file(image, "image")
    info = MediaInfo(
        path=source.local_path,
        sha256=source.sha256,
        duration=Fraction(1),
        video_stream_index=0,
        codec="h264",
        coded_width=320,
        coded_height=180,
        sar=Fraction(1),
        rotation_deg=0,
        fps=Fraction(30),
        pixel_format="yuv420p",
        color=ColorInfo(space="bt709", transfer="bt709", primaries="bt709", range="tv"),
        start_time=Fraction(0),
        audio=AudioInfo(
            stream_index=1,
            codec="aac",
            sample_rate=44100,
            channels=1,
            channel_layout="mono",
            duration=Fraction(1),
            start_time=Fraction(0),
        ),
    )
    plan = compile_plan(InputBundle(source=info), preset)
    accounts = tuple(
        AccountBinding(account_id=f"{platform}-id", platform=platform, nickname=nick, active=True)
        for platform, nick in [("douyin", "ASMR"), ("toutiao", "ASMR"), ("bilibili", "枕畔慢时光")]
    )
    job = store.create(
        JobSpec(
            source_url="https://youtu.be/L6imSiJLd9Q",
            title="沉浸式剪发",
            preset_version_id=preset.id,
            target_ids=[a.account_id for a in accounts],
            publish_mode="simulation",
        )
    )
    source_manifest = SourceManifest(
        video_id="L6imSiJLd9Q",
        source=source,
        thumbnail=cover,
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
    output_path = tmp_path / "output.mp4"
    output_path.write_bytes(b"verified-output")
    output_info = info.model_copy(
        update={
            "path": output_path,
            "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
            "codec": "hevc",
            "coded_width": 2560,
            "coded_height": 1440,
        }
    )
    output = OutputManifest(
        path=output_path,
        sha256=output_info.sha256,
        size_bytes=output_path.stat().st_size,
        media_info=output_info,
        plan_digest=plan.digest,
        verified_at=datetime.now(UTC),
    )
    with store.transaction() as db:
        db.execute(
            "UPDATE jobs SET state='READY',source_json=?,plan_digest=?,output_json=? WHERE id=?",
            (source_manifest.model_dump_json(), plan.digest, output.model_dump_json(), job.id),
        )
        db.execute(
            "INSERT INTO processing_snapshots VALUES (?,?,?,?,?,?)",
            (
                job.id,
                job.content_revision,
                preset.id,
                source_manifest.model_dump_json(),
                plan.model_dump_json(),
                datetime.now(UTC).isoformat(),
            ),
        )
    approvals = PreviewApprovalStore(store, presets, assets)
    monkeypatch.setattr(approvals, "approval_matches", lambda *args: True)
    category = CategoryEvidence(
        account_id="bilibili-id",
        queried_at=datetime.now(UTC),
        path=(
            {
                "yixiaoerId": "1031",
                "yixiaoerName": "生活经验",
                "raw": {"id": 1031, "name": "生活经验"},
            },
        ),
    )
    settings = tuple(
        AccountPublishSettings(
            account=a,
            declaration=SourceDeclaration(
                origin="licensed_reuse",
                confirmed=True,
                bilibili_type="repost" if a.platform == "bilibili" else None,
                statement="offsite" if a.platform == "toutiao" else "none",
            ),
            category=category if a.platform == "bilibili" else None,
        )
        for a in accounts
    )
    config = PublicationConfig(accounts=settings)
    return store, assets, presets, approvals, store.get(job.id), output, config
