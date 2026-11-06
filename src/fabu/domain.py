"""Persistent domain models. State revision and immutable content revision are distinct."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

JobState = Literal[
    "NEW",
    "INSPECTING",
    "DOWNLOADING",
    "PROCESSING",
    "READY",
    "PUBLISHING",
    "PAUSED",
    "FAILED",
    "CANCELED",
]
TargetState = Literal[
    "PENDING",
    "PRECHECKED",
    "SUBMITTING",
    "PROVIDER_ACCEPTED",
    "SUBMITTED",
    "SCHEDULED",
    "PUBLISHED",
    "FAILED",
    "UNKNOWN",
]


class JobSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_url: str = Field(min_length=1, max_length=4096)
    title: str = Field(min_length=1, max_length=1000)
    preset_version_id: str = Field(min_length=1, max_length=100)
    target_ids: list[str] = Field(default_factory=list, max_length=3)
    publish_mode: Literal["local", "simulation", "immediate", "native_schedule"] = "local"
    scheduled_at_utc: datetime | None = None

    @model_validator(mode="after")
    def validate_time_and_targets(self):
        if len(set(self.target_ids)) != len(self.target_ids):
            raise ValueError("Target IDs must be unique")
        if not self.title.strip():
            raise ValueError("Title cannot be blank")
        if self.publish_mode == "native_schedule":
            if self.scheduled_at_utc is None or self.scheduled_at_utc.utcoffset() is None:
                raise ValueError("A timezone-aware scheduled time is required")
            object.__setattr__(self, "scheduled_at_utc", self.scheduled_at_utc.astimezone(UTC))
        elif self.scheduled_at_utc is not None:
            raise ValueError("Only native_schedule accepts a scheduled time")
        return self


class JobSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    revision: int
    content_revision: int
    spec: JobSpec
    source_manifest: dict | None = None
    plan_digest: str | None = None
    output_manifest: dict | None = None
    state: JobState
    pause_reason: str | None = None
    content_frozen: bool = False
    owner_worker_id: str | None = None


class TargetAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(default_factory=lambda: str(uuid4()))
    job_id: str
    revision: int = Field(ge=1)  # content revision, never the CAS/state revision
    target_id: str
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_task_id: str | None = None
    state: TargetState
