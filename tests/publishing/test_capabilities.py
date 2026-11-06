from datetime import UTC, datetime

import pytest

NOW = datetime(2026, 11, 7, tzinfo=UTC)


def query_fixture(name):
    return {
        "version": {"version": "3.2.32"},
        "accounts": {
            "accounts": [
                {"id": "fake-dy", "platform": "douyin", "nickname": "ASMR", "active": True},
                {"id": "fake-tt", "platform": "toutiao", "nickname": "ASMR", "active": True},
                {"id": "fake-bi", "platform": "bilibili", "nickname": "枕畔慢时光", "active": True},
            ]
        },
        "schema:douyin": {"fields": ["scheduledTime"]},
        "schema:toutiao": {"fields": ["scheduledTime"]},
        "schema:bilibili": {"fields": ["scheduledTime"]},
        "entitlements": {"client_daily_free": 3, "client_account_slots": 5},
    }[name]


def test_schema_field_does_not_prove_scheduling():
    from fabu.publishing.capabilities import inspect_capabilities

    report = inspect_capabilities(query_fixture, NOW)
    assert report.native_schedule == "UNKNOWN"
    assert report.immediate == "UNKNOWN"
    assert report.upload == "UNKNOWN"
    assert report.account_status == "SUPPORTED"
    assert report.tool_version == "3.2.32"
    assert report.checked_at == NOW
    assert report.evidence_refs


def test_free_client_quota_does_not_unlock_cli():
    from fabu.publishing.capabilities import can_enable_mode, inspect_capabilities

    report = inspect_capabilities(query_fixture, NOW)
    assert report.quota == "UNKNOWN"
    assert not can_enable_mode(report, "immediate", ["fake-dy", "fake-tt", "fake-bi"])


def test_paid_gate_stops_route():
    from fabu.publishing.capabilities import can_enable_mode, inspect_capabilities

    def query(name):
        if name == "entitlements":
            return {"requires_payment": True, "source": "verified_cli_entitlement"}
        return query_fixture(name)

    report = inspect_capabilities(query, NOW)
    assert report.payment_required
    assert not can_enable_mode(report, "immediate", ["fake-dy"])
    assert "paid_gate" in report.blockers


def test_untrusted_payment_marketing_not_hard_gate():
    from fabu.publishing.capabilities import inspect_capabilities

    def query(name):
        return {"requires_payment": True} if name == "entitlements" else query_fixture(name)

    assert not inspect_capabilities(query, NOW).payment_required


def test_query_failure_stays_unknown_without_error_leak():
    from fabu.publishing.capabilities import inspect_capabilities

    def query(name):
        raise RuntimeError("Bearer synthetic-secret-do-not-log")

    report = inspect_capabilities(query, NOW)
    assert report.account_status == "UNKNOWN"
    assert "synthetic-secret" not in report.model_dump_json()
    assert len(report.blockers) > 0


def test_inactive_account_does_not_enable():
    from fabu.publishing.capabilities import inspect_capabilities

    def query(name):
        return (
            {"accounts": [{"id": "x", "active": False}]}
            if name == "accounts"
            else query_fixture(name)
        )

    assert inspect_capabilities(query, NOW).account_status == "UNSUPPORTED"


def test_report_requires_timezone():
    from fabu.publishing.capabilities import inspect_capabilities

    with pytest.raises(ValueError, match="timezone"):
        inspect_capabilities(query_fixture, datetime(2026, 11, 7))


def test_readonly_gateway_refuses_write_commands(tmp_path):
    from fabu.diagnostics import ReadOnlyCli

    cli = ReadOnlyCli(executable=None, config=None, project_dir=tmp_path)
    for op in ["publish", "upload", "config get", "query arbitrary", "version; publish"]:
        with pytest.raises(ValueError, match="read-only"):
            cli.query(op)


def test_readonly_entitlements_not_invented(tmp_path):
    from fabu.diagnostics import ReadOnlyCli

    cli = ReadOnlyCli(executable=None, config=None, project_dir=tmp_path)
    assert cli.query("entitlements") == {"unavailable": True}


def test_normalize_response_retains_only_safe_account_fields():
    from fabu.diagnostics import normalize_readonly

    raw = {
        "ok": True,
        "data": {
            "accounts": [
                {
                    "id": "fake-id",
                    "platform": "抖音",
                    "name": "ASMR",
                    "status": 1,
                    "cookie": "secret-cookie",
                    "apiKey": "secret-key",
                }
            ]
        },
    }
    accounts = normalize_readonly("accounts", raw)["accounts"]
    assert accounts[0] == {
        "id": "fake-id",
        "platform": "douyin",
        "nickname": "ASMR",
        "active": True,
    }
    assert "secret" not in str(accounts)


def test_mode_cannot_enable_without_per_target_platform_evidence():
    from fabu.publishing.capabilities import CapabilityReport, can_enable_mode

    report = CapabilityReport(
        account_status="SUPPORTED",
        upload="SUPPORTED",
        immediate="SUPPORTED",
        quota="SUPPORTED",
        file_limits="SUPPORTED",
        checked_at=NOW,
    )
    assert not can_enable_mode(report, "immediate", [])
    assert not can_enable_mode(report, "immediate", ["nonexistent-target"])
    assert not can_enable_mode(report, "whatever", ["fake-dy"])


def test_normalize_actual_332_account_shape():
    from fabu.diagnostics import normalize_readonly

    raw = {
        "ok": True,
        "data": [
            {
                "id": "fake-332",
                "platformName": "哔哩哔哩",
                "platformAccountName": "枕畔慢时光",
                "status": 1,
                "proxyInfo": {"password": "not-loggable"},
            }
        ],
    }
    account = normalize_readonly("accounts", raw)["accounts"][0]
    assert account["nickname"] == "枕畔慢时光"
    assert account["platform"] == "bilibili"
    assert "proxyInfo" not in account
