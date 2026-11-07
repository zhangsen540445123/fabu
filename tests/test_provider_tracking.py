"""Read-only provider observation contracts; no fixture invokes an external CLI."""

import copy
import importlib
import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fabu.domain import JobSpec, TargetAttempt
from fabu.publishing.models import PublishSnapshot, publish_snapshot_digest
from fabu.store import JobStore


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 8, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds=30):
        self.now += timedelta(seconds=seconds)


def module():
    try:
        return importlib.import_module("fabu.publishing.provider_tracking")
    except ModuleNotFoundError:
        pytest.fail("ProviderTracker has not been implemented")


def details(*tasks, task_set="set-1", **extra):
    return {
        "id": task_set,
        "tasks": list(tasks),
        "page": 1,
        "totalPage": 1,
        "totalSize": len(tasks),
        **extra,
    }


def task(status="doing", platform="抖音", account=None, **extra):
    return {
        "id": "child-1",
        "platformName": platform,
        "platformAccountId": account,
        "stageStatus": status,
        **extra,
    }


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    store = JobStore(tmp_path / "state.sqlite3")
    package = tmp_path / "cli"
    for name in ("bin-native", "schemas", "references"):
        (package / name).mkdir(parents=True)
    (package / "bin-native/yxer.exe").write_bytes(b"not executable")
    (tmp_path / "publishing-connection.json").write_text(
        json.dumps({"package_root": str(package)}), encoding="utf-8"
    )
    calls = []
    state = SimpleNamespace(
        store=store, settings=SimpleNamespace(state_dir=tmp_path), settings_lock=threading.RLock()
    )
    context = SimpleNamespace(
        app=SimpleNamespace(state=state),
        store=store,
        clock=clock,
        calls=calls,
        response=details(task()),
        version="3.2.32",
        configured=True,
        config=None,
        cancel=threading.Event(),
    )

    class Credentials:
        def configured(self):
            return context.configured

        @contextmanager
        def temporary_cli_config(self):
            path = tmp_path / f"temporary-auth-{uuid4().hex}.json"
            path.write_text("synthetic credential", encoding="utf-8")
            context.config = path
            try:
                yield path
            finally:
                path.unlink()

    class ReadOnly:
        def query(self, operation):
            assert operation == "version"
            assert self.cancel is context.cancel
            calls.append("version")
            return {"version": context.version}

        def details(self, provider_task_id):
            assert context.config.is_file()
            assert state.settings_lock._is_owned()
            # Query must not hold a SQLite write transaction.
            with store.transaction():
                pass
            calls.append(("details", provider_task_id))
            if isinstance(context.response, Exception):
                raise context.response
            if callable(context.response):
                return context.response()
            return copy.deepcopy(context.response)

    def factory(executable, config, root):
        assert executable == package / "bin-native/yxer.exe"
        assert root == package and config == context.config and config.is_file()
        assert state.settings_lock._is_owned()
        return ReadOnly()

    state.credentials = Credentials()
    state.readonly_cli_factory = factory
    return context


def seed(
    env,
    *,
    scope="live",
    state="PROVIDER_ACCEPTED",
    provider="set-1",
    account="account-1",
    platform="douyin",
    linked=True,
    duration="120",
):
    job = env.store.create(
        JobSpec(
            source_url="https://youtu.be/abcdefghijk",
            title="fixture",
            preset_version_id="fixture",
            target_ids=[account],
            publish_mode="immediate",
        )
    )
    color = dict(space="bt709", transfer="bt709", primaries="bt709", range="tv")
    media = dict(
        path="output.mp4",
        sha256="a" * 64,
        duration=duration,
        video_stream_index=0,
        codec="h264",
        coded_width=320,
        coded_height=180,
        sar="1",
        rotation_deg=0,
        fps="30",
        pixel_format="yuv420p",
        color=color,
        start_time="0",
        audio=None,
    )
    # AssetRef is copied only as a frozen structural value; no files are uploaded.
    from fabu.assets import AssetRef

    cover = AssetRef(
        id="cover", sha256="b" * 64, size_bytes=1, media_type="image", local_path=Path("cover.png")
    ).model_dump(mode="json")
    snapshot = PublishSnapshot.model_validate(
        dict(
            job_id=job.id,
            content_revision=1,
            prepared_revision=1,
            video_id="abcdefghijk",
            source_url=job.spec.source_url,
            title=job.spec.title,
            topic={"status": "needs_selection", "reason": "no_match"},
            output=dict(
                path="output.mp4",
                sha256="a" * 64,
                size_bytes=1,
                media_info=media,
                plan_digest="c" * 64,
                verified_at=env.clock(),
            ),
            cover=cover,
            mode="immediate",
            scheduled_at_utc=None,
            targets=[
                dict(
                    account=dict(
                        account_id=account, platform=platform, nickname="fixture", active=True
                    ),
                    declaration=dict(origin="self_created", statement="none", confirmed=True),
                    form={},
                )
            ],
            created_at=env.clock(),
            digest="temporary",
        )
    )
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    run = "run-" + str(uuid4())
    attempt = TargetAttempt(
        job_id=job.id,
        revision=1,
        target_id=account,
        payload_digest=snapshot.digest,
        provider_task_id=provider,
        state=state,
    )
    env.store.record_attempt(attempt)
    with env.store.transaction() as db:
        db.execute(
            "INSERT INTO publication_runs(id,job_id,content_revision,snapshot_digest,"
            "snapshot_json,scope,state,created_at,updated_at) VALUES (?,?,?,?,?,?,'PAUSED',?,?)",
            (
                run,
                job.id,
                1,
                snapshot.digest,
                snapshot.model_dump_json(),
                scope,
                env.clock().isoformat(),
                env.clock().isoformat(),
            ),
        )
        if linked:
            db.execute(
                "INSERT INTO publication_attempt_links VALUES (?,?,0,NULL,0)", (attempt.id, run)
            )
    return SimpleNamespace(run=run, attempt=attempt, snapshot=snapshot, job=job)


def observe(env, seeded):
    tracker = module().ProviderTracker(env.app, clock=env.clock)
    assert tracker.process_due(env.cancel) is True
    assert env.calls == []  # First query is delayed by 30 seconds, not immediate.
    env.clock.advance()
    assert tracker.process_due(env.cancel) is True
    return module().observations_for_run(env.store, seeded.run)[seeded.attempt.target_id]


def test_discovers_live_attempt_and_waits_thirty_seconds(env):
    item = seed(env)
    tracker = module().ProviderTracker(env.app, clock=env.clock)
    assert tracker.process_due(env.cancel)
    observation = module().observations_for_run(env.store, item.run)["account-1"]
    assert observation == dict(
        status="pending",
        checked_at=None,
        next_check_at=(env.clock() + timedelta(seconds=30)).isoformat(),
        platform_content_id=None,
        reason="awaiting_provider_check",
        quality_status="unverified",
        expected_duration_s=120.0,
        observed_duration_s=None,
    )
    assert not tracker.process_due(env.cancel)
    env.clock.advance(29)
    assert not tracker.process_due(env.cancel)
    env.clock.advance(1)
    assert tracker.process_due(env.cancel)
    assert env.calls == ["version", ("details", "set-1")]
    assert not env.config.exists()


@pytest.mark.parametrize(
    "status,expected",
    [
        ("pending", "pending"),
        ("doing", "running"),
        ("running", "running"),
        ("success", "success"),
        ("failed", "failed"),
        ("unknown", "unknown"),
        ("new-unreviewed", "unknown"),
        (None, "unknown"),
    ],
)
def test_provider_status_does_not_change_attempt_run_or_job(env, status, expected):
    item = seed(env)
    with env.store.connection() as db:
        before = {
            name: [tuple(r) for r in db.execute(f"SELECT * FROM {name}")]
            for name in (
                "jobs",
                "target_attempts",
                "publication_runs",
                "events",
                "publication_attempt_links",
            )
        }
    env.response = details(task(status))
    observation = observe(env, item)
    assert observation["status"] == expected
    assert observation["quality_status"] == "unverified"
    assert observation["observed_duration_s"] is None
    assert (observation["next_check_at"] is None) == (expected in {"success", "failed"})
    with env.store.connection() as db:
        assert before == {
            name: [tuple(r) for r in db.execute(f"SELECT * FROM {name}")] for name in before
        }


@pytest.mark.parametrize("state", ["PROVIDER_ACCEPTED", "SUBMITTED", "SCHEDULED", "UNKNOWN"])
def test_all_trackable_states_are_discovered(env, state):
    item = seed(env, state=state)
    assert observe(env, item)["status"] == "running"


@pytest.mark.parametrize(
    "options",
    [
        {"scope": "simulation"},
        {"state": "PUBLISHED"},
        {"state": "FAILED"},
        {"state": "SUBMITTING"},
        {"provider": None},
        {"provider": ""},
        {"linked": False},
    ],
)
def test_ineligible_attempts_are_not_discovered(env, options):
    item = seed(env, **options)
    assert not module().ProviderTracker(env.app, env.clock).process_due(env.cancel)
    assert module().observations_for_run(env.store, item.run) == {}
    assert not env.calls


@pytest.mark.parametrize(
    "response",
    [
        details(task("success"), task("failed")),
        details(task("success", account="other-account")),
        details(task("success", platform="头条号")),
        details(task("success"), task_set="other-set"),
        details(task("success"), taskSetId="other-set"),
        details(task("success", taskSetId="other-set")),
        details(task("success"), totalPage=2),
        details(task("success"), totalSize=2),
        details(task("success"), page=2),
        details(task("success", platform="unknown")),
        details({"id": "child-1", "title": "fixture", "stageStatus": "success"}),
        details(),
        {},
        [],
        "not a response",
        {"tasks": [task("success")]},
        [{"id": "set-1", "platformName": "抖音", "stageStatus": "success"}],
        details(task("success"), tasks="not rows"),
    ],
)
def test_ambiguous_malformed_or_unbound_responses_stay_unknown(env, response):
    item = seed(env)
    env.response = response
    observation = observe(env, item)
    assert observation["status"] == "unknown"
    assert observation["platform_content_id"] is None
    assert observation["next_check_at"] is not None


@pytest.mark.parametrize(
    "response",
    [
        details(task("success", account="account-1"), task("failed", account="other-account")),
        details(task("success"), task("failed", platform="头条号")),
        [dict(task("success", account="account-1"), taskSetId="set-1")],
        dict(task("success", account="account-1"), taskSetId="set-1"),
    ],
)
def test_unique_platform_account_candidates_can_be_bound(env, response):
    item = seed(env)
    env.response = response
    assert observe(env, item)["status"] == "success"


@pytest.mark.parametrize(
    "tasks",
    [
        [task("success", account="account-1"), task("failed")],
        [task("success", account="account-1"), task("failed", account="account-1")],
        [task("success"), {"id": "other", "stageStatus": "failed"}],
    ],
)
def test_missing_identity_never_hides_a_second_possible_candidate(env, tasks):
    item = seed(env)
    env.response = details(*tasks)
    assert observe(env, item)["status"] == "unknown"


def test_provider_success_keeps_content_id_but_not_raw_private_fields(env):
    item = seed(env)
    secret = "SYNTHETIC_PRIVATE_MARKER"
    env.response = details(
        task(
            "success",
            publishId="BV1synthetic",
            title=secret,
            errorMessage=secret,
            platformAccountName=secret,
            openUrl="https://private.invalid/" + secret,
            coverUrl="https://private.invalid/" + secret,
            apiKey=secret,
            duration=2,
            videoDuration=2,
            upload={"duration": 2},
            documentId="internal-document",
        )
    )
    observation = observe(env, item)
    assert observation["platform_content_id"] == "BV1synthetic"
    assert observation["quality_status"] == "unverified"
    assert observation["observed_duration_s"] is None
    assert secret not in json.dumps(observation)
    with env.store.connection() as db:
        assert secret not in "\n".join(db.iterdump())


@pytest.mark.parametrize(
    "identifier",
    [
        "https://private.invalid/key",
        "x?token=secret",
        "../key",
        "x\nsecret",
        "x" * 257,
        123,
        True,
        {"key": "secret"},
    ],
)
def test_content_identity_is_whitelisted_not_copied_as_url(env, identifier):
    item = seed(env)
    env.response = details(task("success", publishId=identifier))
    assert observe(env, item)["platform_content_id"] is None


def test_failure_backoff_and_restart_are_persisted(env):
    item = seed(env)
    env.response = RuntimeError("https://private.invalid/?key=NEVER_STORE")
    first = observe(env, item)
    assert first["status"] == "unknown"
    assert first["reason"] == "provider_query_failed"
    assert first["next_check_at"] == (env.clock() + timedelta(seconds=60)).isoformat()
    tracker = module().ProviderTracker(env.app, env.clock)
    env.clock.advance(59)
    assert not tracker.process_due(env.cancel)
    env.clock.advance(1)
    assert tracker.process_due(env.cancel)
    second = module().observations_for_run(env.store, item.run)["account-1"]
    assert second["next_check_at"] == (env.clock() + timedelta(seconds=120)).isoformat()
    assert "NEVER_STORE" not in json.dumps(second)
    assert not env.config.exists()


@pytest.mark.parametrize("mode", ["errors", "polls", "elapsed"])
def test_tracking_is_bounded_and_restart_does_not_reset_limits(env, mode):
    item = seed(env)
    env.response = RuntimeError("temporary") if mode == "errors" else details(task())
    tracker = module().ProviderTracker(env.app, env.clock)
    tracker.process_due(env.cancel)
    for _ in range(130):
        value = module().observations_for_run(env.store, item.run)["account-1"]
        if value["next_check_at"] is None:
            break
        env.clock.now = datetime.fromisoformat(value["next_check_at"])
        if mode == "elapsed":
            env.clock.advance(24 * 60 * 60)
        tracker = module().ProviderTracker(env.app, env.clock)
        tracker.process_due(env.cancel)
    value = module().observations_for_run(env.store, item.run)["account-1"]
    assert value["status"] == "unknown" and value["next_check_at"] is None
    assert value["reason"] == "tracking_limit_reached"
    assert len(env.calls) <= (16 if mode == "errors" else 240)
    if mode == "elapsed":
        assert env.calls == []
    assert not tracker.process_due(env.cancel)


def test_terminal_observation_is_not_queried_again_after_restart(env):
    item = seed(env)
    env.response = details(task("success"))
    observe(env, item)
    before = list(env.calls)
    env.clock.advance(86400)
    assert not module().ProviderTracker(env.app, env.clock).process_due(env.cancel)
    assert env.calls == before


@pytest.mark.parametrize("failure", ["unconfigured", "version", "canceled"])
def test_preconditions_and_cancellation_never_issue_details(env, failure):
    item = seed(env)
    tracker = module().ProviderTracker(env.app, env.clock)
    if failure == "canceled":
        env.cancel.set()
        assert not tracker.process_due(env.cancel)
        assert module().observations_for_run(env.store, item.run) == {}
        return
    tracker.process_due(env.cancel)
    if failure == "unconfigured":
        env.configured = False
    else:
        env.version = "unreviewed"
    env.clock.advance()
    assert tracker.process_due(env.cancel)
    assert not any(isinstance(call, tuple) for call in env.calls)
    if env.config is not None:
        assert not env.config.exists()


def test_cancel_during_details_discards_even_success_and_resumes_later(env):
    item = seed(env)

    def cancel_query():
        env.cancel.set()
        return details(task("success"))

    env.response = cancel_query
    observation = observe(env, item)
    assert observation["status"] != "success"
    assert observation["next_check_at"] is not None
    assert not env.config.exists()


@pytest.mark.parametrize(
    "column,value",
    [("payload_digest", "different"), ("revision", 2), ("target_id", "unbound-account")],
)
def test_attempt_must_match_frozen_snapshot(env, column, value):
    item = seed(env)
    with env.store.transaction() as db:
        db.execute(f"UPDATE target_attempts SET {column}=? WHERE id=?", (value, item.attempt.id))
    tracker = module().ProviderTracker(env.app, env.clock)
    tracker.process_due(env.cancel)
    env.clock.advance()
    tracker.process_due(env.cancel)
    assert env.calls == []
    observation = module().observations_for_run(env.store, item.run)
    assert not observation or all(v["status"] == "unknown" for v in observation.values())


def test_tampered_snapshot_never_becomes_query_binding(env):
    item = seed(env)
    raw = item.snapshot.model_dump(mode="json")
    raw["targets"][0]["account"]["platform"] = "toutiao"
    with env.store.transaction() as db:
        db.execute(
            "UPDATE publication_runs SET snapshot_json=? WHERE id=?", (json.dumps(raw), item.run)
        )
    tracker = module().ProviderTracker(env.app, env.clock)
    tracker.process_due(env.cancel)
    env.clock.advance()
    tracker.process_due(env.cancel)
    assert not env.calls


def test_read_api_is_pure_and_unknown_run_is_empty(env):
    item = seed(env)
    with env.store.connection() as db:
        before = db.execute("SELECT total_changes()").fetchone()[0]
    assert module().observations_for_run(env.store, item.run) == {}
    assert module().observations_for_run(env.store, "does-not-exist") == {}
    assert env.calls == [] and before == 0


def test_shared_task_set_with_two_possible_local_accounts_is_not_guessed(env):
    first = seed(env, account="account-1")
    second = seed(env, account="account-2")
    tracker = module().ProviderTracker(env.app, env.clock)
    assert tracker.process_due(env.cancel)
    env.clock.advance()
    while tracker.process_due(env.cancel):
        pass
    for item in (first, second):
        observation = module().observations_for_run(env.store, item.run)[item.attempt.target_id]
        assert observation["status"] == "unknown"
        assert observation["reason"] == "provider_binding_ambiguous"


def test_explicit_account_disambiguates_shared_task_set(env):
    first = seed(env, account="account-1")
    second = seed(env, account="account-2")
    env.response = details(
        task("success", account="account-1"), task("failed", account="account-2")
    )
    tracker = module().ProviderTracker(env.app, env.clock)
    tracker.process_due(env.cancel)
    env.clock.advance()
    while tracker.process_due(env.cancel):
        pass
    assert module().observations_for_run(env.store, first.run)["account-1"]["status"] == "success"
    assert module().observations_for_run(env.store, second.run)["account-2"]["status"] == "failed"


@pytest.mark.parametrize("provider", ["https://private.invalid/secret", "--publish", "x\nsecret"])
def test_invalid_task_id_is_not_duplicated_into_observations_or_queried(env, provider):
    item = seed(env, provider=provider)
    tracker = module().ProviderTracker(env.app, env.clock)
    tracker.process_due(env.cancel)
    env.clock.advance()
    tracker.process_due(env.cancel)
    assert not env.calls
    assert module().observations_for_run(env.store, item.run) == {}
    with env.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM provider_observations").fetchone()[0] == 0


def test_cancellation_while_waiting_for_settings_lock_issues_no_query(env):
    seed(env)
    tracker = module().ProviderTracker(env.app, env.clock)
    tracker.process_due(env.cancel)
    env.clock.advance()
    original_lock = env.app.state.settings_lock

    class CancelingLock:
        def __enter__(self):
            original_lock.acquire()
            env.cancel.set()

        def __exit__(self, *args):
            original_lock.release()

        def _is_owned(self):
            return original_lock._is_owned()

    env.app.state.settings_lock = CancelingLock()
    assert tracker.process_due(env.cancel)
    assert env.calls == []
    assert env.config is None


def test_cancel_after_version_never_issues_details(env):
    seed(env)
    tracker = module().ProviderTracker(env.app, env.clock)
    tracker.process_due(env.cancel)
    factory = env.app.state.readonly_cli_factory

    def cancel_factory(*args):
        client = factory(*args)
        query = client.query

        def canceled(operation):
            result = query(operation)
            env.cancel.set()
            return result

        client.query = canceled
        return client

    env.app.state.readonly_cli_factory = cancel_factory
    env.clock.advance()
    tracker.process_due(env.cancel)
    assert env.calls == ["version"]
    assert not env.config.exists()


def test_two_workers_do_not_query_the_same_due_attempt(env):
    item = seed(env)
    tracker = module().ProviderTracker(env.app, env.clock)
    tracker.process_due(env.cancel)
    env.clock.advance()
    entered, release = threading.Event(), threading.Event()
    results = []

    def blocked_query():
        entered.set()
        assert release.wait(timeout=5)
        return details(task("success"))

    env.response = blocked_query
    thread = threading.Thread(target=lambda: results.append(tracker.process_due(env.cancel)))
    thread.start()
    try:
        assert entered.wait(timeout=5)
        assert not module().ProviderTracker(env.app, env.clock).process_due(env.cancel)
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive() and results == [True]
    assert env.calls == ["version", ("details", "set-1")]
    assert module().observations_for_run(env.store, item.run)["account-1"]["status"] == "success"


def test_crashed_query_has_durable_lease_and_count_after_database_reopen(env):
    item = seed(env)
    tracker = module().ProviderTracker(env.app, env.clock)
    tracker.process_due(env.cancel)
    env.clock.advance()

    def crash():
        raise SystemExit("synthetic process crash")

    env.response = crash
    with pytest.raises(SystemExit):
        tracker.process_due(env.cancel)
    assert not env.config.exists()
    env.store = JobStore(env.store.path)
    env.app.state.store = env.store
    tracker = module().ProviderTracker(env.app, env.clock)
    env.clock.advance(89)
    assert not tracker.process_due(env.cancel)
    env.clock.advance(1)
    env.response = details(task("success"))
    assert tracker.process_due(env.cancel)
    value = module().observations_for_run(env.store, item.run)["account-1"]
    assert value["status"] == "success"
    with env.store.connection() as db:
        row = db.execute("SELECT check_count FROM provider_observations").fetchone()
        assert row["check_count"] == 2
        assert (
            db.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name=?",
                ("011_provider_observations.sql",),
            ).fetchone()[0]
            == 1
        )


def test_stale_inflight_response_cannot_overwrite_newer_claim(env):
    item = seed(env)

    def stale_query():
        env.clock.advance(module().CLAIM_TIMEOUT_S + 1)
        env.response = details(task("success"))
        assert module().ProviderTracker(env.app, env.clock).process_due(env.cancel)
        return details(task("failed"))

    env.response = stale_query
    assert observe(env, item)["status"] == "success"


def test_changed_task_identity_during_query_does_not_get_old_success(env):
    item = seed(env)

    def mutate():
        with env.store.transaction() as db:
            db.execute(
                "UPDATE target_attempts SET provider_task_id='new-set' WHERE id=?",
                (item.attempt.id,),
            )
        return details(task("success"))

    env.response = mutate
    tracker = module().ProviderTracker(env.app, env.clock)
    tracker.process_due(env.cancel)
    env.clock.advance()
    tracker.process_due(env.cancel)
    assert module().observations_for_run(env.store, item.run) == {}
    with env.store.connection() as db:
        row = db.execute("SELECT * FROM provider_observations").fetchone()
    assert row["status"] == "unknown" and row["next_check_at"] is None


def test_successful_read_resets_error_backoff(env):
    item = seed(env)
    env.response = RuntimeError("temporary")
    observe(env, item)
    tracker = module().ProviderTracker(env.app, env.clock)
    env.clock.advance(60)
    env.response = details(task())
    tracker.process_due(env.cancel)
    value = module().observations_for_run(env.store, item.run)["account-1"]
    assert value["next_check_at"] == (env.clock() + timedelta(seconds=30)).isoformat()
    env.clock.advance(30)
    env.response = RuntimeError("temporary again")
    tracker.process_due(env.cancel)
    value = module().observations_for_run(env.store, item.run)["account-1"]
    assert value["next_check_at"] == (env.clock() + timedelta(seconds=60)).isoformat()


def test_process_due_performs_at_most_one_query_per_call(env):
    items = [seed(env, provider=f"set-{i}", account=f"account-{i}") for i in range(1, 4)]
    tracker = module().ProviderTracker(env.app, env.clock)
    tracker.process_due(env.cancel)
    assert not env.calls
    env.clock.advance()
    for count in range(1, 4):
        assert tracker.process_due(env.cancel)
        assert len(env.calls) == count * 2
    assert not tracker.process_due(env.cancel)
    for item in items:
        assert module().observations_for_run(env.store, item.run)


@pytest.mark.parametrize("duration", ["0", "-3", "240/2"])
def test_invalid_or_fractional_expected_duration_is_safe(env, duration):
    item = seed(env, duration=duration)
    value = observe(env, item)
    assert value["expected_duration_s"] == (120.0 if duration == "240/2" else None)
    assert value["observed_duration_s"] is None


def test_missing_snapshot_duration_yields_unknown_with_null_durations(env):
    item = seed(env)
    raw = item.snapshot.model_dump(mode="json")
    del raw["output"]["media_info"]["duration"]
    with env.store.transaction() as db:
        db.execute(
            "UPDATE publication_runs SET snapshot_json=? WHERE id=?", (json.dumps(raw), item.run)
        )
    tracker = module().ProviderTracker(env.app, env.clock)
    tracker.process_due(env.cancel)
    value = module().observations_for_run(env.store, item.run)["account-1"]
    assert value["status"] == "unknown"
    assert value["expected_duration_s"] is None and value["observed_duration_s"] is None
    assert value["next_check_at"] is None and not env.calls


def test_latest_linked_attempt_never_reuses_older_success(env):
    item = seed(env)
    env.response = details(task("success"))
    observe(env, item)
    newer = TargetAttempt(
        job_id=item.job.id,
        revision=1,
        target_id="account-1",
        payload_digest=item.snapshot.digest,
        state="UNKNOWN",
    )
    env.store.record_attempt(newer)
    with env.store.transaction() as db:
        db.execute(
            "INSERT INTO publication_attempt_links VALUES (?,?,1,NULL,0)", (newer.id, item.run)
        )
    assert module().observations_for_run(env.store, item.run) == {}


def test_overlarge_or_conflicting_provider_data_is_unknown(env):
    item = seed(env)
    tracker = module().ProviderTracker(env.app, env.clock)
    tracker.process_due(env.cancel)
    for response in (
        details(*([task()] * 1001)),
        details(dict(task("success"), status="failed")),
        details(task("success", platform="toutiao")),
        details(task("success", account="account-1", accountId="other")),
        details(task(["success"])),
        details(task("success"), totalPage=True),
    ):
        env.response = response
        value = module().observations_for_run(env.store, item.run)["account-1"]
        env.clock.now = datetime.fromisoformat(value["next_check_at"])
        tracker.process_due(env.cancel)
        value = module().observations_for_run(env.store, item.run)["account-1"]
        assert value["status"] == "unknown"


def test_no_work_requires_no_credentials_or_cli_factory(env):
    del env.app.state.credentials
    del env.app.state.readonly_cli_factory
    assert not module().ProviderTracker(env.app, env.clock).process_due(env.cancel)


def test_clock_must_be_aware_and_is_normalized_to_utc(env):
    seed(env)
    env.clock.now = env.clock.now.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        module().ProviderTracker(env.app, env.clock).process_due(env.cancel)


def test_manual_refresh_scopes_discovery_to_existing_live_run(env):
    first = seed(env)
    other = seed(env, provider="set-2", account="other")
    simulation = seed(env, scope="simulation", provider="set-3", account="simulated")
    tracker = module().ProviderTracker(env.app, env.clock)
    assert callable(getattr(tracker, "refresh_run", None)), "Manual refresh is not implemented"
    assert tracker.refresh_run(first.run, env.cancel) is None
    assert module().observations_for_run(env.store, first.run)["account-1"]["status"] == "pending"
    assert module().observations_for_run(env.store, other.run) == {}
    assert module().observations_for_run(env.store, simulation.run) == {}
    assert not env.calls
    env.clock.advance()
    assert tracker.refresh_run(first.run, env.cancel) is None
    assert env.calls == ["version", ("details", "set-1")]
    tracker.refresh_run("does-not-exist", env.cancel)
    tracker.refresh_run(simulation.run, env.cancel)
    assert len(env.calls) == 2


def test_manual_refresh_cannot_bypass_minimum_interval_or_backoff(env):
    item = seed(env)
    tracker = module().ProviderTracker(env.app, env.clock)
    assert callable(getattr(tracker, "refresh_run", None)), "Manual refresh is not implemented"
    env.response = RuntimeError("transient")
    tracker.refresh_run(item.run, env.cancel)
    for _ in range(5):
        tracker.refresh_run(item.run, env.cancel)
    assert not env.calls
    env.clock.advance()
    tracker.refresh_run(item.run, env.cancel)
    assert len(env.calls) == 2
    env.clock.advance(30)
    tracker.refresh_run(item.run, env.cancel)
    assert len(env.calls) == 2
    env.clock.advance(30)
    tracker.refresh_run(item.run, env.cancel)
    assert len(env.calls) == 4


def test_manual_refresh_checks_all_three_due_targets_without_changing_old_state(env):
    item = seed(env)
    snapshot = item.snapshot
    from fabu.publishing.models import AccountBinding

    targets = tuple(
        snapshot.targets[0].model_copy(
            update={
                "account": AccountBinding(
                    account_id=f"account-{index}",
                    platform=platform,
                    nickname="fixture",
                    active=True,
                )
            }
        )
        for index, platform in enumerate(("douyin", "toutiao", "bilibili"), 1)
    )
    snapshot = snapshot.model_copy(update={"targets": targets})
    snapshot = snapshot.model_copy(update={"digest": publish_snapshot_digest(snapshot)})
    with env.store.transaction() as db:
        db.execute(
            "UPDATE publication_runs SET snapshot_digest=?,snapshot_json=?,"
            "reason='provider_tracking_pending' WHERE id=?",
            (snapshot.digest, snapshot.model_dump_json(), item.run),
        )
        db.execute(
            "UPDATE target_attempts SET payload_digest=? WHERE id=?",
            (snapshot.digest, item.attempt.id),
        )
    with env.store.transaction() as db:
        spec = item.job.spec.model_copy(
            update={"target_ids": ["account-1", "account-2", "account-3"]}
        )
        db.execute("UPDATE jobs SET spec_json=? WHERE id=?", (spec.model_dump_json(), item.job.id))
    for index in (2, 3):
        attempt = TargetAttempt(
            job_id=item.job.id,
            revision=1,
            target_id=f"account-{index}",
            payload_digest=snapshot.digest,
            provider_task_id="set-1",
            state="PROVIDER_ACCEPTED",
        )
        env.store.record_attempt(attempt)
        with env.store.transaction() as db:
            db.execute(
                "INSERT INTO publication_attempt_links VALUES (?,?,?,NULL,0)",
                (attempt.id, item.run, index - 1),
            )
    with env.store.connection() as db:
        before = {
            table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
            for table in ("jobs", "publication_runs", "target_attempts")
        }
    env.response = details(
        task("success"), task("success", platform="头条号"), task("success", platform="哔哩哔哩")
    )
    tracker = module().ProviderTracker(env.app, env.clock)
    assert callable(getattr(tracker, "refresh_run", None)), "Manual refresh is not implemented"
    tracker.refresh_run(item.run, env.cancel)
    env.clock.advance()
    tracker.refresh_run(item.run, env.cancel)
    observations = module().observations_for_run(env.store, item.run)
    assert len(observations) == 3 and len(env.calls) == 6
    assert all(value["status"] == "success" for value in observations.values())
    tracker.refresh_run(item.run, env.cancel)
    assert len(env.calls) == 6
    with env.store.connection() as db:
        assert before == {
            table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in before
        }


def test_manual_refresh_cannot_reset_exhausted_limits_or_cancellation(env):
    item = seed(env)
    tracker = module().ProviderTracker(env.app, env.clock)
    assert callable(getattr(tracker, "refresh_run", None)), "Manual refresh is not implemented"
    env.cancel.set()
    assert tracker.refresh_run(item.run, env.cancel) is None
    assert module().observations_for_run(env.store, item.run) == {}
    env.cancel.clear()
    tracker.refresh_run(item.run, env.cancel)
    env.clock.advance(module().MAX_TRACKING_S)
    tracker.refresh_run(item.run, env.cancel)
    for _ in range(3):
        module().ProviderTracker(env.app, env.clock).refresh_run(item.run, env.cancel)
    value = module().observations_for_run(env.store, item.run)["account-1"]
    assert value["status"] == "unknown" and value["next_check_at"] is None
    assert value["reason"] == "tracking_limit_reached" and not env.calls
