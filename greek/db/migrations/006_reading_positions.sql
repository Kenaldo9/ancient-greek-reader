-- Last reading position per text, so "Continue at …" works across devices.
CREATE TABLE IF NOT EXISTS reading_positions (
    text_id     INTEGER PRIMARY KEY REFERENCES texts(id) ON DELETE CASCADE,
    line_number INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
