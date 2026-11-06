"""Native scheduling requires platform-owned records, not elapsed time or provider queues."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from fabu.publishing.gateway import ReconcileResult


class ScheduleIssue(BaseModel):
    target_id: str
    code: str
    message: str


def validate_schedule(targets, when_utc, capabilities, now):
    def issue(code, message):
        return [ScheduleIssue(target_id="all", code=code, message=message)]

    if when_utc.utcoffset() is None or now.utcoffset() is None:
        return issue("timezone_required", "预约时间必须明确时区，界面统一使用北京时间")
    if when_utc <= now:
        return issue("schedule_expired", "预约时间已过期，不会转为立即发布")
    if when_utc.microsecond % 1000:
        return issue("timestamp_precision", "平台时间精度必须为毫秒")
    if not targets or len(set(targets)) != len(targets):
        return issue("targets_invalid", "请选择明确且不重复的目标账号")
    checked = capabilities.checked_at
    if checked.utcoffset() is None or not -timedelta(seconds=5) <= now - checked <= timedelta(
        minutes=5
    ):
        return issue("preflight_expired", "平台预约窗口查询已过期，重新预检")
    issues = []
    for target in targets:
        window = capabilities.schedule_windows.get(target)
        if window is None:
            issues.append(
                ScheduleIssue(
                    target_id=target, code="window_unverified", message="平台预约窗口未经核对"
                )
            )
        elif (
            not max(now + timedelta(seconds=window.minimum_lead_seconds), window.earliest_utc)
            < when_utc
            <= window.latest_utc
        ):
            issues.append(
                ScheduleIssue(
                    target_id=target,
                    code="outside_platform_window",
                    message="统一预约时间不在此平台允许的范围内",
                )
            )
    return issues


class NativeScheduleObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    target_id: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    provider_task_id: str = Field(min_length=1)
    platform_content_id: str = Field(min_length=1)
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    video_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cover_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scheduled_at_utc: datetime
    observed_at: datetime
    execution_owner: Literal["platform", "provider_queue", "unknown"]
    status: Literal["SCHEDULED", "PUBLISHED", "FAILED"]
    published_at_utc: datetime | None = None
    client_independence_verified: StrictBool = False

    @model_validator(mode="after")
    def timezone(self):
        if any(
            t is not None and t.utcoffset() is None
            for t in [self.scheduled_at_utc, self.observed_at, self.published_at_utc]
        ):
            raise ValueError("Platform schedule evidence needs explicit timezones")
        return self


def _latest(receipt, evidence, expected, now):
    if expected.utcoffset() is None or now.utcoffset() is None or not receipt.provider_task_id:
        return None
    try:
        observations = [NativeScheduleObservation.model_validate(v) for v in evidence]
    except ValueError:
        return None
    matching = []
    for value in observations:
        if any(
            getattr(value, field) != getattr(receipt, field)
            for field in [
                "target_id",
                "platform",
                "provider_task_id",
                "snapshot_digest",
                "video_sha256",
                "cover_sha256",
            ]
        ):
            return None
        if (
            value.scheduled_at_utc != expected
            or value.execution_owner != "platform"
            or value.observed_at > now
            or receipt.actual_channel != "local"
            or receipt.platform_content_id is not None
            and receipt.platform_content_id != value.platform_content_id
        ):
            return None
        if value.status == "PUBLISHED" and (
            value.published_at_utc is None
            or not expected <= value.published_at_utc <= value.observed_at
        ):
            return None
        matching.append(value)
    if not matching:
        return None
    latest = max(v.observed_at for v in matching)
    finalists = [v for v in matching if v.observed_at == latest]
    if any(v != finalists[0] for v in finalists):
        return None
    return finalists[0]


def verify_native_schedule(receipt, evidence, expected_utc, *, now=None):
    now = now or datetime.now(UTC)
    value = _latest(receipt, evidence, expected_utc, now)
    if value is None:
        return ReconcileResult(
            target_id=receipt.target_id,
            state="UNKNOWN",
            provider_task_id=receipt.provider_task_id,
            reason="native_platform_evidence_unverified",
        )
    return ReconcileResult(
        target_id=value.target_id,
        state=value.status,
        provider_task_id=value.provider_task_id,
        platform_content_id=value.platform_content_id,
        scheduled_at_utc=value.scheduled_at_utc,
        snapshot_digest=value.snapshot_digest,
        video_sha256=value.video_sha256,
        cover_sha256=value.cover_sha256,
        platform=value.platform,
        evidence_origin="verified_platform",
        definitely_not_submitted=False,
    )


def can_exit_client(receipt, evidence, expected_utc, *, now=None):
    value = _latest(receipt, evidence, expected_utc, now or datetime.now(UTC))
    return bool(
        value and value.status in {"SCHEDULED", "PUBLISHED"} and value.client_independence_verified
    )
