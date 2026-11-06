from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_templates import build


@pytest.fixture
def gateway_setup(publishing_context, tmp_path):
    import json

    from fabu.credentials import Credentials, CredentialStore
    from fabu.publishing.cli_gateway import CliPublisher
    from fabu.publishing.permits import PermitAuthority
    from fabu.tools import CommandResult

    snapshot = build(publishing_context)
    vault = CredentialStore(tmp_path / "vault")
    vault.save(Credentials(api_key="synthetic-gateway-key", local_client_id="synthetic-client"))
    package = tmp_path / "cli"
    package.mkdir()
    (package / "schemas").mkdir()
    (package / "references").mkdir()
    (package / "bin-native").mkdir()
    exe = package / "bin-native/yxer.exe"
    exe.write_bytes(b"synthetic-no-executable")
    media = tmp_path / "media-tools"
    media.mkdir()
    (media / "ffmpeg.exe").write_bytes(b"test")
    (media / "ffprobe.exe").write_bytes(b"test")
    calls = []
    responses = []

    def runner(spec, cancel):
        calls.append(spec)
        assert Path(spec.env["YIXIAOER_CONFIG"]).is_file()
        if responses:
            return responses.pop(0)
        args = spec.args
        if args == ["--version"]:
            data = {"version": "3.2.32"}
        elif args[0] == "upload":
            data = {
                "key": "cloud-publish/test/" + Path(args[1]).name,
                "bucket": "cloud-publish",
                "name": Path(args[1]).name,
            }
        elif args[0] == "validate" or "--dry-run" in args:
            data = {"publishChannel": "local", "clientId": "synthetic-client"}
        elif args[0] == "publish":
            data = {
                "taskSetId": "provider-task-1",
                "publishChannel": "local",
                "clientId": "synthetic-client",
            }
        else:
            data = {}
        return CommandResult(
            exit_code=0,
            stdout=json.dumps({"ok": True, "version": "3.2.32", "data": data}).encode(),
            stderr=b"",
        )

    authority = PermitAuthority()
    gateway = CliPublisher(package, vault, media, tmp_path / "payloads", authority, runner=runner)
    return snapshot, gateway, authority, calls, responses


def live_snapshot(snapshot):
    from fabu.publishing.models import publish_snapshot_digest

    result = snapshot.model_copy(update={"mode": "immediate"})
    return result.model_copy(update={"digest": publish_snapshot_digest(result)})


def permit(authority, snapshot):
    return authority.issue(
        snapshot,
        kind="controlled_test",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        max_uploads=2,
        max_submissions_per_target=1,
        allow_temporary_storage_upload=True,
        authorization_note="Synthetic unit-test scope only",
    )


def test_default_gateway_never_writes(gateway_setup):
    snapshot, gateway, _, calls, _ = gateway_setup
    with pytest.raises(ValueError):
        gateway.prepare(snapshot, None)
    assert calls == []


def test_local_channel_cannot_fall_back(gateway_setup):
    import json

    from fabu.tools import CommandResult

    snapshot, gateway, authority, calls, responses = gateway_setup
    snapshot = live_snapshot(snapshot)
    scope = permit(authority, snapshot)
    prepared = gateway.prepare(snapshot, scope)
    assert len([s for s in calls if s.args[0] == "upload"]) == 2
    responses.append(
        CommandResult(
            exit_code=1,
            stdout=b"",
            stderr=json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "YIXIAOER_REMOTE_ERR",
                        "message": "secret https://bad/?token=secret",
                        "nextCommand": "publish --publish-channel cloud",
                    },
                }
            ).encode(),
        )
    )
    receipt = gateway.submit(prepared, scope, snapshot.targets[0].account.account_id)
    assert receipt.state == "UNKNOWN"
    commands = [s.args for s in calls if s.args[0] in {"publish", "validate"}]
    assert commands and all(c[c.index("--publish-channel") + 1] == "local" for c in commands)
    assert all("--auto-fallback-local" not in c for c in commands)
    assert "secret" not in receipt.model_dump_json()
    assert all(not Path(s.env["YIXIAOER_CONFIG"]).exists() for s in calls)


def test_ok_does_not_mean_published(gateway_setup):
    snapshot, gateway, authority, _, _ = gateway_setup
    snapshot = live_snapshot(snapshot)
    scope = permit(authority, snapshot)
    prepared = gateway.prepare(snapshot, scope)
    result = gateway.submit(prepared, scope, snapshot.targets[0].account.account_id)
    assert result.state == "PROVIDER_ACCEPTED" and result.provider_task_id == "provider-task-1"
    assert result.platform_content_id is None


def test_permit_expiry_mutation_and_operation_budget(gateway_setup):
    from fabu.publishing.models import publish_snapshot_digest

    snapshot, gateway, authority, calls, _ = gateway_setup
    snapshot = live_snapshot(snapshot)
    scope = permit(authority, snapshot)
    bad = scope.model_copy(update={"max_uploads": 100})
    with pytest.raises(ValueError):
        gateway.prepare(snapshot, bad)
    assert not calls
    prepared = gateway.prepare(snapshot, scope)
    target = snapshot.targets[0].account.account_id
    gateway.submit(prepared, scope, target)
    count = len(calls)
    with pytest.raises(ValueError):
        gateway.submit(prepared, scope, target)
    assert len(calls) == count
    changed = snapshot.model_copy(update={"title": "different"})
    changed = changed.model_copy(update={"digest": publish_snapshot_digest(changed)})
    with pytest.raises(ValueError):
        authority.validate(scope, changed)


def test_wrong_channel_or_unstructured_success_is_unknown(gateway_setup):
    import json

    from fabu.tools import CommandResult

    snapshot, gateway, authority, _, responses = gateway_setup
    snapshot = live_snapshot(snapshot)
    scope = permit(authority, snapshot)
    prepared = gateway.prepare(snapshot, scope)
    responses.append(
        CommandResult(
            exit_code=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "data": {"taskSetId": "task-x", "publishChannel": "cloud"},
                    "version": "3.2.32",
                }
            ).encode(),
            stderr=b"",
        )
    )
    receipt = gateway.submit(prepared, scope, snapshot.targets[0].account.account_id)
    assert receipt.state == "UNKNOWN" and receipt.reason == "channel_mismatch"


def test_prepared_payload_cannot_be_modified(gateway_setup):
    snapshot, gateway, authority, calls, _ = gateway_setup
    snapshot = live_snapshot(snapshot)
    scope = permit(authority, snapshot)
    prepared = gateway.prepare(snapshot, scope)
    target = snapshot.targets[0].account.account_id
    prepared.payloads[target]["publishChannel"] = "cloud"
    before = len(calls)
    with pytest.raises(ValueError):
        gateway.submit(prepared, scope, target)
    assert len(calls) == before


def test_fake_gateway_has_no_cli_or_network_fallback(publishing_context):
    from fabu.publishing.fake_gateway import FakePublisher
    from fabu.publishing.permits import PermitAuthority

    snapshot = build(publishing_context)
    authority = PermitAuthority()
    gateway = FakePublisher(authority, outcomes=["SUBMITTED", "FAILED", "UNKNOWN"])
    scope = authority.issue(
        snapshot,
        kind="simulation",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        max_uploads=2,
        max_submissions_per_target=1,
        authorization_note="Local simulation",
    )
    prepared = gateway.prepare(snapshot, scope)
    states = [gateway.submit(prepared, scope, t.account.account_id).state for t in snapshot.targets]
    assert states == ["SUBMITTED", "FAILED", "UNKNOWN"]
    assert len(gateway.calls) == 4


def test_unknown_upload_is_not_repeated_even_with_a_new_permit(gateway_setup):
    import json

    from fabu.tools import CommandResult

    snapshot, gateway, authority, calls, responses = gateway_setup
    snapshot = live_snapshot(snapshot)
    responses.extend(
        [
            CommandResult(
                exit_code=0,
                stdout=json.dumps(
                    {"ok": True, "version": "3.2.32", "data": {"version": "3.2.32"}}
                ).encode(),
                stderr=b"",
            ),
            CommandResult(exit_code=-1, stdout=b"", stderr=b"", timed_out=True),
        ]
    )
    with pytest.raises(ValueError, match="unknown"):
        gateway.prepare(snapshot, permit(authority, snapshot))
    with pytest.raises(ValueError, match="unknown"):
        gateway.prepare(snapshot, permit(authority, snapshot))
    assert len([call for call in calls if call.args[0] == "upload"]) == 1


def test_expired_and_simulation_permits_reject_real_writes(gateway_setup):
    snapshot, gateway, authority, calls, _ = gateway_setup
    local = authority.issue(
        snapshot,
        kind="simulation",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
        max_uploads=2,
        max_submissions_per_target=1,
        authorization_note="Only simulation",
    )
    with pytest.raises(ValueError):
        gateway.prepare(snapshot, local)
    snapshot = live_snapshot(snapshot)
    scope = permit(authority, snapshot)
    authority.clock = lambda: scope.expires_at + timedelta(seconds=1)
    with pytest.raises(ValueError):
        gateway.prepare(snapshot, scope)
    assert not calls


def test_readonly_preflight_never_unlocks_free_native_scheduling(gateway_setup):
    snapshot, gateway, _, calls, _ = gateway_setup
    report = gateway.preflight(snapshot)
    assert report.quota == report.upload == report.native_schedule == "UNKNOWN"
    assert [c.args[0] for c in calls] == ["--version", "accounts"]


def test_reconcile_does_not_trust_unverified_platform_status(gateway_setup):
    from fabu.domain import TargetAttempt

    snapshot, gateway, _, calls, _ = gateway_setup
    attempt = TargetAttempt(
        job_id=snapshot.job_id,
        revision=snapshot.content_revision,
        target_id=snapshot.targets[0].account.account_id,
        payload_digest=snapshot.digest,
        provider_task_id="provider-task-1",
        state="PROVIDER_ACCEPTED",
    )
    result = gateway.reconcile(attempt)
    assert result.state == "UNKNOWN" and result.reason == "platform_evidence_contract_unverified"
    assert calls[-1].args == ["query", "details", "provider-task-1", "--json"]


def test_changed_resource_or_cli_prevents_new_side_effect(gateway_setup):
    snapshot, gateway, authority, calls, _ = gateway_setup
    snapshot = live_snapshot(snapshot)
    scope = permit(authority, snapshot)
    prepared = gateway.prepare(snapshot, scope)
    count = len(calls)
    gateway.executable.write_bytes(b"different-binary")
    with pytest.raises(ValueError, match="executable changed"):
        gateway.submit(prepared, scope, snapshot.targets[0].account.account_id)
    assert len(calls) == count


def test_prepared_payload_contains_matching_verified_resource_metadata(gateway_setup):
    snapshot, gateway, authority, calls, _ = gateway_setup
    snapshot = live_snapshot(snapshot)
    scope = permit(authority, snapshot)
    prepared = gateway.prepare(snapshot, scope)
    for value in prepared.payloads.values():
        account = value["publishArgs"]["accountForms"][0]
        assert account["video"]["size"] == snapshot.output.size_bytes
        assert account["video"]["width"] == 2560
        assert account["cover"]["size"] == snapshot.cover.size_bytes
        assert account["cover"]["key"] == account["coverKey"] == value["coverKey"]
        assert value["publishChannel"] == "local" and value["clientId"] == "synthetic-client"
    assert not list(gateway.workspace.glob("payload-*.json"))


def test_canceled_fake_delay_does_not_invent_submission(publishing_context):
    from threading import Event

    from fabu.publishing.fake_gateway import FakePublisher
    from fabu.publishing.permits import PermitAuthority

    snapshot = build(publishing_context)
    authority = PermitAuthority()
    cancel = Event()
    gateway = FakePublisher(authority, delay_seconds=5, cancel=cancel)
    scope = authority.issue(
        snapshot,
        kind="simulation",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        max_uploads=2,
        max_submissions_per_target=1,
        authorization_note="Synthetic canceled run",
    )
    prepared = gateway.prepare(snapshot, scope)
    cancel.set()
    result = gateway.submit(prepared, scope, snapshot.targets[0].account.account_id)
    assert result.state == "UNKNOWN" and result.platform_content_id is None


def test_real_transport_requires_durable_upload_journal(gateway_setup):
    from fabu.publishing.cli_gateway import CliPublisher

    _, gateway, authority, _, _ = gateway_setup
    with pytest.raises(ValueError, match="durable"):
        CliPublisher(
            gateway.package_root,
            gateway.credentials,
            gateway.media_tools,
            gateway.workspace,
            authority,
        )


def test_native_schedule_can_be_simulated_without_a_real_permit(publishing_context):
    from fabu.publishing.fake_gateway import FakePublisher
    from fabu.publishing.models import publish_snapshot_digest
    from fabu.publishing.permits import PermitAuthority

    snapshot = build(publishing_context)
    when = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    snapshot = snapshot.model_copy(update={"mode": "native_schedule", "scheduled_at_utc": when})
    targets = tuple(
        t.model_copy(update={"form": {**t.form, "scheduledTime": int(when.timestamp() * 1000)}})
        for t in snapshot.targets
    )
    snapshot = snapshot.model_copy(update={"targets": targets})
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    authority = PermitAuthority()
    gateway = FakePublisher(authority)
    scope = authority.issue(
        snapshot,
        kind="simulation",
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
        max_uploads=2,
        max_submissions_per_target=1,
        authorization_note="Local scheduling simulation",
    )
    prepared = gateway.prepare(snapshot, scope)
    receipt = gateway.submit(prepared, scope, snapshot.targets[0].account.account_id)
    assert receipt.state == "SCHEDULED" and receipt.actual_channel == "simulation"
    assert receipt.scheduled_at_utc == when
