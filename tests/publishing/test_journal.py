import pytest
from test_templates import build


def test_upload_journal_persists_encrypted_receipt_and_blocks_unknown_retry(publishing_context):
    from fabu.publishing.gateway import UploadReceipt, resource_metadata
    from fabu.publishing.journal import SqliteUploadJournal

    store, *_ = publishing_context
    snapshot = build(publishing_context)
    journal = SqliteUploadJournal(store, snapshot, scope="simulation")
    assert journal.get(snapshot.digest, "video") is None
    journal.begin(snapshot.digest, "video")
    with pytest.raises(ValueError, match="unknown"):
        SqliteUploadJournal(store, snapshot, scope="simulation").get(snapshot.digest, "video")
    receipt = UploadReceipt(
        resource="video",
        sha256=snapshot.output.sha256,
        key="cloud-publish/synthetic/video",
        metadata=resource_metadata(snapshot, "video"),
        raw={
            "key": "cloud-publish/synthetic/video",
            "url": "https://fake.invalid/?token=synthetic-sensitive-receipt",
        },
    )
    journal.complete(snapshot.digest, receipt)
    restored = SqliteUploadJournal(store, snapshot, scope="simulation").get(
        snapshot.digest, "video"
    )
    assert restored == receipt
    with store.connection() as db:
        row = db.execute("SELECT receipt_cipher,state FROM upload_attempts").fetchone()
        assert row["state"] == "COMPLETE"
        assert b"synthetic-sensitive" not in row["receipt_cipher"]
    for path in store.path.parent.glob("state.sqlite3*"):
        assert b"synthetic-sensitive" not in path.read_bytes()


def test_upload_unknown_blocks_changed_snapshot_and_scope_isolation(publishing_context):
    from fabu.publishing.journal import SqliteUploadJournal
    from fabu.publishing.models import publish_snapshot_digest

    store, *_ = publishing_context
    snapshot = build(publishing_context)
    first = SqliteUploadJournal(store, snapshot, scope="simulation")
    first.begin(snapshot.digest, "cover")
    changed = snapshot.model_copy(update={"prepared_revision": snapshot.prepared_revision + 1})
    changed = changed.model_copy(update={"digest": publish_snapshot_digest(changed)})
    with pytest.raises(ValueError, match="unknown"):
        SqliteUploadJournal(store, changed, scope="simulation").get(changed.digest, "cover")
    # Fake receipts never authorize real resources, even for identical file hashes.
    assert SqliteUploadJournal(store, changed, scope="live").get(changed.digest, "cover") is None


def test_wrong_receipt_identity_does_not_complete_attempt(publishing_context):
    from fabu.publishing.gateway import UploadReceipt, resource_metadata
    from fabu.publishing.journal import SqliteUploadJournal

    store, *_ = publishing_context
    snapshot = build(publishing_context)
    journal = SqliteUploadJournal(store, snapshot, scope="simulation")
    journal.begin(snapshot.digest, "video")
    bad = UploadReceipt(
        resource="video",
        sha256="0" * 64,
        key="cloud-publish/other",
        metadata=resource_metadata(snapshot, "video"),
        raw={},
    )
    with pytest.raises(ValueError):
        journal.complete(snapshot.digest, bad)
    with pytest.raises(ValueError, match="unknown"):
        journal.get(snapshot.digest, "video")


def test_concurrent_begin_creates_one_resource_attempt_and_restart_marks_unknown(
    publishing_context,
):
    from concurrent.futures import ThreadPoolExecutor

    from fabu.publishing.journal import SqliteUploadJournal

    store, *_ = publishing_context
    snapshot = build(publishing_context)

    def begin():
        try:
            SqliteUploadJournal(store, snapshot, scope="simulation").begin(snapshot.digest, "video")
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        result = list(executor.map(lambda _: begin(), range(2)))
    assert result.count(True) == 1
    assert SqliteUploadJournal.recover_interrupted(store) == 1
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM upload_attempts").fetchone()[0] == 1
        assert db.execute("SELECT state FROM upload_attempts").fetchone()[0] == "UNKNOWN"
    with pytest.raises(ValueError, match="unknown"):
        SqliteUploadJournal(store, snapshot, scope="simulation").get(snapshot.digest, "video")


def test_corrupt_protected_receipt_is_not_reuploaded(publishing_context):
    from fabu.publishing.gateway import UploadReceipt, resource_metadata
    from fabu.publishing.journal import SqliteUploadJournal

    store, *_ = publishing_context
    snapshot = build(publishing_context)
    journal = SqliteUploadJournal(store, snapshot, scope="simulation")
    journal.begin(snapshot.digest, "cover")
    journal.complete(
        snapshot.digest,
        UploadReceipt(
            resource="cover",
            sha256=snapshot.cover.sha256,
            key="cloud-publish/test",
            metadata=resource_metadata(snapshot, "cover"),
            raw={},
        ),
    )
    with store.transaction() as db:
        db.execute("UPDATE upload_attempts SET receipt_cipher=?", (b"bad",))
    with pytest.raises(ValueError):
        journal.get(snapshot.digest, "cover")
    with pytest.raises(ValueError):
        journal.begin(snapshot.digest, "cover")


def test_unknown_source_cannot_bypass_upload_guard_with_changed_output_hash(publishing_context):
    from fabu.publishing.journal import SqliteUploadJournal
    from fabu.publishing.models import publish_snapshot_digest

    store, *_ = publishing_context
    snapshot = build(publishing_context)
    SqliteUploadJournal(store, snapshot, scope="simulation").begin(snapshot.digest, "video")
    changed = snapshot.model_copy(
        update={"output": snapshot.output.model_copy(update={"sha256": "f" * 64})}
    )
    changed = changed.model_copy(update={"digest": publish_snapshot_digest(changed)})
    replacement = SqliteUploadJournal(store, changed, scope="simulation")
    with pytest.raises(ValueError, match="unknown"):
        replacement.get(changed.digest, "video")
    with pytest.raises(ValueError, match="unknown"):
        replacement.begin(changed.digest, "video")
