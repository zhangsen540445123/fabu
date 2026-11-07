"""A deliberately finite, read-only CLI diagnostic adapter. Never config get/upload/publish."""

import json
import os
import re
import threading
from pathlib import Path

from fabu.tools import CommandSpec, run_command

_PLATFORMS = {"抖音": "douyin", "头条号": "toutiao", "哔哩哔哩": "bilibili", "B站": "bilibili"}
_OPERATIONS = {
    "version": ["--version"],
    "accounts": ["accounts", "list", "--json"],
    "schema:douyin": ["schema", "fields", "抖音", "video", "--json"],
    "schema:toutiao": ["schema", "fields", "头条号", "video", "--json"],
    "schema:bilibili": ["schema", "fields", "哔哩哔哩", "video", "--json"],
}


def normalize_readonly(operation: str, raw: dict) -> dict:
    if raw.get("ok") is not True:
        raise RuntimeError("CLI read-only query failed; check local configuration")
    data = raw.get("data", {})
    if operation == "version":
        return {"version": str(raw.get("version") or data.get("version", "unknown"))[:50]}
    if operation == "accounts":
        if isinstance(data, dict):
            rows = data.get("accounts", data.get("list", data.get("items")))
        else:
            rows = data
        if not isinstance(rows, list):
            raise ValueError("Unrecognized account-list response; do not guess account IDs")
        accounts = []
        for row in rows:
            platform = row.get("platform", row.get("platformName", ""))
            identifier = row.get("id", row.get("accountId"))
            if identifier is None or not isinstance(platform, str):
                raise ValueError("Account response lacks a stable ID or platform")
            accounts.append(
                {
                    "id": str(identifier),
                    "platform": _PLATFORMS.get(platform, platform),
                    "nickname": str(
                        row.get("platformAccountName", row.get("name", row.get("nickname", "")))
                    ),
                    "active": row.get("status") == 1,
                }
            )
        return {"accounts": accounts}
    if operation.startswith("schema:"):
        # Only field names are useful here, not defaults, private account values or raw objects.
        fields = []

        def visit(value):
            if isinstance(value, dict):
                for key in ("name", "field", "path", "key"):
                    if isinstance(value.get(key), str):
                        fields.append(value[key][:160])
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(data)
        return {"fields": sorted(set(fields))}
    raise ValueError("Operation is not an allowed read-only query")


class ReadOnlyCli:
    def __init__(self, executable: Path | None, config: Path | None, project_dir: Path):
        self.executable = executable
        self.config = config
        self.project_dir = project_dir
        self.cancel = threading.Event()

    def query(self, operation: str) -> dict:
        if operation == "entitlements":
            # No verified free-entitlement endpoint has been established. Do not invent one.
            return {"unavailable": True}
        if operation not in _OPERATIONS:
            raise ValueError("Operation is not an allowed read-only query")
        return normalize_readonly(
            operation, self._execute(_OPERATIONS[operation], operation != "version")
        )

    def categories(self, account_id: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", account_id):
            raise ValueError("Invalid stable account identifier")
        raw = self._execute(
            ["query", "categories", account_id, "--type", "video", "--paths", "--json"], True
        )
        if raw.get("ok") is not True or not isinstance(raw.get("data"), dict):
            raise ValueError("Category query did not return canonical evidence")
        return raw["data"]

    def details(self, provider_task_id: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", provider_task_id):
            raise ValueError("Invalid existing provider task identifier")
        raw = self._execute(["query", "details", provider_task_id, "--json"], True)
        if raw.get("ok") is not True or not isinstance(raw.get("data"), dict):
            raise ValueError("Details query did not return usable data")
        return raw["data"]

    def _execute(self, args, needs_config):
        if self.executable is None:
            raise ValueError("CLI executable not configured")
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("YIXIAOER_", "YXER_"))
        }
        env["YIXIAOER_PROJECT_DIR"] = str(self.project_dir)
        env["YIXIAOER_API_URL"] = "https://www.yixiaoer.cn/api"
        if needs_config:
            if self.config is None or not self.config.is_file():
                raise ValueError("A known local CLI config is required for account/schema queries")
            env["YIXIAOER_CONFIG"] = str(self.config)
        spec = CommandSpec(
            executable=self.executable,
            args=args,
            cwd=self.project_dir,
            env=env,
            timeout_s=30,
        )
        if self.cancel.is_set():
            raise RuntimeError("Read-only query canceled")
        result = run_command(spec, self.cancel)
        if (
            result.exit_code != 0
            or result.timed_out
            or result.canceled
            or result.output_limit_exceeded
        ):
            raise RuntimeError("CLI read-only query did not complete successfully")
        raw = json.loads(result.stdout.decode("utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("CLI response must be an object")
        return raw
