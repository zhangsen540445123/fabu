"""Canonical installed-CLI acknowledgements; no external server or real credentials."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlparse

import pytest
from test_gateway import gateway_setup as gateway_setup
from test_gateway import live_snapshot, permit

from fabu.tools import CommandResult, CommandSpec, run_command


def acknowledged(data=None, **changes):
    return {
        "ok": True,
        "version": "3.2.32",
        "action": "publish",
        "data": data or {"taskSetId": "set-1", "response": "set-1"},
        **changes,
    }


def test_canonical_ack_is_accepted_but_channel_and_platform_result_remain_unverified(gateway_setup):
    snapshot, gateway, authority, calls, responses = gateway_setup
    snapshot = live_snapshot(snapshot)
    scope = permit(authority, snapshot)
    prepared = gateway.prepare(snapshot, scope)
    responses.append(
        CommandResult(exit_code=0, stdout=json.dumps(acknowledged()).encode(), stderr=b"")
    )
    target = snapshot.targets[0].account.account_id
    receipt = gateway.submit(prepared, scope, target)
    assert receipt.state == "PROVIDER_ACCEPTED"
    assert receipt.provider_task_id == "set-1"
    assert receipt.actual_channel is None
    assert receipt.platform_content_id is None and receipt.evidence_origin == "unknown"
    assert receipt.definitely_not_submitted is False
    before = len(calls)
    with pytest.raises(ValueError):
        gateway.submit(prepared, scope, target)
    assert len(calls) == before


@pytest.mark.parametrize(
    "change",
    [
        {"action": "publish.dry-run"},
        {"action": "query.details"},
        {"action": None},
        {"version": "3.2.31"},
        {"ok": False},
        {"data": {"taskSetId": "set-1", "publishChannel": "cloud"}},
        {"data": {"taskSetId": "set-1", "clientId": "different"}},
        {"data": {"taskSetId": "set-1", "publishChannel": "local"}},
        {"data": {"taskSetId": "set-1", "publishChannel": "local", "clientId": None}},
        {"data": {"taskSetId": "set-1", "meta": {"publishChannel": "cloud"}}},
        {"data": {"taskSetId": "../bad"}},
        {"data": {"taskSetId": ""}},
    ],
)
def test_uncertain_receipt_never_promotes_or_authorizes_retry(gateway_setup, change):
    snapshot, gateway, authority, calls, responses = gateway_setup
    snapshot = live_snapshot(snapshot)
    scope = permit(authority, snapshot)
    prepared = gateway.prepare(snapshot, scope)
    raw = acknowledged(**change)
    responses.append(CommandResult(exit_code=0, stdout=json.dumps(raw).encode(), stderr=b""))
    receipt = gateway.submit(prepared, scope, snapshot.targets[0].account.account_id)
    assert receipt.state == "UNKNOWN" and not receipt.definitely_not_submitted
    assert receipt.platform_content_id is None
    assert len([c for c in calls if c.args[0] == "publish" and "--dry-run" not in c.args]) == 1


@pytest.mark.parametrize(
    "remote_data, expected_state",
    [
        ("loopback-synthetic-task", "PROVIDER_ACCEPTED"),
        (
            {
                "taskSetId": "loopback-synthetic-task",
                "publishChannel": "cloud",
                "clientId": "synthetic-client",
            },
            "UNKNOWN",
        ),
        (
            {
                "taskSetId": "loopback-synthetic-task",
                "publishChannel": "local",
                "clientId": "different",
            },
            "UNKNOWN",
        ),
    ],
)
def test_installed_publish_receipt_is_consumed_without_fabricated_channel_fields(
    gateway_setup, tmp_path, remote_data, expected_state
):
    value = os.environ.get("FABU_TEST_YIXIAOER_PACKAGE")
    if not value:
        pytest.skip("Set FABU_TEST_YIXIAOER_PACKAGE for loopback-only installed CLI contract check")
    root = Path(value).resolve()
    snapshot, gateway, authority, calls, responses = gateway_setup
    snapshot = live_snapshot(snapshot)
    scope = permit(authority, snapshot)
    prepared = gateway.prepare(snapshot, scope)
    target = snapshot.targets[0]
    payload = prepared.payloads[target.account.account_id]
    path = tmp_path / "synthetic-payload.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    config = tmp_path / "isolated-config.json"
    config.write_text(json.dumps({"apiKey": "synthetic-not-a-real-credential"}), encoding="utf-8")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            requests.append(("GET", self.path))
            if urlparse(self.path).path != "/api/v2/platform/accounts":
                self.reply(404, {"statusCode": 404})
                return
            self.reply(
                200,
                {
                    "statusCode": 0,
                    "data": {
                        "list": [
                            {
                                "id": target.account.account_id,
                                "platform": "抖音",
                                "platformAccountName": "synthetic",
                                "status": 1,
                            }
                        ],
                        "totalSize": 1,
                    },
                },
            )

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            requests.append(("POST", self.path, body))
            if self.path != "/api/taskSets/v2":
                self.reply(404, {"statusCode": 404})
                return
            self.reply(200, {"statusCode": 0, "data": remote_data})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("YIXIAOER_", "YXER_"))
        and k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}
    }
    env.update(
        {
            "YIXIAOER_PROJECT_DIR": str(root),
            "YIXIAOER_CONFIG": str(config),
            "YIXIAOER_API_URL": f"http://127.0.0.1:{server.server_port}/api",
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )
    try:
        command = [
            "publish",
            "video",
            "抖音",
            str(path),
            "--publish-channel",
            "local",
            "--client-id",
            "synthetic-client",
            "--json",
        ]
        result = run_command(
            CommandSpec(
                executable=root / "bin-native/yxer.exe",
                args=command,
                cwd=tmp_path,
                env=env,
                timeout_s=15,
            ),
            Event(),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    raw = json.loads((result.stdout or result.stderr).decode("utf-8-sig"))
    assert result.exit_code == 0, raw
    assert raw["action"] == "publish" and raw["data"]["taskSetId"] == "loopback-synthetic-task"
    assert "publishChannel" not in raw["data"] and "clientId" not in raw["data"]
    posts = [r for r in requests if r[0] == "POST"]
    assert len(posts) == 1
    body = posts[0][2]
    assert body["publishChannel"] == "local" and body["clientId"] == "synthetic-client"
    assert body["publishArgs"]["accountForms"][0]["platformAccountId"] == target.account.account_id
    responses.append(result)
    receipt = gateway.submit(prepared, scope, target.account.account_id)
    assert receipt.state == expected_state and receipt.provider_task_id == "loopback-synthetic-task"
    assert receipt.actual_channel is None and receipt.platform_content_id is None


@pytest.mark.parametrize(
    "response",
    [
        {"taskSetId": "set-1", "publishChannel": "cloud", "clientId": "synthetic-client"},
        {"taskSetId": "set-1", "clientId": "other-client"},
        {"taskSetId": "other-id"},
        {"data": {"publishChannel": "cloud"}},
        ["unrecognized"],
        "different-id",
    ],
)
def test_nested_response_contradictions_never_become_unconfirmed_acceptance(response):
    from fabu.publishing.cli_receipts import parse_publish_acknowledgement

    result = parse_publish_acknowledgement(
        acknowledged({"taskSetId": "set-1", "response": response}),
        transport_ok=True,
        target_id="account",
        client_id="synthetic-client",
    )
    assert result.state == "UNKNOWN" and result.provider_task_id == "set-1"
    assert result.definitely_not_submitted is False and result.actual_channel is None
