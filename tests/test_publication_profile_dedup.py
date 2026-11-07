"""Duplicate profiles are hidden/reused without changing historical snapshots."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import pytest
import test_publication_defaults as fixtures
from test_api_security import login
from test_workflow import workflow

from fabu.publishing.defaults import DefaultsRequest, save_defaults

publishing_context = fixtures.publishing_context
publish_app = fixtures.publish_app


def rows(app):
    with app.state.store.connection() as db:
        return [dict(row) for row in db.execute("SELECT * FROM publication_profiles")]


def clone(app, original, *, name=None, scope=None, config=None):
    identifier = str(uuid4())
    with app.state.store.transaction() as db:
        db.execute(
            "INSERT INTO publication_profiles VALUES (?,?,?,?,?)",
            (
                identifier,
                name or original["name"],
                scope or original["scope"],
                json.dumps(config) if config is not None else original["config_json"],
                original["created_at"],
            ),
        )
    return identifier


def test_repeated_default_save_reuses_id_and_preserves_snapshot(publish_app):
    client, headers = login(publish_app)
    first, body = fixtures.save_defaults(publish_app, client, headers)
    before = rows(publish_app)
    repeated = client.put("/api/publishing/defaults", json=body, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()["profile"]["id"] == first["profile"]["id"]
    assert rows(publish_app) == before
    assert len(client.get("/api/publish/profiles").json()) == 1


def test_category_observation_refresh_does_not_create_template(publish_app):
    client, headers = login(publish_app)
    first, body = fixtures.save_defaults(publish_app, client, headers)
    before = rows(publish_app)
    with publish_app.state.store.transaction() as db:
        entry = db.execute(
            "SELECT * FROM publication_catalog WHERE scope='live' AND category_json IS NOT NULL"
        ).fetchone()
        category = json.loads(entry["category_json"])
        category["queried_at"] = "2026-11-08T12:00:00+00:00"
        db.execute(
            "UPDATE publication_catalog SET category_json=? WHERE scope='live' AND account_id=?",
            (json.dumps(category), entry["account_id"]),
        )
    result = client.put("/api/publishing/defaults", json=body, headers=headers)
    assert result.status_code == 200
    assert result.json()["profile"]["id"] == first["profile"]["id"]
    assert rows(publish_app) == before


def test_list_prefers_current_default_and_preserves_historical_job(publish_app, publishing_context):
    client, headers = login(publish_app)
    defaults, body = fixtures.save_defaults(publish_app, client, headers)
    original = rows(publish_app)[0]
    duplicate_id = clone(publish_app, original)
    created = client.post(
        "/api/workflow/jobs", json=workflow(publishing_context, duplicate_id), headers=headers
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]
    before_job = publish_app.state.store.get(job_id)
    before_rows = rows(publish_app)
    visible = client.get("/api/publish/profiles").json()
    assert [p["id"] for p in visible] == [defaults["profile"]["id"]]
    repeated = client.put("/api/publishing/defaults", json=body, headers=headers)
    assert repeated.json()["profile"]["id"] == defaults["profile"]["id"]
    assert (
        publish_app.state.publication_profiles.get(duplicate_id)["config_json"]
        == original["config_json"]
    )
    assert publish_app.state.store.get(job_id) == before_job
    assert rows(publish_app) == before_rows
    assert not publish_app.state.store.attempts(job_id)


def test_switching_back_to_existing_defaults_reuses_profile(publish_app):
    client, headers = login(publish_app)
    original, body = fixtures.save_defaults(publish_app, client, headers)
    changed = deepcopy(body)
    changed["accounts"] = changed["accounts"][:1]
    other = client.put("/api/publishing/defaults", json=changed, headers=headers).json()
    assert other["profile"]["id"] != original["profile"]["id"]
    restored = client.put("/api/publishing/defaults", json=body, headers=headers).json()
    assert restored["profile"]["id"] == original["profile"]["id"]
    assert len(rows(publish_app)) == 2
    assert len(client.get("/api/publish/profiles").json()) == 2


@pytest.mark.parametrize(
    "change",
    ["name", "scope", "accounts", "order", "declaration", "topic", "description", "category"],
)
def test_list_keeps_distinct_intent_even_with_same_display_name(publish_app, change):
    client, headers = login(publish_app)
    defaults, _ = fixtures.save_defaults(publish_app, client, headers)
    original = rows(publish_app)[0]
    config = json.loads(original["config_json"])
    options = {}
    if change == "name":
        options["name"] = "Explicitly named profile"
    elif change == "scope":
        options["scope"] = "simulation"
    elif change == "accounts":
        config["accounts"] = config["accounts"][:1]
    elif change == "order":
        config["accounts"].reverse()
    elif change == "declaration":
        config["accounts"][0]["declaration"]["origin"] = "licensed_reuse"
    elif change == "topic":
        config["topic_override"] = "2aZjVP8u"
    elif change == "description":
        config["accounts"][0]["description"] = "A different description"
    elif change == "category":
        category = next(a["category"] for a in config["accounts"] if a["category"])
        category["path"][0]["raw"]["test_changed_category"] = True
    other = clone(publish_app, original, config=config, **options)
    visible = client.get("/api/publish/profiles").json()
    assert {p["id"] for p in visible} == {defaults["profile"]["id"], other}
    assert len(rows(publish_app)) == 2


def test_without_defaults_list_uses_latest_duplicate_but_keeps_rows(publish_app):
    client, headers = login(publish_app)
    fixtures.save_defaults(publish_app, client, headers)
    original = rows(publish_app)[0]
    newer = clone(publish_app, original)
    with publish_app.state.store.transaction() as db:
        db.execute("DELETE FROM publication_defaults")
    before = rows(publish_app)
    assert [p["id"] for p in client.get("/api/publish/profiles").json()] == [newer]
    assert rows(publish_app) == before


def test_concurrent_identical_saves_reuse_one_profile(publish_app):
    body = fixtures.live_defaults(publish_app)
    command = DefaultsRequest.model_validate(body)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: save_defaults(publish_app, command), range(4)))
    assert len({result["profile"]["id"] for result in results}) == 1
    assert len(rows(publish_app)) == 1
