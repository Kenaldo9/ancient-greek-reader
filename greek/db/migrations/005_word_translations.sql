CREATE TABLE IF NOT EXISTS word_translations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    passage_hash   TEXT    NOT NULL UNIQUE,
    source_text_id INTEGER REFERENCES texts(id) ON DELETE SET NULL,
    line_start     INTEGER,
    line_end       INTEGER,
    words_json     TEXT    NOT NULL,
    model_used     TEXT    NOT NULL,
    analyzed_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
