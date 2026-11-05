import importlib
from pathlib import Path

import pytest

from fabu.client import Control


def module():
    try:
        return importlib.import_module("fabu.safety")
    except ModuleNotFoundError:
        pytest.fail("Safety module not implemented", pytrace=False)


def test_selector_requires_semantic_identity():
    s = module()
    with pytest.raises(ValueError):
        s.Selector(control_type="Button")


def test_unique_enabled_semantic_selector():
    s = module()
    control = Control(name="账号管理", control_type="Button", automation_id="accounts")
    assert s.select_unique((control,), s.Selector("Button", "账号管理")) == control


@pytest.mark.parametrize("enabled,visible", [(False, True), (True, False)])
def test_selector_rejects_unavailable_control(enabled, visible):
    s = module()
    with pytest.raises(s.UnsafeState):
        s.select_unique(
            (Control(name="发布", control_type="Button", enabled=enabled, visible=visible),),
            s.Selector("Button", "发布"),
        )


def test_duplicate_selector_never_chooses_first():
    s = module()
    controls = (Control(name="发布", control_type="Button"),) * 2
    with pytest.raises(s.UnsafeState):
        s.select_unique(controls, s.Selector("Button", "发布"))


def test_identity_requires_platform_and_stable_id():
    s = module()
    with pytest.raises(ValueError):
        s.AccountIdentity(platform="douyin", account_id="", display_name="ASMR")
    binding = s.AccountIdentity("douyin", "123", "ASMR")
    assert s.match_account(binding, [s.AccountIdentity("toutiao", "123", "ASMR")]) is None
    assert s.match_account(binding, [s.AccountIdentity("douyin", "999", "ASMR")]) is None
    assert (
        s.match_account(binding, [s.AccountIdentity("douyin", "123", "renamed")]).account_id
        == "123"
    )


def test_duplicate_account_identity_is_unsafe():
    s = module()
    account = s.AccountIdentity("douyin", "123", "ASMR")
    with pytest.raises(s.UnsafeState):
        s.match_account(account, [account, account])


def evidence(s, **kwargs):
    values = dict(
        platform="douyin",
        account_id="123",
        record_id="record1",
        attempt_id="attempt1",
        status="已提交",
        observed_at=120,
        submitted_at=105,
        independently_verified=True,
    )
    values.update(kwargs)
    return s.SubmissionEvidence(**values)


def test_created_upload_or_unverified_evidence_never_success():
    s = module()
    for status in ["上传完成", "任务已创建", "提交中", "失败", "未知"]:
        assert not s.confirms_submission(
            evidence(s, status=status), "douyin", "123", "attempt1", 100
        )
    assert not s.confirms_submission(
        evidence(s, independently_verified=False), "douyin", "123", "attempt1", 100
    )


def test_evidence_requires_unique_attempt_and_account_correlation():
    s = module()
    assert s.confirms_submission(evidence(s), "douyin", "123", "attempt1", 100)
    for change in [
        dict(platform="bilibili"),
        dict(account_id="999"),
        dict(attempt_id="attempt2"),
        dict(record_id=""),
        dict(submitted_at=99),
        dict(observed_at=101),
    ]:
        assert not s.confirms_submission(evidence(s, **change), "douyin", "123", "attempt1", 100)


def test_unknown_and_submitted_targets_cannot_retry():
    s = module()
    assert s.retryable_targets(
        {"douyin": "FAILED", "toutiao": "SUBMITTED", "bilibili": "UNKNOWN"}
    ) == ("douyin",)
    with pytest.raises(ValueError):
        s.retryable_targets({"douyin": "invented"})


def test_idle_guard_requires_30_seconds_and_known_unlocked_desktop():
    s = module()
    for idle, locked in [(29.9, False), (40, True), (40, None)]:
        with pytest.raises(s.UnsafeState):
            s.check_desktop(s.DesktopActivity(idle, locked, 17, False), expected_hwnd=17)
    s.check_desktop(s.DesktopActivity(30, False, 17, False), expected_hwnd=17)


def test_foreground_changes_and_human_input_stop_automation():
    s = module()
    for hwnd, human in [(18, False), (17, True)]:
        with pytest.raises(s.UnsafeState):
            s.check_desktop(s.DesktopActivity(30, False, hwnd, human), expected_hwnd=17)


def test_gate_is_not_passed_by_readable_window_alone():
    s = module()
    gate = s.GateEvidence(cold_start_verified=True, wake_verified=True, embedded_page_verified=True)
    assert not gate.passed
    assert "account_identity_verified" in gate.missing
    assert "submission_records_verified" in gate.missing


def test_gate_requires_all_five_real_verifications():
    s = module()
    gate = s.GateEvidence(True, True, True, True, True)
    assert gate.passed
    assert not gate.missing


def test_diagnostics_do_not_include_account_or_raw_page_content():
    s = module()
    from fabu.client import Observation, WindowSnapshot

    obs = Observation(
        (1,),
        (
            WindowSnapshot(
                17,
                1,
                Path("secret-path.exe"),
                controls=(
                    Control(name="Alice 13800138000", value="token=abc", control_type="Text"),
                ),
            ),
        ),
        False,
    )
    report = s.diagnostic_report(obs)
    assert report["publication_enabled"] is False
    assert "Alice" not in str(report)
    assert "13800138000" not in str(report)
    assert "abc" not in str(report)
    assert "secret-path" not in str(report)


def test_report_never_claims_all_platforms_submitted():
    s = module()
    assert (
        s.diagnostic_report(
            __import__("fabu.client", fromlist=["Observation"]).Observation(desktop_locked=False)
        )["publication_enabled"]
        is False
    )
