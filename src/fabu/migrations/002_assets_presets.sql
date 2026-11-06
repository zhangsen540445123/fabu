CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, local_path TEXT NOT NULL UNIQUE,
 media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS presets (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preset_versions (
 id TEXT PRIMARY KEY, preset_id TEXT NOT NULL REFERENCES presets(id),
 revision INTEGER NOT NULL, document_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(preset_id, revision)
);
CREATE TABLE IF NOT EXISTS asset_refs (
 asset_id TEXT NOT NULL REFERENCES assets(id), owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL,
 PRIMARY KEY(asset_id, owner_kind, owner_id)
);
