"""Publisher protocol and receipt types; provider acceptance is not platform success."""

import hashlib
import hmac
import json
from datetime import datetime
from typing import Literal, Protocol

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from fabu.domain import TargetAttempt
from fabu.publishing.capabilities import CapabilityReport
from fabu.publishing.models import PublishSnapshot
from fabu.publishing.permits import ExecutionPermit, PermitAuthority

PLATFORM_NAMES = {"douyin": "抖音", "toutiao": "头条号", "bilibili": "哔哩哔哩"}


class UploadReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    resource: Literal["video", "cover"]
    sha256: str
    key: str
    metadata: dict
    raw: dict = Field(repr=False)


class PreparedSubmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    snapshot: PublishSnapshot
    client_id: str = Field(repr=False)
    resources: tuple[UploadReceipt, ...]
    payloads: dict[str, dict] = Field(repr=False)
    proof: str = Field(repr=False)


class SubmissionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    target_id: str
    state: Literal["PROVIDER_ACCEPTED", "SUBMITTED", "SCHEDULED", "PUBLISHED", "FAILED", "UNKNOWN"]
    provider_task_id: str | None = None
    platform_content_id: str | None = None
    scheduled_at_utc: datetime | None = None
    actual_channel: str | None = None
    reason: str | None = None


class ReconcileResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    target_id: str
    state: Literal["SUBMITTED", "SCHEDULED", "PUBLISHED", "FAILED", "UNKNOWN"]
    provider_task_id: str | None = None
    platform_content_id: str | None = None
    scheduled_at_utc: datetime | None = None
    reason: str | None = None


class UploadJournal(Protocol):
    def get(self, snapshot_digest: str, kind: str) -> UploadReceipt | None: ...
    def begin(self, snapshot_digest: str, kind: str) -> None: ...
    def complete(self, snapshot_digest: str, receipt: UploadReceipt) -> None: ...


class MemoryUploadJournal:
    durable = False

    def __init__(self):
        self.rows = {}

    def get(self, digest, kind):
        key = (digest, kind)
        if key in self.rows and self.rows[key] is None:
            raise ValueError("Resource upload outcome is unknown; reconcile before retrying")
        return self.rows.get(key)

    def begin(self, digest, kind):
        if (digest, kind) in self.rows:
            raise ValueError("Resource upload already attempted")
        self.rows[(digest, kind)] = None

    def complete(self, digest, receipt):
        self.rows[(digest, receipt.resource)] = receipt


class Publisher(Protocol):
    def preflight(self, snapshot: PublishSnapshot) -> CapabilityReport: ...
    def prepare(self, snapshot: PublishSnapshot, permit: ExecutionPermit) -> PreparedSubmission: ...
    def submit(
        self, prepared: PreparedSubmission, permit: ExecutionPermit, target_id: str
    ) -> SubmissionReceipt: ...
    def reconcile(self, attempt: TargetAttempt) -> ReconcileResult: ...


def resource_metadata(snapshot: PublishSnapshot, kind: str) -> dict:
    if kind == "video":
        info = snapshot.output.media_info
        return {
            "size": snapshot.output.size_bytes,
            "width": info.coded_width,
            "height": info.coded_height,
            "duration": float(info.duration),
            "format": "mp4",
        }
    with Image.open(snapshot.cover.local_path) as image:
        return {"size": snapshot.cover.size_bytes, "width": image.width, "height": image.height}


def assemble_prepared(
    snapshot: PublishSnapshot,
    resources: tuple[UploadReceipt, ...],
    client_id: str,
    authority: PermitAuthority,
) -> PreparedSubmission:
    by_kind = {r.resource: r for r in resources}
    if set(by_kind) != {"video", "cover"} or len(resources) != 2:
        raise ValueError("Both verified uploaded resources are required")
    video, cover = by_kind["video"], by_kind["cover"]
    if video.sha256 != snapshot.output.sha256 or cover.sha256 != snapshot.cover.sha256:
        raise ValueError("Resource receipt does not match frozen content")
    payloads = {}
    for target in snapshot.targets:
        v = {**video.metadata, "key": video.key}
        c = {**cover.metadata, "key": cover.key}
        form = json.loads(json.dumps(target.form, ensure_ascii=False))
        if target.account.platform == "douyin":
            form["horizontalCover"] = c
        payloads[target.account.account_id] = {
            "action": "publish",
            "publishType": "video",
            "platforms": [PLATFORM_NAMES[target.account.platform]],
            "coverKey": cover.key,
            "publishChannel": "local",
            "clientId": client_id,
            "isDraft": False,
            "publishArgs": {
                "accountForms": [
                    {
                        "platformAccountId": target.account.account_id,
                        "video": v,
                        "cover": c,
                        "coverKey": cover.key,
                        "contentPublishForm": form,
                    }
                ]
            },
        }
    prepared = PreparedSubmission(
        snapshot=snapshot, client_id=client_id, resources=resources, payloads=payloads, proof=""
    )
    return prepared.model_copy(
        update={"proof": authority.sign("prepared", prepared_digest(prepared))}
    )


def prepared_digest(prepared: PreparedSubmission) -> str:
    return hashlib.sha256(
        json.dumps(
            prepared.model_dump(mode="json", exclude={"proof"}),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def verify_prepared(prepared: PreparedSubmission, authority: PermitAuthority):
    if not hmac.compare_digest(
        prepared.proof, authority.sign("prepared", prepared_digest(prepared))
    ):
        raise ValueError("Prepared submission was modified or belongs to another execution session")
