"""Crash-safe upload identities, with DPAPI-protected full remote receipts in SQLite."""

from datetime import UTC, datetime
from uuid import uuid4

import pywintypes
import win32crypt

from fabu.publishing.gateway import UploadReceipt, resource_metadata
from fabu.publishing.models import PublishSnapshot, verify_publish_snapshot
from fabu.store import JobStore


class SqliteUploadJournal:
    durable = True

    def __init__(self, store: JobStore, snapshot: PublishSnapshot, *, scope: str):
        verify_publish_snapshot(snapshot)
        if scope not in {"simulation", "live"}:
            raise ValueError("Upload journal scope must be explicit")
        self.store, self.snapshot, self.scope = store, snapshot, scope

    def _identity(self, digest, kind):
        verify_publish_snapshot(self.snapshot)
        if digest != self.snapshot.digest or kind not in {"video", "cover"}:
            raise ValueError("Upload journal used outside its snapshot scope")
        return self.snapshot.output.sha256 if kind == "video" else self.snapshot.cover.sha256

    def _reject_unresolved_source(self, db):
        if db.execute(
            "SELECT 1 FROM upload_attempts WHERE scope=? AND source_video_id=? "
            "AND state IN ('SUBMITTING','UNKNOWN')",
            (self.scope, self.snapshot.video_id),
        ).fetchone():
            raise ValueError(
                "Source has an unknown resource upload; reconcile before any new upload"
            )

    def get(self, snapshot_digest: str, kind: str) -> UploadReceipt | None:
        sha = self._identity(snapshot_digest, kind)
        with self.store.connection() as db:
            row = db.execute(
                "SELECT state,receipt_cipher FROM upload_attempts "
                "WHERE scope=? AND resource=? AND sha256=?",
                (self.scope, kind, sha),
            ).fetchone()
            if row is None:
                self._reject_unresolved_source(db)
        if row is None:
            return None
        if row["state"] != "COMPLETE" or row["receipt_cipher"] is None:
            raise ValueError("Resource upload outcome is unknown; do not issue another upload")
        try:
            _, raw = win32crypt.CryptUnprotectData(
                bytes(row["receipt_cipher"]), None, None, None, 1
            )
            receipt = UploadReceipt.model_validate_json(raw)
        except (pywintypes.error, ValueError) as exc:
            raise ValueError(
                "Stored upload receipt cannot be verified; reconcile before reuse"
            ) from exc
        if (
            receipt.sha256 != sha
            or receipt.resource != kind
            or receipt.metadata != resource_metadata(self.snapshot, kind)
        ):
            raise ValueError("Stored upload receipt does not match the current frozen resource")
        return receipt

    def begin(self, snapshot_digest: str, kind: str) -> None:
        sha = self._identity(snapshot_digest, kind)
        now = datetime.now(UTC).isoformat()
        with self.store.transaction() as db:
            self._reject_unresolved_source(db)
            if db.execute(
                "SELECT 1 FROM upload_attempts WHERE scope=? AND resource=? AND sha256=?",
                (self.scope, kind, sha),
            ).fetchone():
                raise ValueError(
                    "Resource upload already attempted; get receipt or reconcile unknown outcome"
                )
            db.execute(
                "INSERT INTO upload_attempts VALUES (?,?,?,?,?,?,'SUBMITTING',NULL,?,?)",
                (
                    str(uuid4()),
                    self.scope,
                    kind,
                    sha,
                    snapshot_digest,
                    self.snapshot.video_id,
                    now,
                    now,
                ),
            )

    def complete(self, snapshot_digest: str, receipt: UploadReceipt) -> None:
        sha = self._identity(snapshot_digest, receipt.resource)
        if receipt.sha256 != sha or receipt.metadata != resource_metadata(
            self.snapshot, receipt.resource
        ):
            raise ValueError("Returned upload receipt does not match attempted media")
        encrypted = win32crypt.CryptProtectData(
            receipt.model_dump_json().encode("utf-8"), "Fabu upload receipt", None, None, None, 1
        )
        with self.store.transaction() as db:
            result = db.execute(
                "UPDATE upload_attempts SET state='COMPLETE',receipt_cipher=?,updated_at=? "
                "WHERE scope=? AND resource=? AND sha256=? "
                "AND snapshot_digest=? AND state='SUBMITTING'",
                (
                    encrypted,
                    datetime.now(UTC).isoformat(),
                    self.scope,
                    receipt.resource,
                    sha,
                    snapshot_digest,
                ),
            )
            if result.rowcount != 1:
                raise ValueError("No matching in-flight upload attempt")

    @staticmethod
    def recover_interrupted(store: JobStore) -> int:
        # Caller must own the global Worker mutex; mark uncertainty, never erase attempt history.
        with store.transaction() as db:
            result = db.execute(
                "UPDATE upload_attempts SET state='UNKNOWN',updated_at=? WHERE state='SUBMITTING'",
                (datetime.now(UTC).isoformat(),),
            )
            return result.rowcount
