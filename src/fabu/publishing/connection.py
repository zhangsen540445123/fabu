"""Explicit credential configuration and finite read-only account discovery."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, SecretStr

from fabu.assets import assert_no_reparse
from fabu.credentials import Credentials
from fabu.publishing.models import CLI_CONTRACT_VERSION, AccountBinding, CategoryEvidence
from fabu.publishing.templates import select_bilibili_category


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_root: Path
    api_key: SecretStr
    local_client_id: str


def _path(app):
    path = app.state.settings.state_dir / "publishing-connection.json"
    assert_no_reparse(path)
    return path


def _package(root):
    if not root.is_absolute():
        raise ValueError("Official CLI package path must be absolute")
    assert_no_reparse(root)
    if not (root / "bin-native/yxer.exe").is_file() or not all(
        (root / name).is_dir() for name in ["schemas", "references"]
    ):
        raise ValueError("Select the installed official CLI package root")
    return root


def connection_status(app):
    path = _path(app)
    root = (
        json.loads(path.read_text(encoding="utf-8")).get("package_root") if path.is_file() else None
    )
    return {
        "configured": app.state.credentials.configured(),
        "package_root": root,
        "live_enabled": bool(
            getattr(app.state, "live_policy", None) and app.state.live_policy.enabled()
        ),
    }


def save_connection(app, request):
    root = _package(request.package_root)
    credentials = Credentials(api_key=request.api_key, local_client_id=request.local_client_id)
    with app.state.settings_lock:
        if app.state.media_controller.status()["job_id"]:
            raise ValueError("Cannot replace credentials during a running job")
        app.state.credentials.save(credentials)
        path = _path(app)
        temporary = path.with_suffix(".tmp")
        assert_no_reparse(temporary)
        temporary.write_text(json.dumps({"package_root": str(root)}), encoding="utf-8")
        os.replace(temporary, path)
        # Credentials may belong to a different account; old inventory must not look current.
        with app.state.store.transaction() as db:
            db.execute("DELETE FROM publication_catalog WHERE scope='live'")
    return connection_status(app)


def refresh_accounts(app):
    with app.state.settings_lock:
        status = connection_status(app)
        if not status["configured"] or not status["package_root"]:
            raise ValueError("Configure the official CLI connection first")
        root = _package(Path(status["package_root"]))
        with app.state.credentials.temporary_cli_config() as config:
            client = app.state.readonly_cli_factory(root / "bin-native/yxer.exe", config, root)
            if client.query("version").get("version") != CLI_CONTRACT_VERSION:
                raise ValueError("CLI version is not the reviewed contract")
            raw = client.query("accounts").get("accounts")
            if not isinstance(raw, list) or len(raw) > 100:
                raise ValueError("Unexpected account response")
            entries = []
            seen = set()
            now = datetime.now(UTC)
            for item in raw:
                if item.get("platform") not in {"douyin", "toutiao", "bilibili"}:
                    continue
                account = AccountBinding(
                    account_id=item["id"],
                    platform=item["platform"],
                    nickname=item["nickname"],
                    active=item["active"],
                )
                if account.account_id in seen:
                    raise ValueError("Duplicate stable account identifier")
                seen.add(account.account_id)
                category = None
                if account.platform == "bilibili" and account.active:
                    category = CategoryEvidence(
                        account_id=account.account_id,
                        queried_at=now,
                        path=select_bilibili_category(client.categories(account.account_id)),
                    )
                entries.append((account, category))
        with app.state.store.transaction() as db:
            db.execute("DELETE FROM publication_catalog WHERE scope='live'")
            for account, category in entries:
                db.execute(
                    "INSERT INTO publication_catalog VALUES ('live',?,?,?,?)",
                    (
                        account.account_id,
                        account.model_dump_json(),
                        category.model_dump_json() if category else None,
                        now.isoformat(),
                    ),
                )
    return {
        "refreshed": len(entries),
        "checked_at": now,
        "live_enabled": connection_status(app)["live_enabled"],
    }
