-- Questions asked about a passage in the reader (Ask), with the model's answer,
-- so answers are kept and earlier questions about the same lines can be shown.
CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text_id     INTEGER NOT NULL REFERENCES texts(id) ON DELETE CASCADE,
    line_start  INTEGER NOT NULL,
    line_end    INTEGER NOT NULL,
    question    TEXT    NOT NULL,
    answer      TEXT    NOT NULL,
    model_used  TEXT    NOT NULL,
    asked_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_questions_range ON questions(text_id, line_start, line_end);
