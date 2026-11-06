"""Scope-bound controlled-test evidence. No network and no HTTP evidence/grant endpoint.

Recording is a trusted operator/adapter operation after inspecting original provider evidence.
Typed facts and hashes are not an automatic source of truth: invented facts are not verification.
All durable evidence is DPAPI protected; execution gates and grants expire with this process.
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import uuid4

import pywintypes
import win32crypt
from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter, model_validator

from fabu.publishing.capabilities import CapabilityReport, ScheduleWindow, can_enable_mode
from fabu.publishing.gateway import SubmissionReceipt
from fabu.publishing.journal import SqliteUploadJournal
from fabu.publishing.models import CLI_CONTRACT_VERSION
from fabu.publishing.reconcile import checked_receipt
from fabu.publishing.schedule import (
    NativeScheduleObservation,
    validate_schedule,
    verify_native_schedule,
)

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def metadata_digest(target):
    return hashlib.sha256(_json(target.form).encode("utf-8")).hexdigest()


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    observed_at: datetime
    artifact_sha256: Digest

    @model_validator(mode="after")
    def timezone(self):
        for field in ("observed_at", "expires_at", "resources_valid_until", "reset_at"):
            when = getattr(self, field, None)
            if when is not None and when.utcoffset() is None:
                raise ValueError("Evidence timestamps require an explicit timezone")
        return self


class SafetyEvidence(Evidence):
    kind: Literal["safety"]
    expires_at: datetime
    cli_sha256: Digest
    client_sha256: Digest
    target_ids: tuple[str, ...]
    free_units_remaining: Count
    max_upload_debit: Count
    max_target_debits: dict[str, Count]
    max_cost_cents: Count
    automatic_purchase: StrictBool
    failed_attempts_included: StrictBool
    max_video_bytes: Count
    max_cover_bytes: Count
    free_storage_bytes: Count
    resources_valid_until: datetime
    reset_at: datetime
    route: Literal["local"]
    submission_strategy: Literal["per_target"]


class UploadEvidence(Evidence):
    kind: Literal["upload"]
    resource: Literal["video", "cover"]
    sha256: Digest
    size_bytes: Count
    provider_resource_sha256: Digest
    expires_at: datetime
    unchanged: StrictBool
    reuse_verified: StrictBool


class TargetEvidence(Evidence):
    kind: Literal["target"]
    metadata_digest: Digest
    receipt: SubmissionReceipt
    native_schedule: NativeScheduleObservation | None = None


class QuotaEvidence(Evidence):
    kind: Literal["quota"]
    operation: str = Field(min_length=1, max_length=160)
    free_before: Count
    free_after: Count
    paid_debit_cents: Count
    counter_epoch: str = Field(min_length=1, max_length=200)
    isolated: StrictBool


class WindowEvidence(Evidence):
    kind: Literal["window"]
    target_id: str = Field(min_length=1, max_length=128)
    earliest_utc: datetime
    latest_utc: datetime
    minimum_lead_seconds: Count
    expires_at: datetime
    platform_native: StrictBool

    @model_validator(mode="after")
    def bounds(self):
        ScheduleWindow(
            earliest_utc=self.earliest_utc,
            latest_utc=self.latest_utc,
            minimum_lead_seconds=self.minimum_lead_seconds,
        )
        return self


Observation = Annotated[
    SafetyEvidence | UploadEvidence | TargetEvidence | QuotaEvidence | WindowEvidence,
    Field(discriminator="kind"),
]
_observation = TypeAdapter(Observation)


class ControlledGate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    permit_id: str
    snapshot_digest: Digest
    evidence_id: str
    expires_at: datetime
    cli_sha256: Digest
    client_sha256: Digest
    signature: str = Field(repr=False)


class VerificationService:
    def __init__(self, store, authority):
        self.store, self.authority = store, authority
        self._grants = {}

    def _integrity(self, permit, snapshot):
        self.authority.verify_integrity(permit, snapshot)
        if permit.kind != "controlled_test":
            raise ValueError("Only explicitly scoped controlled tests can supply verification")

    def record(self, permit, snapshot, evidence):
        self._integrity(permit, snapshot)
        value = _observation.validate_python(evidence)
        now = self.authority.clock()
        if not permit.issued_at <= value.observed_at <= now + timedelta(seconds=5):
            raise ValueError("Evidence timestamp is outside the authorized observation interval")
        if (
            value.kind in {"safety", "upload", "quota", "window"}
            and value.observed_at > permit.expires_at
        ):
            raise ValueError("Write evidence is outside the execution permit")
        identifier = str(uuid4())
        envelope = {
            "id": identifier,
            "snapshot_digest": snapshot.digest,
            "permit_id": permit.id,
            "evidence": value.model_dump(mode="json"),
        }
        cipher = win32crypt.CryptProtectData(
            _json(envelope).encode("utf-8"), "Fabu verification", None, None, None, 1
        )
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO verification_evidence VALUES (?,?,?,?,?,?)",
                (identifier, snapshot.digest, permit.id, value.kind, cipher, now.isoformat()),
            )
        return identifier

    def _read(self, permit, snapshot, identifier):
        self._integrity(permit, snapshot)
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM verification_evidence WHERE id=?", (identifier,)
            ).fetchone()
        if (
            row is None
            or row["snapshot_digest"] != snapshot.digest
            or row["permit_id"] != permit.id
        ):
            raise ValueError("Evidence is missing or belongs to another authorization")
        try:
            _, plain = win32crypt.CryptUnprotectData(
                bytes(row["evidence_cipher"]), None, None, None, 1
            )
            envelope = json.loads(plain)
            if (
                envelope["id"] != identifier
                or envelope["snapshot_digest"] != snapshot.digest
                or envelope["permit_id"] != permit.id
            ):
                raise ValueError("Evidence envelope identity mismatch")
            return _observation.validate_python(envelope["evidence"])
        except (pywintypes.error, KeyError, ValueError, TypeError) as exc:
            raise ValueError("Stored verification evidence could not be validated") from exc

    def _safety(self, permit, snapshot, value, *, historical=False):
        if not isinstance(value, SafetyEvidence):
            raise ValueError("A reviewed free-cost boundary is required")
        now = value.observed_at if historical else self.authority.clock()
        targets = tuple(t.account.account_id for t in snapshot.targets)
        worst = (
            permit.max_uploads * value.max_upload_debit
            + sum(value.max_target_debits.values()) * permit.max_submissions_per_target
        )
        if (
            value.target_ids != targets
            or set(value.max_target_debits) != set(targets)
            or value.max_cost_cents != 0
            or value.automatic_purchase
            or not value.failed_attempts_included
            or value.free_units_remaining < worst
            or not value.observed_at <= now < min(value.expires_at, value.reset_at)
            or value.expires_at > permit.expires_at
            or value.resources_valid_until
            <= max(permit.expires_at, snapshot.scheduled_at_utc or permit.expires_at)
            or value.max_video_bytes < snapshot.output.size_bytes
            or value.max_cover_bytes < snapshot.cover.size_bytes
            or value.free_storage_bytes < snapshot.output.size_bytes + snapshot.cover.size_bytes
        ):
            raise ValueError("Free quota/storage/expiry cannot cover this exact bounded test")
        return value

    def controlled_gate(self, permit, snapshot, evidence_id):
        self.authority.validate(permit, snapshot, real=True)
        value = self._safety(permit, snapshot, self._read(permit, snapshot, evidence_id))
        gate = ControlledGate(
            permit_id=permit.id,
            snapshot_digest=snapshot.digest,
            evidence_id=evidence_id,
            expires_at=value.expires_at,
            cli_sha256=value.cli_sha256,
            client_sha256=value.client_sha256,
            signature="",
        )
        return gate.model_copy(
            update={
                "signature": self.authority.sign(
                    "controlled-gate", _json(gate.model_dump(mode="json", exclude={"signature"}))
                )
            }
        )

    def allow_controlled(self, gate, permit, snapshot, report):
        try:
            self.authority.validate(permit, snapshot, real=True)
            expected = self.controlled_gate(permit, snapshot, gate.evidence_id)
            if gate != expected or not hmac.compare_digest(gate.signature, expected.signature):
                return False
            now = self.authority.clock()
            required = (
                report.account_status,
                report.source_declarations,
                report.categories,
                report.quota,
                report.temporary_storage,
                report.file_limits,
            )
            mode = "native_schedule" if snapshot.mode == "native_schedule" else "immediate"
            if snapshot.mode == "native_schedule" and validate_schedule(
                list(permit.target_ids), snapshot.scheduled_at_utc, report, now
            ):
                return False
            return (
                all(v == "SUPPORTED" for v in required)
                and not report.payment_required
                and report.tool_version == CLI_CONTRACT_VERSION
                and report.submission_strategy == "per_target"
                and report.checked_at.utcoffset() is not None
                and -timedelta(seconds=5) <= now - report.checked_at <= timedelta(minutes=5)
                and all(
                    v != "UNSUPPORTED" for v in (report.upload, report.hevc, getattr(report, mode))
                )
            )
        except (ValueError, KeyError, AttributeError):
            return False

    def prepare_controlled_report(self, gate, permit, snapshot, base, *, cli_sha256, client_sha256):
        expected = self.controlled_gate(permit, snapshot, gate.evidence_id)
        if gate != expected or gate.cli_sha256 != cli_sha256 or gate.client_sha256 != client_sha256:
            raise ValueError("Controlled test binary/client identity changed")
        self._safety(permit, snapshot, self._read(permit, snapshot, gate.evidence_id))
        if (
            base.account_status != "SUPPORTED"
            or base.payment_required
            or base.tool_version != CLI_CONTRACT_VERSION
        ):
            raise ValueError("Current official account/version preflight failed")
        now = self.authority.clock()
        for target in snapshot.targets:
            if target.account.platform == "bilibili":
                category = target.category_evidence
                if category is None or not -timedelta(
                    minutes=5
                ) <= now - category.queried_at <= timedelta(days=1):
                    raise ValueError("Fresh account-bound category evidence required")
        windows = {}
        if snapshot.mode == "native_schedule":
            with self.store.connection() as db:
                identifiers = [
                    r[0]
                    for r in db.execute(
                        "SELECT id FROM verification_evidence WHERE snapshot_digest=? "
                        "AND permit_id=? AND kind='window'",
                        (snapshot.digest, permit.id),
                    ).fetchall()
                ]
            evidence = [self._read(permit, snapshot, i) for i in identifiers]
            for target in permit.target_ids:
                matching = [v for v in evidence if v.target_id == target]
                if not matching:
                    continue
                matching.sort(key=lambda v: v.observed_at, reverse=True)
                v = matching[0]
                if (
                    len(matching) > 1
                    and matching[1].observed_at == v.observed_at
                    or not v.platform_native
                    or not v.observed_at <= now < v.expires_at
                    or now - v.observed_at > timedelta(minutes=5)
                ):
                    continue
                windows[target] = ScheduleWindow(
                    earliest_utc=v.earliest_utc,
                    latest_utc=v.latest_utc,
                    minimum_lead_seconds=v.minimum_lead_seconds,
                )
        return base.model_copy(
            update={
                "schedule_windows": windows,
                "quota": "SUPPORTED",
                "temporary_storage": "SUPPORTED",
                "file_limits": "SUPPORTED",
                "source_declarations": "SUPPORTED",
                "categories": "SUPPORTED",
                "submission_strategy": "per_target",
                "evidence_refs": [*base.evidence_refs, gate.evidence_id],
            }
        )

    def resource_reusable(self, gate, permit, snapshot, receipt):
        try:
            if gate != self.controlled_gate(permit, snapshot, gate.evidence_id):
                return False
            with self.store.connection() as db:
                identifiers = [
                    r[0]
                    for r in db.execute(
                        "SELECT id FROM verification_evidence WHERE snapshot_digest=? "
                        "AND permit_id=? AND kind='upload'",
                        (snapshot.digest, permit.id),
                    ).fetchall()
                ]
            now = self.authority.clock()
            for identifier in identifiers:
                value = self._read(permit, snapshot, identifier)
                if (
                    value.resource == receipt.resource
                    and value.sha256 == receipt.sha256
                    and value.provider_resource_sha256
                    == hashlib.sha256(receipt.key.encode()).hexdigest()
                    and value.unchanged
                    and value.reuse_verified
                    and value.expires_at > max(now, snapshot.scheduled_at_utc or now)
                ):
                    return True
        except (ValueError, KeyError):
            pass
        return False

    def _durable_matches(self, snapshot, uploads, targets):
        try:
            journal = SqliteUploadJournal(self.store, snapshot, scope="live")
            for kind, value in uploads.items():
                receipt = journal.get(snapshot.digest, kind)
                if (
                    receipt is None
                    or hashlib.sha256(receipt.key.encode()).hexdigest()
                    != value.provider_resource_sha256
                ):
                    return False
            with self.store.connection() as db:
                rows = db.execute(
                    "SELECT a.*,l.receipt_json FROM target_attempts a "
                    "JOIN publication_attempt_links l ON a.id=l.attempt_id "
                    "JOIN publication_runs r ON r.id=l.run_id "
                    "WHERE r.scope='live' AND r.snapshot_digest=?",
                    (snapshot.digest,),
                ).fetchall()
            for target_id, value in targets.items():
                matches = [r for r in rows if r["target_id"] == target_id]
                if len(matches) != 1 or matches[0]["state"] not in {
                    "SUBMITTED",
                    "SCHEDULED",
                    "PUBLISHED",
                }:
                    return False
                row = matches[0]
                if (
                    row["provider_task_id"] != value.receipt.provider_task_id
                    or not row["receipt_json"]
                ):
                    return False
                saved = SubmissionReceipt.model_validate_json(row["receipt_json"])
                if saved.platform_content_id != value.receipt.platform_content_id:
                    return False
            return len(uploads) == 2 and len(targets) == len(snapshot.targets)
        except (ValueError, KeyError):
            return False

    def verify_controlled_run(self, permit, snapshot, evidence, *, require_durable=True):
        if not require_durable:
            raise ValueError("Durable evidence cannot be bypassed")
        self._integrity(permit, snapshot)
        if len(set(evidence)) != len(evidence):
            raise ValueError("Duplicate evidence identifiers")
        values = [self._read(permit, snapshot, i) for i in evidence]
        now = self.authority.clock()
        safety = [v for v in values if v.kind == "safety"]
        facts = {
            "upload": "UNKNOWN",
            "quota": "UNKNOWN",
            "file_limits": "UNKNOWN",
            "hevc": "UNKNOWN",
            "temporary_storage": "UNKNOWN",
            "categories": "UNKNOWN",
            "source_declarations": "UNKNOWN",
            "account_status": "UNKNOWN",
        }
        mode = "native_schedule" if snapshot.mode == "native_schedule" else "immediate"
        blockers = []
        boundary = None
        try:
            if len(safety) != 1:
                raise ValueError("No unique safety evidence")
            boundary = self._safety(permit, snapshot, safety[0], historical=True)
        except ValueError:
            blockers.append("cost_boundary_unverified")
        uploads = {}
        upload_values = [v for v in values if v.kind == "upload"]
        for v in upload_values:
            item = snapshot.output if v.resource == "video" else snapshot.cover
            if (
                v.sha256 == item.sha256
                and v.size_bytes == item.size_bytes
                and v.unchanged
                and v.expires_at > max(now, snapshot.scheduled_at_utc or now)
            ):
                uploads[v.resource] = v
        if len(upload_values) == 2 and set(uploads) == {"video", "cover"}:
            facts["upload"] = "SUPPORTED"
            facts["temporary_storage"] = (
                "SUPPORTED" if all(v.reuse_verified for v in uploads.values()) else "UNKNOWN"
            )
        else:
            blockers.append("upload_evidence_incomplete")
        targets = {}
        target_values = [v for v in values if v.kind == "target"]
        for v in target_values:
            target = next(
                (t for t in snapshot.targets if t.account.account_id == v.receipt.target_id), None
            )
            if target is None:
                continue
            checked = checked_receipt(snapshot, "live", target.account.account_id, v.receipt)
            if snapshot.mode == "native_schedule":
                if v.native_schedule is None:
                    continue
                native = verify_native_schedule(
                    v.receipt, [v.native_schedule], snapshot.scheduled_at_utc, now=now
                )
                if (
                    native.state != "PUBLISHED"
                    or not v.native_schedule.client_independence_verified
                ):
                    continue
            if (
                checked.state == "PUBLISHED"
                and checked.actual_channel == "local"
                and v.metadata_digest == metadata_digest(target)
            ):
                targets[target.account.account_id] = v
        all_targets = len(target_values) == len(snapshot.targets) and set(targets) == set(
            permit.target_ids
        )
        if all_targets:
            for field in (
                "account_status",
                "file_limits",
                "hevc",
                "categories",
                "source_declarations",
            ):
                facts[field] = "SUPPORTED"
        else:
            blockers.append("platform_final_evidence_incomplete")
        quota = [v for v in values if v.kind == "quota"]
        expected_operations = ["upload:video", "upload:cover"] + [
            "submit:" + t for t in permit.target_ids
        ]
        if (
            boundary
            and len(quota) == len(expected_operations)
            and [v.operation for v in quota] == expected_operations
        ):
            remaining = boundary.free_units_remaining
            epoch = quota[0].counter_epoch
            good = True
            for v in quota:
                maximum = (
                    boundary.max_upload_debit
                    if v.operation.startswith("upload:")
                    else boundary.max_target_debits[v.operation.removeprefix("submit:")]
                )
                good &= (
                    v.isolated
                    and v.counter_epoch == epoch
                    and v.free_before == remaining
                    and 0 <= v.free_before - v.free_after <= maximum
                    and v.paid_debit_cents == 0
                )
                remaining = v.free_after
            if good:
                facts["quota"] = "SUPPORTED"
        if facts["quota"] != "SUPPORTED":
            blockers.append("quota_accounting_unverified")
        durable = self._durable_matches(snapshot, uploads, targets)
        if not durable:
            blockers.append("durable_attempts_unverified")
        within_permit = (
            len(upload_values) <= permit.max_uploads
            and permit.max_submissions_per_target == 1
            and permit.allow_temporary_storage_upload
        )
        if not within_permit:
            blockers.append("observed_operations_exceed_permit")
        complete = (
            within_permit
            and durable
            and boundary is not None
            and all(v == "SUPPORTED" for v in facts.values())
        )
        result = CapabilityReport(
            checked_at=now,
            tool_version=CLI_CONTRACT_VERSION,
            submission_strategy="per_target" if boundary else "UNKNOWN",
            evidence_refs=list(evidence),
            blockers=blockers,
            mode_evidence={t: [mode] for t in permit.target_ids} if complete else {},
            **facts,
            **{mode: "SUPPORTED" if complete else "UNKNOWN"},
        )
        if complete:
            digest = hashlib.sha256(result.model_dump_json().encode()).hexdigest()
            self._grants[digest] = (
                tuple(permit.target_ids),
                mode,
                min(boundary.expires_at, now + timedelta(minutes=5)),
            )
        return result

    def grant_production_mode(self, report, targets, mode):
        digest = hashlib.sha256(report.model_dump_json().encode()).hexdigest()
        grant = self._grants.get(digest)
        return bool(
            grant
            and targets
            and len(set(targets)) == len(targets)
            and set(targets) <= set(grant[0])
            and mode == grant[1]
            and self.authority.clock() < grant[2]
            and can_enable_mode(report, mode, targets)
        )
