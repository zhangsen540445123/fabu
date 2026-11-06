"""Evidence-based capability report. Read-only schema observations never unlock publishing."""

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Capability = Literal["UNKNOWN", "SUPPORTED", "UNSUPPORTED"]


class CapabilityReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    account_status: Capability = "UNKNOWN"
    upload: Capability = "UNKNOWN"
    immediate: Capability = "UNKNOWN"
    native_schedule: Capability = "UNKNOWN"
    quota: Capability = "UNKNOWN"
    file_limits: Capability = "UNKNOWN"
    hevc: Capability = "UNKNOWN"
    temporary_storage: Capability = "UNKNOWN"
    categories: Capability = "UNKNOWN"
    source_declarations: Capability = "UNKNOWN"
    checked_at: datetime
    tool_version: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    payment_required: bool = False
    accounts: list[dict] = Field(default_factory=list)
    # Only the later scope-bound verification service may populate this, not a request payload.
    mode_evidence: dict[str, list[str]] = Field(default_factory=dict)


def inspect_capabilities(query: Callable[[str], dict], now: datetime) -> CapabilityReport:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Capability timestamp requires a timezone")
    observations = {}
    blockers = []
    evidence = []
    operations = (
        "version",
        "accounts",
        "schema:douyin",
        "schema:toutiao",
        "schema:bilibili",
        "entitlements",
    )
    for operation in operations:
        try:
            observations[operation] = query(operation)
            if observations[operation].get("unavailable"):
                blockers.append(f"unverified:{operation}")
            else:
                evidence.append(f"read-only:{operation}:{now.isoformat()}")
        except Exception:
            # Remote error bodies are untrusted and can contain credentials or session URLs.
            blockers.append(f"query_failed:{operation}")
            observations[operation] = {}
    accounts = observations.get("accounts", {}).get("accounts", [])
    account_status = "UNKNOWN"
    if accounts:
        account_status = (
            "SUPPORTED" if all(a.get("active") is True for a in accounts) else "UNSUPPORTED"
        )
    entitlements = observations.get("entitlements", {})
    paid = (
        entitlements.get("requires_payment") is True
        and entitlements.get("source") == "verified_cli_entitlement"
    )
    if paid:
        blockers.append("paid_gate")
    blockers.append("live_upload_publish_schedule_not_verified")
    return CapabilityReport(
        account_status=account_status,
        checked_at=now,
        tool_version=observations.get("version", {}).get("version"),
        accounts=accounts,
        evidence_refs=evidence,
        blockers=blockers,
        payment_required=paid,
    )


def can_enable_mode(report: CapabilityReport, mode: str, target_ids: list[str]) -> bool:
    if mode not in {"immediate", "native_schedule"} or not target_ids or report.payment_required:
        return False
    required = (
        report.account_status,
        report.upload,
        report.quota,
        report.file_limits,
        report.hevc,
        report.source_declarations,
        getattr(report, mode),
    )
    return all(value == "SUPPORTED" for value in required) and all(
        mode in report.mode_evidence.get(target, []) for target in target_ids
    )
