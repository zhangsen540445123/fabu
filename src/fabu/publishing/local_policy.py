"""Explicit local job authority, not fabricated external capability/entitlement evidence.

Only a trusted local setup action enables this policy. New automatic workflow creation
records immutable intent; old jobs never inherit it. Reservations are a conservative
local cap, NOT the provider's remaining quota. No payment or cloud publishing API exists.
"""

import hashlib
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fabu.assets import sha256_file
from fabu.launcher import _read_private, _write_private
from fabu.publishing.connection import _package, connection_status
from fabu.publishing.models import CLI_CONTRACT_VERSION

# Reviewed installed 3.2.32 Windows executable; update only after contract review.
REVIEWED_CLI_SHA256 = "947a3bd1e68546707e75865328151e7f8b7309add70b9c720a32b16291a9c2c4"
CHINA = timezone(timedelta(hours=8))


def spec_digest(spec):
    return hashlib.sha256(spec.model_dump_json().encode()).hexdigest()


class LocalPolicyError(ValueError):
    """Safe machine-readable codes only: never exception bodies from a remote CLI."""


class LocalPublishPolicy:
    def __init__(self, app, clock=None):
        self.app = app
        self.store = app.state.store
        self.authority = app.state.publication_authority
        self.clock = clock or self.authority.clock
        self.path = app.state.settings.state_dir / "local-publish-policy.dpapi"

    def _load(self):
        if not self.path.is_file():
            raise LocalPolicyError("live_policy_not_enabled")
        try:
            value = _read_private(self.path)
        except ValueError:
            raise LocalPolicyError("live_policy_invalid") from None
        if (
            value.get("enabled") is not True
            or value.get("version") != 1
            or value.get("daily_limit") != 3
            or value.get("channel") != "local"
            or not isinstance(value.get("id"), str)
        ):
            raise LocalPolicyError("live_policy_not_enabled")
        return value

    def enabled(self):
        try:
            self._load()
            return True
        except (ValueError, OSError):
            return False

    def _identity(self):
        status = connection_status(self.app)
        if not status["configured"] or not status["package_root"]:
            raise LocalPolicyError("cli_connection_not_configured")
        root = _package(Path(status["package_root"]))
        credentials = self.app.state.credentials.load()
        identity = hashlib.sha256(
            (credentials.local_client_id + "\0" + credentials.api_key.get_secret_value()).encode()
        ).hexdigest()
        return str(root), sha256_file(root / "bin-native/yxer.exe"), identity

    def enable(self):
        """Trusted installation action only; intentionally not exposed as a web toggle."""
        root, binary, client = self._identity()
        if binary != REVIEWED_CLI_SHA256:
            raise LocalPolicyError("cli_identity_not_reviewed")
        value = {
            "version": 1,
            "id": str(uuid4()),
            "enabled": True,
            "daily_limit": 3,
            "channel": "local",
            "package_root": root,
            "cli_sha256": binary,
            "client_sha256": client,
            "enabled_at": self.clock().isoformat(),
        }
        if self.path.is_file():
            current = self._load()
            if all(
                current.get(k) == value[k] for k in ("package_root", "cli_sha256", "client_sha256")
            ):
                return  # Idempotent setup must not revoke current new-job intent.
        _write_private(self.path, value)

    def check_identity(self):
        value = self._load()
        root, binary, client = self._identity()
        if root != value["package_root"] or binary != value["cli_sha256"]:
            raise LocalPolicyError("cli_identity_changed")
        if client != value["client_sha256"]:
            raise LocalPolicyError("client_identity_changed")
        return value

    def authorize_job(self, job, *, db=None):
        value = self.check_identity()
        if (
            job.state != "NEW"
            or job.content_frozen
            or job.owner_worker_id
            or not job.spec.publication_profile_id
            or job.spec.publish_mode != "immediate"
        ):
            raise LocalPolicyError("new_workflow_required")
        with self.store.transaction() if db is None else nullcontext(db) as db:
            current = self.store._snapshot(
                db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
            )
            profile = db.execute(
                "SELECT scope FROM publication_profiles WHERE id=?",
                (job.spec.publication_profile_id,),
            ).fetchone()
            if current != job or not profile or profile[0] != "live":
                raise LocalPolicyError("new_workflow_required")
            db.execute(
                "INSERT INTO publication_job_authorizations VALUES (?,?,?,?)",
                (job.id, value["id"], spec_digest(job.spec), self.clock().isoformat()),
            )

    def _intent(self, job_id):
        value = self._load()
        job = self.store.get(job_id)
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM publication_job_authorizations WHERE job_id=?", (job_id,)
            ).fetchone()
        if not row:
            raise LocalPolicyError("job_not_authorized")
        if row["policy_id"] != value["id"] or row["spec_digest"] != spec_digest(job.spec):
            raise LocalPolicyError("job_authorization_changed")
        return job

    def is_authorized(self, job_id):
        try:
            self._intent(job_id)
            return True
        except (ValueError, KeyError):
            return False

    def issue(self, snapshot):
        self.check_identity()
        job = self._intent(snapshot.job_id)
        if snapshot.mode != "immediate" or job.spec.publish_mode != "immediate":
            raise LocalPolicyError("native_schedule_not_verified")
        if snapshot.content_revision != job.content_revision or snapshot.title != job.spec.title:
            raise LocalPolicyError("job_authorization_changed")
        if tuple(job.spec.target_ids) != tuple(t.account.account_id for t in snapshot.targets):
            raise LocalPolicyError("job_authorization_changed")
        return self.authority.issue(
            snapshot,
            kind="user_job",
            expires_at=self.clock() + timedelta(hours=12),
            max_uploads=2,
            max_submissions_per_target=1,
            allow_temporary_storage_upload=True,
            authorization_note="Explicit new automatic workflow; local channel only; no payments",
        )

    def validate(self, permit, snapshot):
        self.authority.validate(permit, snapshot, real=True)
        if permit.kind != "user_job":
            raise LocalPolicyError("job_permit_required")
        self.check_identity()
        job = self._intent(snapshot.job_id)
        if (
            snapshot.mode != "immediate"
            or snapshot.content_revision != job.content_revision
            or snapshot.title != job.spec.title
            or tuple(job.spec.target_ids) != tuple(t.account.account_id for t in snapshot.targets)
        ):
            raise LocalPolicyError("job_authorization_changed")

    def _day(self):
        return self.clock().astimezone(CHINA).date().isoformat()

    def _used(self, db, day):
        start = datetime.fromisoformat(day).replace(tzinfo=CHINA).astimezone(UTC)
        end = start + timedelta(days=1)
        rows = db.execute(
            "SELECT job_id,target_id FROM publication_daily_reservations WHERE day=?", (day,)
        ).fetchall()
        keys = {(r[0], r[1]) for r in rows}
        # Include old/imported attempts not created by this policy, without double-counting.
        rows = db.execute(
            "SELECT a.job_id,a.target_id,a.created_at,a.id FROM target_attempts a "
            "JOIN publication_attempt_links l ON l.attempt_id=a.id "
            "JOIN publication_runs r ON r.id=l.run_id WHERE r.scope='live'"
        ).fetchall()
        seen_attempts = set()
        for r in rows:
            if start <= datetime.fromisoformat(r[2]).astimezone(UTC) < end:
                pair = (r[0], r[1])
                if pair in seen_attempts:
                    # Distinct attempts may each debit the provider; only pair the FIRST
                    # with an existing reservation, never collapse retries/imported history.
                    keys.add(("attempt:" + r[3], r[1]))
                else:
                    keys.add(pair)
                    seen_attempts.add(pair)
        return keys

    def remaining_today(self):
        with self.store.connection() as db:
            return max(0, 3 - len(self._used(db, self._day())))

    def reserve(self, snapshot):
        self._intent(snapshot.job_id)
        day = self._day()
        requested = {(snapshot.job_id, t.account.account_id) for t in snapshot.targets}
        with self.store.transaction() as db:
            used = self._used(db, day)
            if len(used | requested) > 3:
                raise LocalPolicyError("local_daily_quota_exhausted")
            for job_id, target in sorted(requested):
                db.execute(
                    "INSERT OR IGNORE INTO publication_daily_reservations VALUES (?,?,?,?)",
                    (day, job_id, target, self.clock().isoformat()),
                )

    def preflight_problem(self, snapshot, report):
        if snapshot.mode != "immediate":
            return "native_schedule_not_verified"
        if report.payment_required:
            return "paid_gate"
        if report.tool_version != CLI_CONTRACT_VERSION or report.account_status != "SUPPORTED":
            return "accounts_unverified"
        accounts = {a.get("id"): a for a in report.accounts}
        if any(
            t.account.account_id not in accounts
            or accounts[t.account.account_id].get("active") is not True
            or accounts[t.account.account_id].get("platform") != t.account.platform
            for t in snapshot.targets
        ):
            return "accounts_changed"
        delta = self.clock() - report.checked_at
        if not -timedelta(seconds=5) <= delta <= timedelta(minutes=5):
            return "preflight_expired"
        return None

    def refresh_catalogue_if_stale(self):
        from fabu.publishing.connection import refresh_accounts

        with self.store.connection() as db:
            stamps = [
                row[0]
                for row in db.execute(
                    "SELECT checked_at FROM publication_catalog WHERE scope='live'"
                )
            ]
        fresh = bool(stamps)
        try:
            fresh = fresh and all(
                -timedelta(minutes=5)
                <= self.clock() - datetime.fromisoformat(stamp)
                <= timedelta(minutes=15)
                for stamp in stamps
            )
        except (ValueError, TypeError):
            fresh = False
        if not fresh:
            refresh_accounts(self.app)

    def readiness(self):
        blockers = []
        enabled = False
        try:
            self._load()
            enabled = True
            self.check_identity()
        except (ValueError, KeyError, OSError):
            blockers.append("本机真实发布未授权或CLI/凭证身份已变化")
        settings = self.app.state.settings
        if not all((settings.tools_dir / n).is_file() for n in ("ffmpeg.exe", "ffprobe.exe")):
            blockers.append("请配置FFmpeg/ffprobe工具目录")
        if not settings.node_path or not settings.node_path.is_file():
            blockers.append("请配置Node.js下载运行环境")
        with self.store.connection() as db:
            defaults = db.execute(
                "SELECT p.scope,p.config_json FROM publication_defaults d JOIN "
                "publication_profiles p ON p.id=d.profile_id WHERE d.singleton=1"
            ).fetchone()
        if not defaults or defaults[0] != "live":
            blockers.append("请保存真实账号默认发布配置")
        required = 3
        if defaults and defaults[0] == "live":
            from fabu.publishing.models import PublicationConfig
            from fabu.publishing.service import catalogue

            try:
                config = PublicationConfig.model_validate_json(defaults["config_json"])
                required = len(config.accounts)
                current = {a.account_id: a for a, _ in catalogue(self.store, "live")}
                if any(
                    a.account.account_id not in current
                    or not current[a.account.account_id].active
                    or current[a.account.account_id].platform != a.account.platform
                    for a in config.accounts
                ):
                    raise ValueError("Missing default accounts")
            except (ValueError, KeyError):
                blockers.append("默认账号配置缺失或失效；请刷新并核对目标账号")
        remaining = self.remaining_today()
        if remaining < required:
            blockers.append("本地当日保守投稿预算不足以提交全部目标；不会自动付费")
        return {
            "enabled": enabled,
            "status": "blocked" if blockers else "ready",
            "blockers": blockers,
            "submission_strategy": "per_target",
            "daily_submission_limit": 3,
            "remaining_local_submissions": remaining,
            "quota_source": "local_conservative_budget",
            "platform_quality_verified": False,
            "native_schedule_enabled": False,
        }
