"""Prepare metadata only, using trusted local approvals and verified outputs."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from PIL import Image

from fabu.assets import AssetStore
from fabu.domain import JobSnapshot
from fabu.media.approvals import PreviewApprovalStore
from fabu.media.models import OutputManifest
from fabu.media.service import (
    load_job_plan,
    require_approved_job_output,
    require_verified_job_output,
)
from fabu.media.source import SourceManifest, parse_youtube_url
from fabu.presets import PresetStore
from fabu.publishing.models import (
    AccountPublishSettings,
    PlatformTarget,
    PublicationConfig,
    PublishSnapshot,
    SourceDeclaration,
    publish_snapshot_digest,
)
from fabu.publishing.topics import select_topic_group
from fabu.store import JobStore

# Reviewed official packaged schemas + references/platforms/video/* for yxer3.2.32.
# These define metadata, NOT verified free entitlements or actual platform acceptance.
LIMITS = {
    "douyin": (30, 1000, 1, 10, None),
    "toutiao": (30, 400, 1, 5, 20),
    "bilibili": (80, 2000, 0, 10, 20),
}
STATEMENTS = {
    "douyin": {"none": 0, "ai": 3, "discomfort": 4, "fiction": 5, "dangerous": 6},
    "toutiao": {"none": 0, "self_shot": 1, "offsite": 2, "ai": 3, "fiction": 6},
    "bilibili": {
        "none": 0,
        "ai": 1,
        "dangerous": 2,
        "entertainment": 3,
        "discomfort": 4,
        "rational_spending": 5,
        "personal_opinion": 6,
    },
}


def select_bilibili_category(data: dict, name: str = "生活经验") -> tuple[dict, ...]:
    matches = []
    if not isinstance(data, dict):
        raise ValueError("Unrecognized category response")
    if isinstance(data.get("paths"), list):
        for item in data["paths"]:
            path = item.get("category") if isinstance(item, dict) else None
            if isinstance(path, list) and path and path[-1].get("yixiaoerName") == name:
                matches.append(tuple(deepcopy(path)))
    else:
        rows = data.get("dataList", data.get("categories"))
        if not isinstance(rows, list):
            raise ValueError("Unrecognized category list")

        def visit(nodes, parents=()):
            if len(parents) > 20:
                raise ValueError("Category tree is too deep")
            for node in nodes:
                if not isinstance(node, dict):
                    raise ValueError("Invalid category node")
                path = (*parents, node)
                if node.get("yixiaoerName") == name:
                    matches.append(tuple(deepcopy(path)))
                children = node.get("children", [])
                if not isinstance(children, list):
                    raise ValueError("Invalid category children")
                visit(children, path)

        visit(rows)
    if len(matches) != 1:
        raise ValueError("Category missing or ambiguous; select a verified full query path")
    for node in matches[0]:
        if not all(k in node for k in ("yixiaoerId", "yixiaoerName", "raw")) or not isinstance(
            node["raw"], dict
        ):
            raise ValueError("Incomplete category object")
    return matches[0]


def declaration_fields(platform: str, declaration: SourceDeclaration, source_url: str) -> dict:
    declaration = SourceDeclaration.model_validate(declaration.model_dump())
    mapping = STATEMENTS.get(platform, {})
    if declaration.statement not in mapping:
        raise ValueError("Source declaration is not supported for this platform")
    if declaration.origin == "licensed_reuse" and declaration.statement == "self_shot":
        raise ValueError("Authorization to reuse does not mean self-shot content")
    fields = {"declaration": mapping[declaration.statement]}
    if platform == "bilibili":
        if declaration.bilibili_type is None:
            raise ValueError("Bilibili self-produced/repost choice requires confirmation")
        if declaration.bilibili_type == "self_produced" and declaration.origin != "self_created":
            raise ValueError("Licensed reuse cannot be automatically declared self-produced")
        fields["createType"] = 1 if declaration.bilibili_type == "self_produced" else 2
        if fields["createType"] == 2:
            parse_youtube_url(source_url)
            fields["contentSourceUrl"] = source_url
    elif declaration.bilibili_type is not None:
        raise ValueError("Bilibili creation type cannot be applied to another platform")
    return fields


def utc_milliseconds(when: datetime) -> int:
    if when.utcoffset() is None or when.microsecond % 1000:
        raise ValueError("Schedule must specify an exact timezone-aware millisecond")
    delta = when.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def _form(
    setting: AccountPublishSettings,
    title: str,
    tags: tuple[str, ...],
    source_url: str,
    scheduled_at: datetime | None,
    now: datetime,
) -> dict:
    platform = setting.account.platform
    title_max, desc_max, desc_min, tags_max, tag_max = LIMITS[platform]
    description = title if setting.description is None else setting.description
    if not title.strip() or len(title) > title_max:
        raise ValueError(f"{platform}: title exceeds the supported length")
    if not desc_min <= len(description) <= desc_max:
        raise ValueError(f"{platform}: description exceeds the supported length")
    if not 1 <= len(tags) <= tags_max or any(not t or tag_max and len(t) > tag_max for t in tags):
        raise ValueError(f"{platform}: topic tags exceed the supported count or length")
    # The provider rewrites #tags in descriptions. Reject rather than silently mutate text.
    if "#" in description or "<topic" in description.casefold():
        raise ValueError(
            "Description topic markup requires explicit review; use the topic group field"
        )
    form = {"formType": "task", "title": title, "description": description, "tags": list(tags)}
    form.update(declaration_fields(platform, setting.declaration, source_url))
    if platform in {"douyin", "toutiao"}:
        form["visibleType"] = 0
    if platform in {"bilibili", "toutiao"}:
        form["pubType"] = 1
    if scheduled_at is not None:
        form["scheduledTime"] = utc_milliseconds(scheduled_at)
    if platform == "bilibili":
        evidence = setting.category
        if (
            evidence is None
            or evidence.account_id != setting.account.account_id
            or evidence.path[-1]["yixiaoerName"] != "生活经验"
            or not -timedelta(minutes=5) <= now - evidence.queried_at <= timedelta(days=1)
        ):
            raise ValueError(
                "Bilibili requires a fresh account-bound life-experience category query"
            )
        form["category"] = deepcopy(list(evidence.path))
    elif setting.category is not None:
        raise ValueError("Bilibili category cannot be sent to another platform")
    return form


def build_publish_snapshot(
    job: JobSnapshot,
    output: OutputManifest,
    account_config: PublicationConfig | dict,
    *,
    store: JobStore,
    assets: AssetStore,
    presets: PresetStore,
    approvals: PreviewApprovalStore,
    now: datetime | None = None,
    require_preview: bool = True,
) -> PublishSnapshot:
    config = PublicationConfig.model_validate(
        account_config.model_dump()
        if isinstance(account_config, PublicationConfig)
        else account_config
    )
    now = now or datetime.now(UTC)
    if now.utcoffset() is None:
        raise ValueError("Snapshot creation needs a timezone-aware clock")
    current = store.get(job.id)
    if current != job or job.content_frozen:
        raise ValueError("Task changed or shared content is already frozen")
    if job.spec.publish_mode == "local" or not job.spec.target_ids:
        raise ValueError(
            "Configure explicit publishing targets and mode before snapshot preparation"
        )
    if set(job.spec.target_ids) != {a.account.account_id for a in config.accounts}:
        raise ValueError("Publish targets do not match this task revision")
    verify_output = require_approved_job_output if require_preview else require_verified_job_output
    verified = verify_output(store, presets, approvals, job)
    if output != verified:
        raise ValueError("Output does not belong to this approved task revision")
    source = SourceManifest.model_validate(job.source_manifest)
    plan = load_job_plan(store, job.id)
    if plan.source_hash != source.source.sha256 or plan.source.path != source.source.local_path:
        raise ValueError("Downloaded source does not match the immutable processing snapshot")
    if parse_youtube_url(job.spec.source_url) != source.video_id:
        raise ValueError("Source URL does not match the downloaded video")
    if (
        not assets.verify(source.thumbnail.id)
        or assets.get(source.thumbnail.id) != source.thumbnail
    ):
        raise ValueError("Original thumbnail is missing or changed")
    with Image.open(source.thumbnail.local_path) as image:
        image.verify()
    topic = select_topic_group(job.spec.title, config.topic_override)
    if topic.status != "selected":
        raise ValueError("A topic group needs manual selection")
    when = job.spec.scheduled_at_utc
    if when is not None and when <= now:
        raise ValueError("Scheduled time has passed; never change it to immediate publishing")
    by_id = {a.account.account_id: a for a in config.accounts}
    targets = []
    for identifier in job.spec.target_ids:
        setting = by_id[identifier]
        if not setting.account.active:
            raise ValueError("Selected account is not active")
        targets.append(
            PlatformTarget(
                account=setting.account,
                declaration=setting.declaration,
                category_evidence=setting.category,
                form=_form(setting, job.spec.title, topic.tags, job.spec.source_url, when, now),
            )
        )
    # Recheck after file hashing so a concurrent content revision cannot sneak through preparation.
    if store.get(job.id) != job:
        raise ValueError("Task changed while preparing publication metadata")
    snapshot = PublishSnapshot(
        job_id=job.id,
        content_revision=job.content_revision,
        prepared_revision=job.revision,
        video_id=source.video_id,
        source_url=job.spec.source_url,
        title=job.spec.title,
        topic=topic,
        output=output,
        cover=source.thumbnail,
        mode=job.spec.publish_mode,
        scheduled_at_utc=when,
        targets=tuple(targets),
        created_at=now,
        digest="",
    )
    return snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
