"""Offline request-contract checks, not evidence of upload/publication capability."""

import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from cli_contract_support import offline_response
from test_templates import build

from fabu.publishing.gateway import (
    PLATFORM_NAMES,
    UploadReceipt,
    assemble_prepared,
    resource_metadata,
)
from fabu.publishing.permits import PermitAuthority
from fabu.tools import CommandSpec, run_command


@pytest.fixture
def prepared(publishing_context):
    snapshot = build(publishing_context)
    receipts = tuple(
        UploadReceipt(
            resource=kind,
            sha256=item.sha256,
            key="offline-fabu-synthetic/" + kind,
            metadata=resource_metadata(snapshot, kind),
            raw={"synthetic": True},
        )
        for kind, item in [("video", snapshot.output), ("cover", snapshot.cover)]
    )
    return assemble_prepared(snapshot, receipts, "offline-fabu-synthetic-client", PermitAuthority())


@pytest.mark.parametrize("phase", ["validate", "dry_run"])
def test_observed_nested_request_contract(prepared, phase):
    from fabu.publishing.cli_contract import matches_local_preflight

    for payload in prepared.payloads.values():
        assert matches_local_preflight(offline_response(payload, phase), payload, phase)


@pytest.mark.parametrize("phase", ["validate", "dry_run"])
@pytest.mark.parametrize(
    "mutation",
    [
        "flat_channel_only",
        "wrong_phase",
        "wrong_version",
        "wrong_client",
        "cloud",
        "wrong_account",
        "additional_account",
        "wrong_platform",
        "wrong_video",
        "wrong_cover",
        "changed_title",
        "changed_tags",
        "changed_declaration",
        "added_schedule",
        "extra_field",
        "draft",
        "bool_as_int",
        "string_number",
        "missing_request",
        "list_request",
        "not_ok",
        "phase_not_confirmed",
        "remote_checks",
        "meta_client",
        "meta_cloud",
        "meta_accounts",
        "normalizations",
        "meta_schema",
        "materialization",
    ],
)
def test_unbound_or_mutated_preflight_is_rejected(prepared, phase, mutation):
    from fabu.publishing.cli_contract import matches_local_preflight

    payload = next(iter(prepared.payloads.values()))
    raw = offline_response(payload, phase)
    data = raw["data"]
    request = data["request"]
    account = request["publishArgs"]["accountForms"][0]
    form = account["contentPublishForm"]
    if mutation == "flat_channel_only":
        raw["data"] = {"publishChannel": "local", "clientId": payload["clientId"]}
    elif mutation == "wrong_phase":
        raw["action"] = "publish"
    elif mutation == "wrong_version":
        raw["version"] = "3.2.31"
    elif mutation == "wrong_client":
        request["clientId"] = "another-client"
    elif mutation == "cloud":
        request["publishChannel"] = "cloud"
    elif mutation == "wrong_account":
        account["platformAccountId"] = "another-account"
    elif mutation == "additional_account":
        request["publishArgs"]["accountForms"].append(deepcopy(account))
    elif mutation == "wrong_platform":
        request["platforms"] = ["unknown-platform"]
    elif mutation == "wrong_video":
        account["video"]["key"] = "another-video"
    elif mutation == "wrong_cover":
        account["cover"]["key"] = "another-cover"
    elif mutation == "changed_title":
        form["title"] = "another-title"
    elif mutation == "changed_tags":
        form["tags"] = ["another-tag"]
    elif mutation == "changed_declaration":
        form["declaration"] = 3
    elif mutation == "added_schedule":
        form["scheduledTime"] = 1900000000000
    elif mutation == "extra_field":
        form["unreviewed"] = True
    elif mutation == "draft":
        request["isDraft"] = True
    elif mutation == "bool_as_int":
        request["isDraft"] = 0
    elif mutation == "string_number":
        account["video"]["duration"] = "1"
    elif mutation == "missing_request":
        data.pop("request")
    elif mutation == "list_request":
        data["request"] = []
    elif mutation == "not_ok":
        raw["ok"] = False
    elif mutation == "phase_not_confirmed":
        data["valid" if phase == "validate" else "dryRun"] = False
    elif mutation == "remote_checks":
        (data if phase == "validate" else data["meta"])["remoteChecks"] = True
    else:
        if phase == "validate":
            data["prepared"] = False
        else:
            meta = data["meta"]
            if mutation == "meta_client":
                meta["clientId"] = "another-client"
            elif mutation == "meta_cloud":
                meta["effectivePublishChannel"] = "cloud"
            elif mutation == "meta_accounts":
                meta["accountIds"] = ["another-account"]
            elif mutation == "normalizations":
                meta["normalizations"] = ["rewrote-fields"]
            elif mutation == "meta_schema":
                meta["schemaChecked"] = False
            elif mutation == "materialization":
                meta["contentImageMaterialization"] = ["uploaded"]
    assert not matches_local_preflight(raw, payload, phase)


def test_numeric_roundtrip_does_not_change_duration(prepared):
    from fabu.publishing.cli_contract import matches_local_preflight

    payload = next(iter(prepared.payloads.values()))
    raw = offline_response(payload, "validate")
    raw["data"]["request"]["publishArgs"]["accountForms"][0]["video"]["duration"] = 1
    assert matches_local_preflight(raw, payload, "validate")


@pytest.fixture
def installed_cli(tmp_path):
    value = os.environ.get("FABU_TEST_YIXIAOER_PACKAGE")
    if not value:
        pytest.skip("Set FABU_TEST_YIXIAOER_PACKAGE for isolated installed-binary offline checks")
    root = Path(value).resolve()
    executable = root / "bin-native/yxer.exe"
    assert executable.is_file()
    config = tmp_path / "empty-offline-config.json"
    config.write_text("{}", encoding="utf-8")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("YIXIAOER_", "YXER_"))
    }
    env.update(
        {
            "YIXIAOER_PROJECT_DIR": str(root),
            "YIXIAOER_CONFIG": str(config),
            "YIXIAOER_API_URL": "http://127.0.0.1:1",
        }
    )

    def run(payload, platform, phase):
        path = tmp_path / "offline-synthetic-payload.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        args = (
            ["validate", platform, "video", str(path)]
            if phase == "validate"
            else ["publish", "video", platform, str(path), "--dry-run"]
        )
        assert args[0] == "validate" or "--dry-run" in args
        args += ["--publish-channel", "local", "--client-id", payload["clientId"], "--json"]
        result = run_command(
            CommandSpec(executable=executable, args=args, cwd=tmp_path, env=env, timeout_s=15),
            Event(),
        )
        assert not result.timed_out and not result.canceled and not result.output_limit_exceeded
        return result.exit_code, json.loads((result.stdout or result.stderr).decode("utf-8-sig"))

    return run


@pytest.mark.parametrize("mode", ["immediate", "native_schedule"])
def test_installed_three_platform_offline_matrix(publishing_context, installed_cli, mode):
    from fabu.publishing.cli_contract import matches_local_preflight
    from fabu.publishing.models import SourceDeclaration

    store, assets, presets, approvals, job, output, config = publishing_context
    when = (
        (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
        if mode == "native_schedule"
        else None
    )
    spec = job.spec.model_copy(update={"publish_mode": mode, "scheduled_at_utc": when})
    with store.transaction() as db:
        db.execute("UPDATE jobs SET spec_json=? WHERE id=?", (spec.model_dump_json(), job.id))
    # Self-created SYNTHETIC fixture only; never relabel user media to make CLI accept it.
    config = config.model_copy(
        update={
            "accounts": tuple(
                setting.model_copy(
                    update={
                        "declaration": SourceDeclaration(
                            origin="self_created",
                            confirmed=True,
                            statement="none",
                            bilibili_type="self_produced"
                            if setting.account.platform == "bilibili"
                            else None,
                        )
                    }
                )
                for setting in config.accounts
            )
        }
    )
    snapshot = build((store, assets, presets, approvals, store.get(job.id), output, config))
    receipts = tuple(
        UploadReceipt(
            resource=kind,
            sha256=item.sha256,
            key="offline-fabu-synthetic/" + kind,
            metadata=resource_metadata(snapshot, kind),
            raw={"synthetic": True},
        )
        for kind, item in [("video", output), ("cover", snapshot.cover)]
    )
    prepared = assemble_prepared(
        snapshot, receipts, "offline-fabu-synthetic-client", PermitAuthority()
    )
    for target in snapshot.targets:
        payload = prepared.payloads[target.account.account_id]
        for phase in ("validate", "dry_run"):
            code, raw = installed_cli(payload, PLATFORM_NAMES[target.account.platform], phase)
            assert code == 0 and raw.get("ok") is True
            assert matches_local_preflight(raw, payload, phase)


def test_installed_repost_source_url_rejection_is_not_hidden(prepared, installed_cli):
    payload = prepared.payloads["bilibili-id"]
    form = payload["publishArgs"]["accountForms"][0]["contentPublishForm"]
    assert form["createType"] == 2 and form["contentSourceUrl"].startswith("https://")
    for phase in ("validate", "dry_run"):
        code, raw = installed_cli(payload, PLATFORM_NAMES["bilibili"], phase)
        assert code != 0 and raw.get("ok") is False
        assert any(
            "contentSourceUrl" in item and "external URL" in item
            for item in raw["error"]["details"]
        )
