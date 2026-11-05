"""Safety primitives. Normalized evidence is never inferred from an upload toast."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields

from .client import Control, Observation, classify, embedded_nodes


class UnsafeState(RuntimeError):
    pass


@dataclass(frozen=True)
class Selector:
    control_type: str
    name: str = ""
    automation_id: str = ""

    def __post_init__(self):
        if not self.control_type or not (self.name or self.automation_id):
            raise ValueError("Semantic name or automation ID required")


def select_unique(controls: Sequence[Control], selector: Selector) -> Control:
    matches = [
        node
        for root in controls
        for node in root.walk()
        if node.enabled
        and node.control_type == selector.control_type
        and (not selector.name or node.name == selector.name)
        and (not selector.automation_id or node.automation_id == selector.automation_id)
    ]
    if len(matches) != 1:
        raise UnsafeState("Selector must identify exactly one enabled visible control")
    return matches[0]


@dataclass(frozen=True)
class AccountIdentity:
    platform: str
    account_id: str
    display_name: str = ""

    def __post_init__(self):
        if not self.platform.strip() or not self.account_id.strip():
            raise ValueError("Platform and stable account ID required; nickname is not identity")


def match_account(
    binding: AccountIdentity, candidates: Sequence[AccountIdentity]
) -> AccountIdentity | None:
    matches = [
        account
        for account in candidates
        if account.platform == binding.platform and account.account_id == binding.account_id
    ]
    if len(matches) > 1:
        raise UnsafeState("Duplicate account identity")
    return matches[0] if matches else None


@dataclass(frozen=True)
class SubmissionEvidence:
    platform: str
    account_id: str
    record_id: str
    attempt_id: str
    status: str
    observed_at: float
    submitted_at: float
    independently_verified: bool = False


def confirms_submission(
    evidence: SubmissionEvidence,
    platform: str,
    account_id: str,
    attempt_id: str,
    attempt_started_at: float,
) -> bool:
    timestamps = (attempt_started_at, evidence.submitted_at, evidence.observed_at)
    return (
        all(math.isfinite(value) for value in timestamps)
        and bool(attempt_id)
        and bool(account_id)
        and bool(platform)
        and evidence.independently_verified
        and evidence.status == "已提交"
        and evidence.platform == platform
        and evidence.account_id == account_id
        and evidence.attempt_id == attempt_id
        and bool(evidence.record_id.strip())
        and attempt_started_at <= evidence.submitted_at <= evidence.observed_at
    )


def retryable_targets(statuses: Mapping[str, str]) -> tuple[str, ...]:
    valid = {"PENDING", "SUBMITTING", "SUBMITTED", "FAILED", "UNKNOWN"}
    if any(value not in valid for value in statuses.values()):
        raise ValueError("Unknown submission state")
    return tuple(platform for platform, state in statuses.items() if state == "FAILED")


@dataclass(frozen=True)
class DesktopActivity:
    idle_seconds: float
    locked: bool | None
    foreground_hwnd: int
    human_input_detected: bool


def check_desktop(activity: DesktopActivity, *, expected_hwnd: int) -> None:
    if (
        activity.locked is not False
        or not math.isfinite(activity.idle_seconds)
        or activity.idle_seconds < 30
        or activity.human_input_detected
        or activity.foreground_hwnd != expected_hwnd
    ):
        raise UnsafeState("Unlocked, idle, unchanged desktop required")


@dataclass(frozen=True)
class GateEvidence:
    cold_start_verified: bool = False
    wake_verified: bool = False
    embedded_page_verified: bool = False
    account_identity_verified: bool = False
    submission_records_verified: bool = False

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(field.name for field in fields(self) if not getattr(self, field.name))

    @property
    def passed(self) -> bool:
        return not self.missing


def diagnostic_report(observation: Observation) -> dict:
    readiness = classify(observation)
    controls = [
        node for window in observation.windows for root in window.controls for node in root.walk()
    ]
    web_nodes = [node for window in observation.windows for node in embedded_nodes(window)]
    return {
        "state": readiness.state.value,
        "reason": readiness.reason,
        "scene": readiness.scene,
        "process_count": len(observation.process_ids),
        "window_count": len(observation.windows),
        "control_count": len(controls),
        "embedded_control_count": len(web_nodes),
        "probe_complete": not observation.error
        and all(not window.error and not window.truncated for window in observation.windows),
        "desktop_locked": observation.desktop_locked,
        "publication_enabled": False,
        "gate": {"passed": False, "reason": "三账号稳定身份及逐平台提交证据尚未通过实机验收。"},
    }
