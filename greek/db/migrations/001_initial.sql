PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS texts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    author      TEXT,
    filename    TEXT    NOT NULL UNIQUE,
    filepath    TEXT    NOT NULL,
    line_count  INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text_id     INTEGER NOT NULL REFERENCES texts(id) ON DELETE CASCADE,
    line_number INTEGER NOT NULL,
    content     TEXT    NOT NULL,
    UNIQUE(text_id, line_number)
);

CREATE TABLE IF NOT EXISTS analyses (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    passage_hash         TEXT    NOT NULL UNIQUE,
    source_text_id       INTEGER REFERENCES texts(id) ON DELETE SET NULL,
    line_start           INTEGER,
    line_end             INTEGER,
    raw_passage          TEXT    NOT NULL,
    fluent_translation   TEXT    NOT NULL,
    literal_translation  TEXT    NOT NULL,
    grammatical_json     TEXT    NOT NULL,
    model_used           TEXT    NOT NULL,
    analyzed_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text_id     INTEGER NOT NULL REFERENCES texts(id) ON DELETE CASCADE,
    line_start  INTEGER NOT NULL,
    line_end    INTEGER NOT NULL,
    note        TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    invoked_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    cache_hit   INTEGER NOT NULL DEFAULT 0
);
