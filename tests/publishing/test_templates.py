import pytest


@pytest.mark.parametrize(
    "title,group,tags",
    [
        ("剃须仙人的日常", "WRHkkYwU", ["解压", "ASMR", "剃须仙人", "助眠"]),
        ("剃光头", "2aZjVP8u", ["ASMR", "解压", "助眠", "剃光头"]),
        ("沉浸式剪发", "QKHLZMFO", ["解压", "助眠", "ASMR", "剪发"]),
        ("  剃光头的日常  ", "2aZjVP8u", ["ASMR", "解压", "助眠", "剃光头"]),
    ],
)
def test_title_topic_rules(title, group, tags):
    from fabu.publishing.topics import select_topic_group

    selected = select_topic_group(title, None)
    assert selected.status == "selected" and selected.group_name == group
    assert list(selected.tags) == tags and group not in selected.tags
    assert selected.source == "title"


@pytest.mark.parametrize(
    "title,reason",
    [("剃光头与剪发", "ambiguous_match"), ("助眠视频", "no_match"), ("普通标题", "no_match")],
)
def test_zero_or_multiple_matches_pause_without_default(title, reason):
    from fabu.publishing.topics import select_topic_group

    selected = select_topic_group(title, None)
    assert selected.status == "needs_selection" and selected.reason == reason
    assert selected.group_name is None and not selected.tags


def test_manual_topic_override_wins():
    from fabu.publishing.topics import select_topic_group

    selected = select_topic_group("剃光头与剪发", "助眠")
    assert selected.status == "selected" and selected.group_name == "助眠"
    assert selected.source == "manual" and list(selected.tags) == ["解压", "助眠"]
    with pytest.raises(ValueError):
        select_topic_group("剃光头", "unrecognized-group")


def test_category_parser_retains_query_path_and_raw_without_fake_ambiguity():
    from fabu.publishing.templates import select_bilibili_category

    raw = {"id": 1031, "name": "生活经验", "extra": {"keep": True}}
    child = {"yixiaoerId": "1031", "yixiaoerName": "生活经验", "raw": raw}
    parent = {"yixiaoerId": "160", "yixiaoerName": "生活", "raw": {"id": 160}, "children": [child]}
    found = select_bilibili_category({"dataList": [parent]})
    assert [n["yixiaoerName"] for n in found] == ["生活", "生活经验"]
    assert found[-1]["raw"] == raw and found[0] == parent
    flat = select_bilibili_category({"dataList": [child]})
    assert flat == (child,)
    with pytest.raises(ValueError, match="ambiguous"):
        select_bilibili_category({"dataList": [child, {**child, "yixiaoerId": "different"}]})
    with pytest.raises(ValueError):
        select_bilibili_category({"dataList": []})


def test_authorized_does_not_imply_self_shot():
    from fabu.publishing.models import SourceDeclaration
    from fabu.publishing.templates import declaration_fields

    with pytest.raises(ValueError):
        SourceDeclaration(origin="licensed_reuse", confirmed=False)
    with pytest.raises(ValueError):
        declaration_fields(
            "bilibili",
            SourceDeclaration(
                origin="licensed_reuse",
                bilibili_type="self_produced",
                statement="none",
                confirmed=True,
            ),
            "https://youtu.be/L6imSiJLd9Q",
        )
    with pytest.raises(ValueError):
        declaration_fields(
            "toutiao",
            SourceDeclaration(origin="licensed_reuse", statement="self_shot", confirmed=True),
            "https://youtu.be/L6imSiJLd9Q",
        )
    fields = declaration_fields(
        "bilibili",
        SourceDeclaration(
            origin="licensed_reuse", bilibili_type="repost", statement="none", confirmed=True
        ),
        "https://youtu.be/L6imSiJLd9Q",
    )
    assert fields == {
        "createType": 2,
        "contentSourceUrl": "https://youtu.be/L6imSiJLd9Q",
        "declaration": 0,
    }
    assert (
        declaration_fields(
            "toutiao",
            SourceDeclaration(origin="licensed_reuse", statement="offsite", confirmed=True),
            "source",
        )["declaration"]
        == 2
    )


def build(context, **kwargs):
    from fabu.publishing.templates import build_publish_snapshot

    store, assets, presets, approvals, job, output, config = context
    return build_publish_snapshot(
        job,
        output,
        config,
        store=store,
        assets=assets,
        presets=presets,
        approvals=approvals,
        **kwargs,
    )


def test_three_platforms_share_content_and_preserve_declared_fields(publishing_context):
    from fabu.publishing.models import verify_publish_snapshot

    snap = build(publishing_context)
    verify_publish_snapshot(snap)
    assert snap.title == "沉浸式剪发" and snap.mode == "simulation"
    assert len(snap.targets) == 3
    assert snap.video_id == "L6imSiJLd9Q"
    assert snap.topic.group_name == "QKHLZMFO"
    forms = {target.account.platform: target.form for target in snap.targets}
    for form in forms.values():
        assert form["title"] == snap.title and form["description"] == snap.title
        assert form["tags"] == list(snap.topic.tags)
        assert "scheduledTime" not in form
        assert "challenge" not in form and "collection" not in form
    assert forms["bilibili"]["createType"] == 2
    assert forms["toutiao"]["declaration"] == 2
    assert forms["douyin"]["visibleType"] == 0
    assert snap.cover.sha256 == publishing_context[4].source_manifest["thumbnail"]["sha256"]


def test_wrong_revision_output_rejected(publishing_context):
    from fabu.publishing.templates import build_publish_snapshot

    store, assets, presets, approvals, job, output, config = publishing_context
    for wrong_job, wrong_output in [
        (job.model_copy(update={"revision": job.revision + 1}), output),
        (job, output.model_copy(update={"plan_digest": "0" * 64})),
    ]:
        with pytest.raises(ValueError):
            build_publish_snapshot(
                wrong_job,
                wrong_output,
                config,
                store=store,
                assets=assets,
                presets=presets,
                approvals=approvals,
            )
    monkey_job = job.model_copy(
        update={"spec": job.spec.model_copy(update={"title": "changed without revision"})}
    )
    with pytest.raises(ValueError):
        build_publish_snapshot(
            monkey_job,
            output,
            config,
            store=store,
            assets=assets,
            presets=presets,
            approvals=approvals,
        )


def test_unapproved_or_corrupt_cover_cannot_create_snapshot(publishing_context, monkeypatch):
    import os
    import stat

    store, assets, presets, approvals, job, output, config = publishing_context
    monkeypatch.setattr(approvals, "approval_matches", lambda *args: False)
    with pytest.raises(ValueError):
        build(publishing_context)
    monkeypatch.setattr(approvals, "approval_matches", lambda *args: True)
    cover = assets.get(job.source_manifest["thumbnail"]["id"])
    os.chmod(cover.local_path, stat.S_IWRITE | stat.S_IREAD)
    cover.local_path.write_bytes(b"bad")
    with pytest.raises(ValueError):
        build(publishing_context)


def test_overlength_title_never_silently_truncated(publishing_context):
    store, _, _, _, job, _, _ = publishing_context
    with store.transaction() as db:
        spec = job.spec.model_copy(update={"title": "剪发" + "长" * 29})
        db.execute("UPDATE jobs SET spec_json=? WHERE id=?", (spec.model_dump_json(), job.id))
    context = (*publishing_context[:4], store.get(job.id), *publishing_context[5:])
    with pytest.raises(ValueError, match="title"):
        build(context)


def test_native_schedule_uses_one_exact_utc_millisecond(publishing_context):
    from datetime import UTC, datetime, timedelta

    store, _, _, _, job, _, _ = publishing_context
    when = datetime.now(UTC) + timedelta(days=1)
    when = when.replace(microsecond=123000)
    spec = job.spec.model_copy(update={"publish_mode": "native_schedule", "scheduled_at_utc": when})
    with store.transaction() as db:
        db.execute("UPDATE jobs SET spec_json=? WHERE id=?", (spec.model_dump_json(), job.id))
    context = (*publishing_context[:4], store.get(job.id), *publishing_context[5:])
    snap = build(context)
    assert snap.scheduled_at_utc == when
    assert {t.form["scheduledTime"] for t in snap.targets} == {int(when.timestamp() * 1000)}
    assert all(t.form.get("pubType", 1) == 1 for t in snap.targets)


def test_content_frozen_and_ambiguous_topics_cannot_build_new_snapshot(publishing_context):
    store, _, _, _, job, _, _ = publishing_context
    with store.transaction() as db:
        db.execute("UPDATE jobs SET content_frozen=1 WHERE id=?", (job.id,))
    with pytest.raises(ValueError):
        build((*publishing_context[:4], store.get(job.id), *publishing_context[5:]))
    with store.transaction() as db:
        spec = job.spec.model_copy(update={"title": "剃光头与剪发"})
        db.execute(
            "UPDATE jobs SET content_frozen=0,spec_json=? WHERE id=?",
            (spec.model_dump_json(), job.id),
        )
    context = (*publishing_context[:4], store.get(job.id), *publishing_context[5:])
    with pytest.raises(ValueError, match="topic"):
        build(context)


def test_source_manifest_must_match_processing_snapshot(publishing_context):
    import json

    store, _, _, _, job, _, _ = publishing_context
    source = job.source_manifest.copy()
    source["source"] = {**source["source"], "sha256": "f" * 64}
    with store.transaction() as db:
        db.execute("UPDATE jobs SET source_json=? WHERE id=?", (json.dumps(source), job.id))
    with pytest.raises(ValueError, match="source"):
        build((*publishing_context[:4], store.get(job.id), *publishing_context[5:]))


def test_snapshot_tampering_is_detected_and_config_does_not_alias(publishing_context):
    from fabu.publishing.models import verify_publish_snapshot

    snapshot = build(publishing_context)
    publishing_context[-1].accounts[2].category.path[0]["raw"]["name"] = "mutated external config"
    verify_publish_snapshot(snapshot)
    assert snapshot.targets[2].form["category"][0]["raw"]["name"] == "生活经验"
    snapshot.targets[0].form["title"] = "injected title"
    with pytest.raises(ValueError):
        verify_publish_snapshot(snapshot)


def test_inactive_accounts_category_age_and_mismatched_target_fail(publishing_context):
    from datetime import timedelta

    from fabu.publishing.models import PublicationConfig

    *rest, config = publishing_context
    for update in [
        {
            "accounts": (
                config.accounts[0].model_copy(
                    update={
                        "account": config.accounts[0].account.model_copy(update={"active": False})
                    }
                ),
                *config.accounts[1:],
            )
        },
        {"accounts": config.accounts[:2]},
        {
            "accounts": (
                *config.accounts[:2],
                config.accounts[2].model_copy(
                    update={
                        "category": config.accounts[2].category.model_copy(
                            update={
                                "queried_at": config.accounts[2].category.queried_at
                                - timedelta(days=2)
                            }
                        )
                    }
                ),
            )
        },
    ]:
        changed = PublicationConfig.model_validate({**config.model_dump(), **update})
        with pytest.raises(ValueError):
            build((*rest, changed))


def test_timestamp_precision_is_explicit():
    from datetime import UTC, datetime

    from fabu.publishing.templates import utc_milliseconds

    assert utc_milliseconds(datetime(2026, 11, 7, 0, 0, 0, 123000, tzinfo=UTC)) == 1794009600123
    with pytest.raises(ValueError):
        utc_milliseconds(datetime(2026, 11, 7))
    with pytest.raises(ValueError):
        utc_milliseconds(datetime(2026, 11, 7, microsecond=123456, tzinfo=UTC))


def test_signed_snapshot_does_not_allow_semantically_divergent_forms(publishing_context):
    from datetime import UTC, datetime, timedelta

    from fabu.publishing.models import publish_snapshot_digest, verify_publish_snapshot

    snapshot = build(publishing_context)
    when = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    snapshot = snapshot.model_copy(update={"mode": "native_schedule", "scheduled_at_utc": when})
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    with pytest.raises(ValueError, match="schedule"):
        verify_publish_snapshot(snapshot)
