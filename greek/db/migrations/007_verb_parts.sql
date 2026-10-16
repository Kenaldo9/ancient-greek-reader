-- Saved principal-part lookups, one per verb and model, so a repeat lookup is
-- instant and free. `verb` is stored normalized (NFC, trimmed, lowercase).
CREATE TABLE IF NOT EXISTS verb_parts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    verb         TEXT    NOT NULL,
    model_used   TEXT    NOT NULL,
    parts_json   TEXT    NOT NULL,
    looked_up_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(verb, model_used)
);
