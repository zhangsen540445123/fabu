"""Scope-bound, signed in-process permits. HTTP request models must never mint these implicitly."""

import hashlib
import hmac
import json
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from fabu.publishing.models import PublishSnapshot, verify_publish_snapshot


class ExecutionPermit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    snapshot_digest: str
    kind: Literal["simulation", "controlled_test", "verified"]
    target_ids: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    max_uploads: int = Field(ge=0, le=2)
    max_submissions_per_target: int = Field(ge=0, le=1)
    allow_temporary_storage_upload: StrictBool = False
    authorization_note: str = Field(min_length=1, max_length=1000)
    zero_new_payment: Literal[True] = True
    signature: str = Field(repr=False)


class PermitAuthority:
    def __init__(self, clock: Callable[[], datetime] | None = None):
        self.clock = clock or (lambda: datetime.now(UTC))
        self._secret = secrets.token_bytes(32)
        self._counts = {}
        self._lock = threading.Lock()

    def sign(self, purpose: str, data: str) -> str:
        return hmac.new(
            self._secret, (purpose + "\0" + data).encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _signature(self, permit):
        text = json.dumps(
            permit.model_dump(mode="json", exclude={"signature"}),
            sort_keys=True,
            separators=(",", ":"),
        )
        return self.sign("execution-permit", text)

    def issue(
        self,
        snapshot: PublishSnapshot,
        *,
        kind: str,
        expires_at: datetime,
        max_uploads: int,
        max_submissions_per_target: int,
        authorization_note: str,
        allow_temporary_storage_upload: bool = False,
    ) -> ExecutionPermit:
        # Trusted coordinator only: simulation, bounded human test scope, or verified user job.
        verify_publish_snapshot(snapshot)
        now = self.clock()
        if expires_at.utcoffset() is None or not now < expires_at <= now + timedelta(days=1):
            raise ValueError("Permit must have a future expiry within one day")
        if kind != "simulation" and snapshot.mode == "simulation":
            raise ValueError("Simulation and real execution permits cannot be interchanged")
        if kind != "simulation" and max_uploads and not allow_temporary_storage_upload:
            raise ValueError("Temporary storage upload needs explicit authorization")
        value = ExecutionPermit(
            id=str(uuid4()),
            snapshot_digest=snapshot.digest,
            kind=kind,
            target_ids=tuple(t.account.account_id for t in snapshot.targets),
            issued_at=now,
            expires_at=expires_at,
            max_uploads=max_uploads,
            max_submissions_per_target=max_submissions_per_target,
            authorization_note=authorization_note,
            allow_temporary_storage_upload=allow_temporary_storage_upload,
            signature="",
        )
        return value.model_copy(update={"signature": self._signature(value)})

    def validate(
        self, permit: ExecutionPermit | None, snapshot: PublishSnapshot, *, real: bool | None = None
    ):
        if permit is None:
            raise ValueError("No execution permit")
        verify_publish_snapshot(snapshot)
        if (
            not hmac.compare_digest(permit.signature, self._signature(permit))
            or permit.snapshot_digest != snapshot.digest
            or permit.target_ids != tuple(t.account.account_id for t in snapshot.targets)
            or not permit.issued_at <= self.clock() < permit.expires_at
        ):
            raise ValueError("Execution permit is stale, changed or outside scope")
        if real is not None and real == (permit.kind == "simulation"):
            raise ValueError("Permit is for a different execution environment")
        if snapshot.scheduled_at_utc is not None and snapshot.scheduled_at_utc <= self.clock():
            raise ValueError("Scheduled time expired; immediate fallback is prohibited")

    def consume(
        self, permit: ExecutionPermit, snapshot: PublishSnapshot, action: str, target_id: str = ""
    ):
        with self._lock:
            self.validate(permit, snapshot)
            if action == "upload":
                if permit.kind != "simulation" and not permit.allow_temporary_storage_upload:
                    raise ValueError("Resource upload is not authorized")
                key = (permit.id, "upload")
                maximum = permit.max_uploads
            elif action == "submit" and target_id in permit.target_ids:
                key = (permit.id, "submit", target_id)
                maximum = permit.max_submissions_per_target
            else:
                raise ValueError("Operation is outside permit scope")
            if self._counts.get(key, 0) >= maximum:
                raise ValueError("Authorized attempt limit reached")
            # Never refund failures. The coordinator journals BEFORE calling a gateway.
            self._counts[key] = self._counts.get(key, 0) + 1
