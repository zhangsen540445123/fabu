"""Local publication drafts and safe projections. Browser data is never platform evidence."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fabu.domain import JobSpec
from fabu.media.service import require_approved_job_output
from fabu.publishing.models import (
    AccountBinding,
    AccountPublishSettings,
    CategoryEvidence,
    PublicationConfig,
    PublishSnapshot,
    SourceDeclaration,
)
from fabu.publishing.topics import select_topic_group

Scope = Literal["simulation", "live"]


class AccountChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=128)
    declaration: SourceDeclaration
    description: str | None = Field(default=None, max_length=2000)


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    scope: Scope = "simulation"
    mode: Literal["immediate", "native_schedule"]
    scheduled_at_utc: datetime | None = None
    accounts: list[AccountChoice] = Field(min_length=1, max_length=3)
    topic_override: str | None = None


def require_simulation(scope):
    # P05/P06 must install a scope-bound verified policy; no web flag opens a real gateway.
    if scope != "simulation":
        raise ValueError("真实发布尚未通过免费额度与平台能力验收；当前只能本地仿真")


def catalogue(store, scope):
    if scope == "simulation":
        now = datetime.now(UTC)
        return [
            (
                AccountBinding(
                    account_id=f"sim-{platform}", platform=platform, nickname=nickname, active=True
                ),
                CategoryEvidence(
                    account_id="sim-bilibili",
                    queried_at=now,
                    path=(
                        {
                            "yixiaoerId": "simulation-life",
                            "yixiaoerName": "生活经验",
                            "raw": {"simulation": True},
                        },
                    ),
                )
                if platform == "bilibili"
                else None,
            )
            for platform, nickname in [
                ("douyin", "ASMR · 仿真"),
                ("toutiao", "ASMR · 仿真"),
                ("bilibili", "枕畔慢时光 · 仿真"),
            ]
        ]
    with store.connection() as db:
        rows = db.execute(
            "SELECT account_json, category_json FROM publication_catalog WHERE scope=? ORDER BY "
            "account_id",
            (scope,),
        ).fetchall()
    return [
        (
            AccountBinding.model_validate_json(r["account_json"]),
            CategoryEvidence.model_validate_json(r["category_json"])
            if r["category_json"]
            else None,
        )
        for r in rows
    ]


def busy(db, job_id):
    return bool(
        db.execute(
            "SELECT 1 FROM publication_commands WHERE job_id=? AND state IN ('QUEUED','RUNNING') "
            "UNION ALL SELECT 1 FROM media_operations WHERE job_id=? AND state IN ('QUEUED', "
            "'RUNNING')",
            (job_id, job_id),
        ).fetchone()
    )


def save_draft(app, job_id, command):
    store = app.state.store
    job = store.get(job_id)
    if job.revision != command.expected_revision or job.content_frozen or job.owner_worker_id:
        raise ValueError("任务已变化或已开始提交；请刷新状态，不可修改冻结内容")
    require_approved_job_output(store, app.state.presets, app.state.approvals, job)
    spec = JobSpec.model_validate(
        {
            **job.spec.model_dump(),
            # Explicit task editing supersedes automatic immutable-profile intent.
            "publication_profile_id": None,
            "target_ids": [a.account_id for a in command.accounts],
            "publish_mode": command.mode,
            "scheduled_at_utc": command.scheduled_at_utc,
        }
    )
    if spec.scheduled_at_utc and spec.scheduled_at_utc <= datetime.now(UTC):
        raise ValueError("预约时间已过期；不会自动转为立即发布")
    topic = select_topic_group(spec.title, command.topic_override)
    if topic.status != "selected":
        raise ValueError("标题未唯一命中话题组，请手动选择")
    entries = {
        account.account_id: (account, category)
        for account, category in catalogue(store, command.scope)
    }
    settings = []
    for choice in command.accounts:
        if choice.account_id not in entries or not entries[choice.account_id][0].active:
            raise ValueError("请选择当前目录中有效的稳定账号ID")
        account, category = entries[choice.account_id]
        settings.append(
            AccountPublishSettings(
                account=account,
                category=category,
                declaration=choice.declaration,
                description=choice.description,
            )
        )
    config = PublicationConfig(accounts=tuple(settings), topic_override=command.topic_override)
    now = datetime.now(UTC).isoformat()
    with store.transaction() as db:
        current = store._snapshot(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        if current != job or busy(db, job_id):
            raise ValueError("任务正在处理或已发生变化，请刷新后再配置")
        if db.execute(
            "SELECT 1 FROM publication_attempt_links l JOIN target_attempts a ON "
            "a.id=l.attempt_id WHERE a.job_id=?",
            (job_id,),
        ).fetchone():
            raise ValueError("此任务已有提交尝试；先查看平台状态，不能用修改内容绕过对账")
        revision = job.content_revision + 1
        # Only publication metadata changes: source/preset/media are unchanged. Clone the
        # immutable processing snapshot, not a newly compiled or implicitly approved plan.
        result = db.execute(
            "INSERT INTO processing_snapshots SELECT job_id, ?, preset_version_id, source_json, "
            "plan_json, ? "
            "FROM processing_snapshots WHERE job_id=? AND content_revision=?",
            (revision, now, job_id, job.content_revision),
        )
        if result.rowcount != 1:
            raise ValueError("已验证媒体快照缺失")
        db.execute(
            "UPDATE jobs SET spec_json=?, content_revision=?, revision=revision+1, updated_at=? "
            "WHERE id=?",
            (spec.model_dump_json(), revision, now, job_id),
        )
        db.execute(
            "INSERT INTO job_revisions VALUES (?,?,?,?)",
            (job_id, revision, spec.model_dump_json(), now),
        )
        db.execute(
            "INSERT INTO publication_drafts VALUES (?, ?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE"
            " SET content_revision=excluded.content_revision, scope=excluded.scope, "
            "config_json=excluded.config_json, updated_at=excluded.updated_at",
            (job_id, revision, command.scope, config.model_dump_json(), now),
        )
    return store.get(job_id)


def load_config(store, job, scope):
    with store.connection() as db:
        row = db.execute(
            "SELECT * FROM publication_drafts WHERE job_id=? AND scope=? AND content_revision=?",
            (job.id, scope, job.content_revision),
        ).fetchone()
    if row is None:
        raise ValueError("请先保存本任务的账号、来源声明和发布方式")
    return PublicationConfig.model_validate_json(row["config_json"])


def latest_run(store, job_id, scope):
    with store.connection() as db:
        row = db.execute(
            "SELECT * FROM publication_runs WHERE job_id=? AND scope=? ORDER BY rowid DESC LIMIT 1",
            (job_id, scope),
        ).fetchone()
    return dict(row) if row else None


def public_run(store, run, local_policy=None):
    snapshot = PublishSnapshot.model_validate_json(run["snapshot_json"])
    job = store.get(snapshot.job_id)
    with store.connection() as db:
        source_conflict = bool(
            db.execute(
                "SELECT 1 FROM publication_source_claims WHERE scope=? AND video_id=? "
                "AND run_id<>? AND target_id IN (" + ",".join("?" for _ in snapshot.targets) + ")",
                (
                    run["scope"],
                    snapshot.video_id,
                    run["id"],
                    *[t.account.account_id for t in snapshot.targets],
                ),
            ).fetchone()
        )
        resource_blocked = bool(
            db.execute(
                "SELECT 1 FROM upload_attempts WHERE scope=? AND source_video_id=? "
                "AND state IN ('SUBMITTING','UNKNOWN')",
                (run["scope"], snapshot.video_id),
            ).fetchone()
        )
        rows = db.execute(
            "SELECT a.*, l.definitely_not_submitted FROM target_attempts a JOIN "
            "publication_attempt_links l ON a.id=l.attempt_id WHERE l.run_id=? ORDER BY a.rowid",
            (run["id"],),
        ).fetchall()
        command = db.execute(
            "SELECT state, error_code FROM publication_commands WHERE run_id=? ORDER BY rowid "
            "DESC LIMIT 1",
            (run["id"],),
        ).fetchone()
    from fabu.publishing.provider_tracking import observations_for_run

    observations = observations_for_run(store, run["id"]) if run["scope"] == "live" else {}
    last = {row["target_id"]: row for row in rows}
    targets = []
    for target in snapshot.targets:
        attempt = last.get(target.account.account_id)
        targets.append(
            {
                "account_id": target.account.account_id,
                "platform": target.account.platform,
                "nickname": target.account.nickname,
                "provider_observation": observations.get(target.account.account_id),
                "state": attempt["state"] if attempt else "PENDING",
                "can_retry": bool(
                    attempt
                    and attempt["state"] == "FAILED"
                    and attempt["definitely_not_submitted"]
                    and sum(r["target_id"] == target.account.account_id for r in rows) < 3
                ),
                "category": [n["yixiaoerName"] for n in target.category_evidence.path]
                if target.category_evidence
                else [],
            }
        )
    needs_reconcile = any(
        t["state"] in {"SUBMITTING", "PROVIDER_ACCEPTED", "UNKNOWN"} for t in targets
    )
    failed_without_retry = any(t["state"] == "FAILED" and not t["can_retry"] for t in targets)
    active = bool(command and command["state"] in {"QUEUED", "RUNNING"})
    from fabu.publishing.progress import summary_reason

    reason = summary_reason(
        run["reason"], observations, [t.account.account_id for t in snapshot.targets]
    )
    return {
        "job_id": snapshot.job_id,
        "content_revision": snapshot.content_revision,
        "snapshot_digest": snapshot.digest,
        "scope": run["scope"],
        "state": run["state"],
        "reason": reason,
        "resource_blocked": resource_blocked,
        "source_conflict": source_conflict,
        "title": snapshot.title,
        "mode": snapshot.mode,
        "scheduled_at_utc": snapshot.scheduled_at_utc,
        "timezone": "Asia/Shanghai",
        "topic": snapshot.topic.model_dump(mode="json"),
        "targets": targets,
        "can_submit": job.state in {"READY", "PAUSED"}
        and job.content_revision == snapshot.content_revision
        and (snapshot.scheduled_at_utc is None or snapshot.scheduled_at_utc > datetime.now(UTC))
        and (
            run["scope"] == "simulation"
            or (local_policy is not None and local_policy.is_authorized(job.id))
        )
        and run["state"] in {"NEW", "PAUSED"}
        and not active
        and not needs_reconcile
        and not resource_blocked
        and not source_conflict
        and not failed_without_retry,
        "can_reconcile": (
            needs_reconcile or any(t["state"] in {"SUBMITTED", "SCHEDULED"} for t in targets)
        )
        and not active,
        "command": dict(command) if command else None,
    }
