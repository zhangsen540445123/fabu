"""Compare selectable templates without mutating historical publication evidence."""

import json

from fabu.publishing.models import PublicationConfig


def profile_key(name, scope, config):
    data = config.model_dump(mode="json")
    for setting in data["accounts"]:
        if setting["category"] is not None:
            # Observation time is not user intent. Retain the complete category path,
            # declarations and account order; execution rechecks the current catalogue.
            setting["category"].pop("queried_at")
    return name, scope, json.dumps(data, sort_keys=True, ensure_ascii=False)


def row_key(row):
    return profile_key(
        row["name"], row["scope"], PublicationConfig.model_validate_json(row["config_json"])
    )


def ordered_profiles(db):
    """Prefer the current default, then the latest snapshot, in one consistent read."""
    return db.execute(
        "SELECT p.* FROM publication_profiles p ORDER BY "
        "(p.id=COALESCE((SELECT profile_id FROM publication_defaults WHERE singleton=1),'')) "
        "DESC, p.rowid DESC"
    ).fetchall()
