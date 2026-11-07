"""Bounded, read-only provider tracking; never a platform publication/quality verdict.

Integration: instantiate after JobStore migrations, call process_due(cancel) from
one worker's idle loop (one query at most per call). True means discovery, a check,
or expiry made progress; False means no work is due or cancellation was requested.
Do not register/replay publications to discover work. observations_for_run is a
pure database projection for the API, keyed by the latest linked attempt's account.
refresh_run(run_id, cancel) checks at most three due targets of one existing live
run; it does not bypass the initial delay, backoff, or exhausted tracking budgets.

Each attempt starts after 30s, stops after 120 checks / 8 consecutive unknowns /
24h since discovery (whichever comes first), and retains that budget on restart.

The reviewed CLI 3.2.32 shape is {id: taskSetId, tasks: [{platformName,
platformAccountId: null | str, stageStatus, publishId, ...}], page, totalPage,
totalSize}. Raw stdout, names, titles, URLs, errors and credentials are discarded.
That shape has no reviewed platform-media duration evidence: arbitrary `duration`
fields, upload receipts and provider success cannot verify the platform video.
"""

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fabu.publishing.models import PublishSnapshot, publish_snapshot_digest
from fabu.publishing.readonly import query_provider_details

POLL_INTERVAL_S = 30
MAX_BACKOFF_S = 15 * 60
MAX_CHECKS = 120
MAX_FAILURES = 8
MAX_TRACKING_S = 24 * 60 * 60
# ReadOnlyCli bounds each of version/details to 30s. Reserve before I/O so an
# interrupted process cannot cause a tight loop or reset its attempt budget.
CLAIM_TIMEOUT_S = 90
_PLATFORMS = {
    "抖音": "douyin",
    "头条号": "toutiao",
    "哔哩哔哩": "bilibili",
    "B站": "bilibili",
    "douyin": "douyin",
    "toutiao": "toutiao",
    "bilibili": "bilibili",
}
_STATUSES = {
    "pending": "pending",
    "doing": "running",
    "running": "running",
    "success": "success",
    "failed": "failed",
    "unknown": "unknown",
}
_PROVIDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
_CONTENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SOURCES = """
 SELECT a.*, r.id AS run_id, r.job_id AS run_job_id,
        r.content_revision AS run_revision, r.snapshot_digest AS run_digest, r.snapshot_json
 FROM target_attempts a
 JOIN publication_attempt_links l ON l.attempt_id=a.id
 JOIN publication_runs r ON r.id=l.run_id
 WHERE r.scope='live' AND l.definitely_not_submitted=0
 AND a.state IN ('PROVIDER_ACCEPTED','SUBMITTED','SCHEDULED','UNKNOWN')
 AND a.provider_task_id IS NOT NULL AND trim(a.provider_task_id)<>''
"""
_PUBLIC_FIELDS = (
    "status",
    "checked_at",
    "next_check_at",
    "platform_content_id",
    "reason",
    "quality_status",
    "expected_duration_s",
    "observed_duration_s",
)


@dataclass(frozen=True)
class _Binding:
    attempt_id: str
    run_id: str
    account_id: str
    provider_task_id: str
    snapshot_digest: str
    platform: str
    expected_duration_s: float | None


def _binding(row):
    """Require both relational identity and the frozen snapshot's integrity."""
    try:
        snapshot = PublishSnapshot.model_validate_json(row["snapshot_json"])
        targets = [t for t in snapshot.targets if t.account.account_id == row["target_id"]]
        if (
            not isinstance(row["provider_task_id"], str)
            or not _PROVIDER_ID.fullmatch(row["provider_task_id"])
            or snapshot.mode == "simulation"
            or snapshot.job_id != row["job_id"]
            or snapshot.job_id != row["run_job_id"]
            or snapshot.content_revision != row["revision"]
            or snapshot.content_revision != row["run_revision"]
            or snapshot.digest != row["payload_digest"]
            or snapshot.digest != row["run_digest"]
            or snapshot.digest != publish_snapshot_digest(snapshot)
            or len(targets) != 1
            or sum(t.account.platform == targets[0].account.platform for t in snapshot.targets) != 1
        ):
            return None
        duration = float(snapshot.output.media_info.duration)
        duration = duration if math.isfinite(duration) and duration > 0 else None
        return _Binding(
            row["id"],
            row["run_id"],
            row["target_id"],
            row["provider_task_id"],
            snapshot.digest,
            targets[0].account.platform,
            duration,
        )
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


class _Unbound(ValueError):
    pass


def _rows(raw, provider_id):
    """No title inference, recursive envelope search, pagination or child-ID guessing."""
    if isinstance(raw, dict) and "tasks" in raw:
        identities = [raw[k] for k in ("id", "taskSetId") if k in raw]
        if not identities or any(value != provider_id for value in identities):
            raise _Unbound
        rows = raw["tasks"]
        if not isinstance(rows, list):
            raise _Unbound
        for key, expected in (("page", 1), ("totalPage", 1), ("totalSize", len(rows))):
            if key in raw and (type(raw[key]) is not int or raw[key] != expected):
                raise _Unbound
        if "size" in raw and (type(raw["size"]) is not int or raw["size"] < len(rows)):
            raise _Unbound
        inherited = True
    else:
        rows = raw if isinstance(raw, list) else [raw]
        inherited = False
    if not rows or len(rows) > 1000 or any(not isinstance(row, dict) for row in rows):
        raise _Unbound
    bound = []
    for row in rows:
        if inherited:
            if "taskSetId" in row and row["taskSetId"] != provider_id:
                raise _Unbound
        elif "taskSetId" not in row:
            raise _Unbound
        elif row["taskSetId"] != provider_id:
            continue
        bound.append(row)
    return bound


def _interpret(raw, binding, *, allow_missing_account):
    try:
        candidates = []
        for row in _rows(raw, binding.provider_task_id):
            platforms = [row[k] for k in ("platformName", "platform") if row.get(k) is not None]
            if not platforms or any(
                not isinstance(p, str) or p not in _PLATFORMS for p in platforms
            ):
                raise _Unbound
            normalized = {_PLATFORMS[p] for p in platforms}
            if len(normalized) != 1:
                raise _Unbound
            if binding.platform not in normalized:
                continue
            accounts = [
                row[k] for k in ("platformAccountId", "accountId") if row.get(k) is not None
            ]
            if any(not isinstance(a, str) or not a for a in accounts) or len(set(accounts)) > 1:
                raise _Unbound
            if not accounts and not allow_missing_account:
                raise _Unbound
            if not accounts or accounts[0] == binding.account_id:
                candidates.append(row)
        if len(candidates) != 1:
            raise _Unbound
        row = candidates[0]
        values = [row[k] for k in ("stageStatus", "status") if row.get(k) is not None]
        statuses = {
            _STATUSES.get(value, "unknown") if isinstance(value, str) else "unknown"
            for value in values
        }
        status = statuses.pop() if len(statuses) == 1 else "unknown"
        content_id = row.get("publishId")
        if not isinstance(content_id, str) or not _CONTENT_ID.fullmatch(content_id):
            content_id = None
        reason = "provider_success_unverified" if status == "success" else "provider_" + status
        return status, content_id, reason
    except _Unbound:
        return "unknown", None, "provider_binding_ambiguous"


def observations_for_run(store, run_id) -> dict[str, dict]:
    """Read only; never start a query or reveal a previous attempt as the latest one."""
    with store.connection() as db:
        rows = db.execute(
            "SELECT o.* FROM provider_observations o "
            "JOIN target_attempts a ON a.id=o.attempt_id "
            "JOIN publication_attempt_links l ON l.attempt_id=a.id AND l.run_id=o.run_id "
            "JOIN publication_runs r ON r.id=l.run_id "
            "WHERE r.scope='live' AND r.id=? AND a.target_id=o.account_id "
            "AND a.provider_task_id=o.provider_task_id AND a.payload_digest=o.snapshot_digest "
            "AND NOT EXISTS (SELECT 1 FROM target_attempts newer "
            "JOIN publication_attempt_links nl ON nl.attempt_id=newer.id "
            "WHERE nl.run_id=r.id AND newer.target_id=a.target_id AND newer.rowid>a.rowid)",
            (run_id,),
        ).fetchall()
    return {row["account_id"]: {key: row[key] for key in _PUBLIC_FIELDS} for row in rows}


class ProviderTracker:
    def __init__(self, app, clock=None):
        self.app = app
        self.store = app.state.store
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self):
        now = self.clock()
        if now.utcoffset() is None:
            raise ValueError("ProviderTracker clock must be timezone-aware")
        return now.astimezone(UTC)

    def _discover(self, now, run_id):
        changed = False
        with self.store.transaction() as db:
            for row in db.execute(
                _SOURCES + " AND (? IS NULL OR r.id=?) "
                "AND NOT EXISTS (SELECT 1 FROM provider_observations o WHERE o.attempt_id=a.id)",
                (run_id, run_id),
            ).fetchall():
                identifier = row["provider_task_id"]
                if not isinstance(identifier, str) or not _PROVIDER_ID.fullmatch(identifier):
                    continue
                binding = _binding(row)
                changed |= (
                    db.execute(
                        "INSERT OR IGNORE INTO provider_observations "
                        "(attempt_id,run_id,account_id,provider_task_id,snapshot_digest,platform,"
                        "status,next_check_at,reason,expected_duration_s,started_at,deadline_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            row["id"],
                            row["run_id"],
                            row["target_id"],
                            row["provider_task_id"],
                            row["payload_digest"],
                            binding.platform if binding else None,
                            "pending" if binding else "unknown",
                            (now + timedelta(seconds=POLL_INTERVAL_S)).isoformat()
                            if binding
                            else None,
                            "awaiting_provider_check" if binding else "invalid_snapshot_binding",
                            binding.expected_duration_s if binding else None,
                            now.isoformat(),
                            (now + timedelta(seconds=MAX_TRACKING_S)).isoformat(),
                        ),
                    ).rowcount
                    > 0
                )
        return changed

    @staticmethod
    def _same_binding(row, binding):
        return binding is not None and all(
            row[key] == getattr(binding, key)
            for key in (
                "attempt_id",
                "run_id",
                "account_id",
                "provider_task_id",
                "snapshot_digest",
                "platform",
            )
        )

    def _claim(self, now, run_id):
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM provider_observations WHERE next_check_at IS NOT NULL "
                "AND (next_check_at<=? OR deadline_at<=?) AND (? IS NULL OR run_id=?) "
                "ORDER BY next_check_at,attempt_id LIMIT 1",
                (now.isoformat(), now.isoformat(), run_id, run_id),
            ).fetchone()
            if row is None:
                return None, None, False
            source = db.execute(_SOURCES + " AND a.id=?", (row["attempt_id"],)).fetchone()
            binding = _binding(source) if source is not None else None
            reason = None
            if not self._same_binding(row, binding):
                reason = "tracking_binding_changed"
            elif (
                row["check_count"] >= MAX_CHECKS
                or row["failure_count"] >= MAX_FAILURES
                or row["deadline_at"] <= now.isoformat()
            ):
                reason = "tracking_limit_reached"
            if reason:
                db.execute(
                    "UPDATE provider_observations SET status='unknown',reason=?,"
                    "next_check_at=NULL,claim_token=NULL,platform_content_id=NULL "
                    "WHERE attempt_id=?",
                    (reason, row["attempt_id"]),
                )
                return None, None, True
            token = uuid4().hex
            db.execute(
                "UPDATE provider_observations SET check_count=check_count+1,claim_token=?,"
                "next_check_at=? WHERE attempt_id=?",
                (token, (now + timedelta(seconds=CLAIM_TIMEOUT_S)).isoformat(), row["attempt_id"]),
            )
            return binding, token, True

    def _finish(self, binding, token, result):
        now = self._now()
        status, content_id, reason = result
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM provider_observations WHERE attempt_id=? AND claim_token=?",
                (binding.attempt_id, token),
            ).fetchone()
            if row is None:
                return  # Another claimant owns an expired/crashed query.
            source = db.execute(_SOURCES + " AND a.id=?", (binding.attempt_id,)).fetchone()
            current = _binding(source) if source is not None else None
            failures = row["failure_count"] + 1 if status == "unknown" else 0
            next_check = None
            if current != binding:
                status, content_id, reason = "unknown", None, "tracking_binding_changed"
            elif status not in {"success", "failed"}:
                if (
                    row["check_count"] >= MAX_CHECKS
                    or failures >= MAX_FAILURES
                    or row["deadline_at"] <= now.isoformat()
                ):
                    status, content_id, reason = "unknown", None, "tracking_limit_reached"
                else:
                    delay = min(MAX_BACKOFF_S, POLL_INTERVAL_S * 2 ** min(failures, 10))
                    next_check = min(
                        now + timedelta(seconds=delay), datetime.fromisoformat(row["deadline_at"])
                    ).isoformat()
            db.execute(
                "UPDATE provider_observations SET status=?,checked_at=?,next_check_at=?,"
                "platform_content_id=?,reason=?,failure_count=?,claim_token=NULL "
                "WHERE attempt_id=? AND claim_token=?",
                (
                    status,
                    now.isoformat(),
                    next_check,
                    content_id,
                    reason,
                    failures,
                    binding.attempt_id,
                    token,
                ),
            )

    def _accountless_is_unique(self, binding):
        # An accountless remote child must not get attributed to two different
        # local accounts just because each run independently has one target.
        with self.store.connection() as db:
            sources = db.execute(
                _SOURCES + " AND a.provider_task_id=?", (binding.provider_task_id,)
            ).fetchall()
        for source in sources:
            other = _binding(source)
            if other is None or (
                other.platform == binding.platform and other.account_id != binding.account_id
            ):
                return False
        return True

    def process_due(self, cancel) -> bool:
        """Discover persisted live receipts and perform at most one bounded read."""
        return self._process_due(cancel)

    def refresh_run(self, run_id, cancel) -> None:
        """Register/check an existing live run without bypassing deadlines/backoff.

        The first call schedules its initial check 30s out, like automatic discovery.
        At most three already-due targets are checked per request. Repeated clicks
        neither advance a next_check_at nor reset a finished/exhausted tracker.
        """
        if not isinstance(run_id, str) or not run_id:
            return
        for _ in range(3):
            if not self._process_due(cancel, run_id=run_id):
                break

    def _process_due(self, cancel, run_id=None) -> bool:
        if cancel.is_set():
            return False
        now = self._now()
        discovered = self._discover(now, run_id)
        if cancel.is_set():
            return discovered
        binding, token, progressed = self._claim(now, run_id)
        if binding is None:
            return discovered or progressed
        try:
            raw = query_provider_details(self.app, binding.provider_task_id, cancel)
            result = _interpret(
                raw, binding, allow_missing_account=self._accountless_is_unique(binding)
            )
        except Exception:
            # Never store or surface provider/exception strings; they can contain keys/URLs.
            result = "unknown", None, "provider_query_failed"
        if cancel.is_set():
            result = "unknown", None, "provider_query_canceled"
        self._finish(binding, token, result)
        return True
