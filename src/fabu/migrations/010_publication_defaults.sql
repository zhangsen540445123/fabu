CREATE TABLE IF NOT EXISTS publication_defaults (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 profile_id TEXT NOT NULL REFERENCES publication_profiles(id),
 mode TEXT NOT NULL CHECK(mode='immediate'),
 declaration_source TEXT NOT NULL CHECK(declaration_source='user_configured_default'),
 confirmed_at TEXT NOT NULL
);
