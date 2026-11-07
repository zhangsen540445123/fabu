"""Explicit, persistent publishing intent. Defaults never authorize transport writes."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from fabu.publishing.models import AccountPublishSettings, PublicationConfig
from fabu.publishing.service import AccountChoice, Scope, catalogue
from fabu.publishing.topics import select_topic_group


class DefaultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Scope
    mode: Literal["immediate"] = "immediate"
    accounts: list[AccountChoice] = Field(min_length=1, max_length=3)
    topic_override: str | None = None


def get_defaults(app):
    with app.state.store.connection() as db:
        row = db.execute("SELECT * FROM publication_defaults WHERE singleton=1").fetchone()
    if row is None:
        return {
            "profile": None,
            "mode": "immediate",
            "declaration_source": None,
            "confirmed_at": None,
        }
    profiles = app.state.publication_profiles
    return {
        "profile": profiles.public(profiles.get(row["profile_id"])),
        "mode": row["mode"],
        "declaration_source": row["declaration_source"],
        "confirmed_at": row["confirmed_at"],
    }


def save_defaults(app, command):
    entries = {a.account_id: (a, c) for a, c in catalogue(app.state.store, command.scope)}
    settings = []
    for choice in command.accounts:
        if choice.account_id not in entries or not entries[choice.account_id][0].active:
            raise ValueError("Choose an active stable account ID from the current catalogue")
        account, category = entries[choice.account_id]
        settings.append(
            AccountPublishSettings(
                account=account,
                category=category,
                declaration=choice.declaration,
                description=choice.description,
            )
        )
    if (
        command.topic_override
        and select_topic_group("", command.topic_override).status != "selected"
    ):
        raise ValueError("Unknown topic override")
    config = PublicationConfig(accounts=tuple(settings), topic_override=command.topic_override)
    identifier, now = str(uuid4()), datetime.now(UTC).isoformat()
    with app.state.store.transaction() as db:
        db.execute(
            "INSERT INTO publication_profiles VALUES (?,?,?,?,?)",
            (identifier, "默认发布配置", command.scope, config.model_dump_json(), now),
        )
        db.execute(
            "INSERT INTO publication_defaults VALUES (1,?,'immediate','user_configured_default',?) "
            "ON CONFLICT(singleton) DO UPDATE SET profile_id=excluded.profile_id, "
            "confirmed_at=excluded.confirmed_at",
            (identifier, now),
        )
    return get_defaults(app)


def _choices(config):
    return [
        {
            "account_id": a.account.account_id,
            "declaration": a.declaration.model_dump(mode="json"),
            **({"description": a.description} if a.description is not None else {}),
        }
        for a in config.accounts
    ]


def form_for_job(app, job_id, scope=None):
    """Read without modifying a job; a saved declaration always beats a global default."""
    job = app.state.store.get(job_id)
    with app.state.store.connection() as db:
        draft = db.execute(
            "SELECT * FROM publication_drafts WHERE job_id=? AND content_revision=?",
            (job.id, job.content_revision),
        ).fetchone()
    row, source = None, "fallback"
    if draft and (scope is None or scope == draft["scope"]):
        row, source = draft, "draft"
    elif job.spec.publication_profile_id:
        profile = app.state.publication_profiles.get(job.spec.publication_profile_id)
        if scope is None or scope == profile["scope"]:
            row, source = profile, "profile"
    # A different scope is a new choice: never replace a saved declaration with the global one.
    if row is None and not draft and not job.spec.publication_profile_id:
        default = get_defaults(app)["profile"]
        if default and (scope is None or scope == default["scope"]):
            row, source = app.state.publication_profiles.get(default["id"]), "default"
    config = PublicationConfig.model_validate_json(row["config_json"]) if row else None
    resolved_scope = row["scope"] if row else (scope or "simulation")
    mode = job.spec.publish_mode if source in {"draft", "profile"} else "immediate"
    return {
        "source": source,
        "scope": resolved_scope,
        "mode": mode if mode in {"immediate", "native_schedule"} else "immediate",
        "scheduled_at_utc": job.spec.scheduled_at_utc if source in {"draft", "profile"} else None,
        "accounts": _choices(config) if config else [],
        "topic_override": config.topic_override if config else None,
        "topic_selection": select_topic_group(
            job.spec.title, config.topic_override if config else None
        ).model_dump(mode="json"),
    }


def get_default_preset(app):
    with app.state.store.connection() as db:
        row = db.execute(
            "SELECT preset_version_id FROM workflow_preset_default WHERE singleton=1"
        ).fetchone()
    return app.state.presets.get_version(row[0]) if row else app.state.default_preset


def set_default_preset(app, identifier):
    preset = app.state.presets.get_version(identifier)
    with app.state.store.transaction() as db:
        db.execute(
            "INSERT INTO workflow_preset_default VALUES (1,?) "
            "ON CONFLICT(singleton) DO UPDATE SET preset_version_id=excluded.preset_version_id",
            (preset.id,),
        )
    return {"preset_version_id": preset.id}
