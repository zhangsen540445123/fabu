"""Publishing data contracts. None of these models authorize external writes."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from fabu.assets import AssetRef
from fabu.media.models import OutputManifest
from fabu.media.youtube import parse_youtube_url


class TopicSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["selected", "needs_selection"]
    group_name: str | None = None
    tags: tuple[str, ...] = ()
    matches: tuple[str, ...] = ()
    source: Literal["title", "manual"] = "title"
    reason: Literal["no_match", "ambiguous_match"] | None = None


Platform = Literal["douyin", "toutiao", "bilibili"]
CLI_CONTRACT_VERSION = "3.2.32"


class AccountBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    account_id: str = Field(min_length=1, max_length=128)
    platform: Platform
    nickname: str = Field(min_length=1, max_length=200)
    active: StrictBool


class SourceDeclaration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    origin: Literal["self_created", "licensed_reuse"]
    statement: Literal[
        "none",
        "self_shot",
        "offsite",
        "ai",
        "discomfort",
        "fiction",
        "dangerous",
        "entertainment",
        "rational_spending",
        "personal_opinion",
    ]
    bilibili_type: Literal["self_produced", "repost"] | None = None
    confirmed: StrictBool

    @model_validator(mode="after")
    def explicit_confirmation(self):
        if not self.confirmed:
            raise ValueError(
                "Each platform's source declaration requires explicit semantic confirmation"
            )
        return self


class CategoryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    account_id: str
    queried_at: datetime
    path: tuple[dict, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def canonical(self):
        if self.queried_at.utcoffset() is None:
            raise ValueError("Category evidence timestamp must be timezone-aware")
        for node in self.path:
            if (
                not isinstance(node.get("yixiaoerId"), str)
                or not node["yixiaoerId"]
                or not isinstance(node.get("yixiaoerName"), str)
                or not node["yixiaoerName"]
                or not isinstance(node.get("raw"), dict)
            ):
                raise ValueError("Category must retain the complete queried canonical objects")
        return self


class AccountPublishSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    account: AccountBinding
    declaration: SourceDeclaration
    category: CategoryEvidence | None = None
    description: str | None = Field(default=None, max_length=2000)
    visibility: Literal["public"] = "public"


class PublicationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    accounts: tuple[AccountPublishSettings, ...] = Field(min_length=1, max_length=3)
    topic_override: str | None = None
    contract_version: Literal["3.2.32"] = CLI_CONTRACT_VERSION

    @model_validator(mode="after")
    def unique_accounts(self):
        if len({a.account.account_id for a in self.accounts}) != len(self.accounts):
            raise ValueError("Duplicate account ID")
        if len({a.account.platform for a in self.accounts}) != len(self.accounts):
            raise ValueError("Only one explicitly selected account per supported platform")
        return self


class PlatformTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    account: AccountBinding
    declaration: SourceDeclaration
    form: dict
    category_evidence: CategoryEvidence | None = None


class PublishSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    job_id: str
    content_revision: int = Field(ge=1)
    prepared_revision: int = Field(ge=1)
    video_id: str
    source_url: str
    title: str
    topic: TopicSelection
    output: OutputManifest
    cover: AssetRef
    mode: Literal["simulation", "immediate", "native_schedule"]
    scheduled_at_utc: datetime | None
    targets: tuple[PlatformTarget, ...] = Field(min_length=1, max_length=3)
    contract_version: Literal["3.2.32"] = CLI_CONTRACT_VERSION
    created_at: datetime
    digest: str


def publish_snapshot_digest(snapshot: PublishSnapshot) -> str:
    return hashlib.sha256(
        json.dumps(
            snapshot.model_dump(mode="json", exclude={"digest"}),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def verify_publish_snapshot(snapshot: PublishSnapshot) -> None:
    validated = PublishSnapshot.model_validate(snapshot.model_dump())
    if validated.digest != publish_snapshot_digest(validated):
        raise ValueError("Publish snapshot changed after preparation")
    if parse_youtube_url(validated.source_url) != validated.video_id:
        raise ValueError("Snapshot video identity disagrees with source URL")
    when = validated.scheduled_at_utc
    if (validated.mode == "native_schedule") != (when is not None):
        raise ValueError("Snapshot schedule and publish mode disagree")
    timestamp = None
    if when is not None:
        if when.utcoffset() is None or when.microsecond % 1000:
            raise ValueError("Snapshot schedule precision or timezone is invalid")
        delta = when.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
        timestamp = (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
    if validated.topic.status != "selected" or not validated.topic.tags:
        raise ValueError("Snapshot has no selected topic semantics")
    if len({t.account.account_id for t in validated.targets}) != len(validated.targets):
        raise ValueError("Duplicate snapshot target")
    for target in validated.targets:
        if target.form.get("title") != validated.title or target.form.get("tags") != list(
            validated.topic.tags
        ):
            raise ValueError("Platform forms disagree with shared title/topic content")
        if target.form.get("scheduledTime") != timestamp:
            raise ValueError("Platform schedule differs from shared native-schedule time")
        if target.form.get("formType") != "task" or target.form.get("pubType", 1) != 1:
            raise ValueError("Snapshot is not a direct platform-submission form")
