import pytest
from PIL import Image


def make_store(tmp_path):
    from fabu.assets import AssetStore
    from fabu.store import JobStore

    db = JobStore(tmp_path / "db.sqlite3")
    return AssetStore(tmp_path / "assets", db), db


def image_file(tmp_path, color="red"):
    path = tmp_path / "封面 [1].png"
    Image.new("RGB", (32, 24), color).save(path)
    return path


def test_external_change_does_not_mutate_asset(tmp_path):
    assets, _ = make_store(tmp_path)
    source = image_file(tmp_path)
    ref = assets.import_file(source, "image")
    saved_bytes = ref.local_path.read_bytes()
    image_file(tmp_path, "blue")
    assert ref.local_path.read_bytes() == saved_bytes
    assert ref.local_path != source
    assert assets.verify(ref.id)
    source.unlink()
    assert assets.verify(ref.id)


def test_referenced_asset_cannot_be_deleted(tmp_path):
    from fabu.assets import AssetInUse
    from fabu.presets import PresetStore

    assets, db = make_store(tmp_path)
    ref = assets.import_file(image_file(tmp_path), "image")
    presets = PresetStore(db, assets)
    presets.save_draft(
        None,
        {
            "name": "test",
            "steps": [
                {
                    "id": "layer-1",
                    "kind": "image_layer",
                    "enabled": True,
                    "scope": "main",
                    "params": {},
                    "asset_ids": [ref.id],
                }
            ],
            "output": {"crf": 20},
        },
    )
    with pytest.raises(AssetInUse):
        assets.delete_unreferenced(ref.id)
    assert assets.verify(ref.id)


def test_partial_copy_never_registers_asset(tmp_path, monkeypatch):
    import fabu.assets as module

    assets, db = make_store(tmp_path)
    source = image_file(tmp_path)
    before = source.read_bytes()

    def fail_copy(source, destination):
        destination.write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(module, "_copy_file", fail_copy)
    with pytest.raises(OSError):
        assets.import_file(source, "image")
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    assert list(assets.root.iterdir()) == []
    assert source.read_bytes() == before


def test_import_rejects_directory_and_reparse_escape(tmp_path, monkeypatch):
    import fabu.assets as module

    assets, _ = make_store(tmp_path)
    with pytest.raises(ValueError, match="file"):
        assets.import_file(tmp_path, "video")
    source = image_file(tmp_path)
    original = module._is_reparse_point
    monkeypatch.setattr(module, "_is_reparse_point", lambda path: path == source or original(path))
    with pytest.raises(ValueError, match="reparse"):
        assets.import_file(source, "image")


def test_invalid_content_cannot_impersonate_image(tmp_path):
    assets, _ = make_store(tmp_path)
    fake = tmp_path / "fake.png"
    fake.write_text("<script>not an image</script>")
    with pytest.raises(ValueError):
        assets.import_file(fake, "image")
    assert list(assets.root.iterdir()) == []


def test_missing_or_tampered_library_asset_detected(tmp_path):
    assets, _ = make_store(tmp_path)
    ref = assets.import_file(image_file(tmp_path), "image")
    ref.local_path.write_bytes(b"tampered")
    assert not assets.verify(ref.id)
    ref.local_path.unlink()
    assert not assets.verify(ref.id)


def test_unreferenced_delete_does_not_touch_source(tmp_path):
    assets, _ = make_store(tmp_path)
    source = image_file(tmp_path)
    ref = assets.import_file(source, "image")
    assets.delete_unreferenced(ref.id)
    assert source.exists() and not ref.local_path.exists()
    with pytest.raises(KeyError):
        assets.get(ref.id)


def test_asset_id_cannot_be_path(tmp_path):
    assets, _ = make_store(tmp_path)
    with pytest.raises((ValueError, KeyError)):
        assets.get("../../anything")


def test_preset_versions_do_not_mutate_old_version(tmp_path):
    from fabu.presets import PresetStore

    assets, db = make_store(tmp_path)
    presets = PresetStore(db, assets)
    doc = {"name": "基本2K", "steps": [], "output": {"crf": 20}}
    first = presets.save_draft(None, doc)
    second = presets.save_draft(first.preset_id, {**doc, "output": {"crf": 19}})
    assert first.revision == 1 and second.revision == 2
    assert first.id != second.id
    assert presets.get_version(first.id).output == {"crf": 20}
    assert presets.get_version(second.id).output == {"crf": 19}


def test_preset_refuses_missing_asset_and_duplicate_step_id(tmp_path):
    from fabu.presets import PresetStore

    assets, db = make_store(tmp_path)
    presets = PresetStore(db, assets)
    step = {"id": "x", "kind": "image_layer", "asset_ids": ["missing"]}
    with pytest.raises((ValueError, KeyError)):
        presets.save_draft(None, {"name": "x", "steps": [step], "output": {}})
    with pytest.raises(ValueError):
        presets.save_draft(
            None, {"name": "x", "steps": [{**step, "asset_ids": []}] * 2, "output": {}}
        )


def test_subtitle_requires_utf8_and_srt_extension(tmp_path):
    assets, _ = make_store(tmp_path)
    source = tmp_path / "中文.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\n示例字幕\n", encoding="utf-8")
    assert assets.verify(assets.import_file(source, "subtitle").id)
    source.write_bytes(b"\xff\xff")
    with pytest.raises(ValueError):
        assets.import_file(source, "subtitle")


def test_wrong_media_type_and_extension_rejected(tmp_path):
    assets, _ = make_store(tmp_path)
    source = image_file(tmp_path)
    with pytest.raises(ValueError):
        assets.import_file(source, "video")
    with pytest.raises(ValueError):
        assets.import_file(source, "script")


def test_preset_cannot_reference_tampered_asset(tmp_path):
    from fabu.presets import PresetStore

    assets, db = make_store(tmp_path)
    ref = assets.import_file(image_file(tmp_path), "image")
    ref.local_path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity"):
        PresetStore(db, assets).save_draft(
            None,
            {
                "name": "x",
                "steps": [{"id": "x", "kind": "image_layer", "asset_ids": [ref.id]}],
                "output": {},
            },
        )
