-- Notes-only batches (greek study notes), which redo the notes of saved study
-- data with a revised note prompt. Back up an existing database before
-- applying this migration: it replaces the notes it rewrites.
--
-- study_batches.kind also allows 'notes'. SQLite can't change a CHECK
-- constraint in place, so the table is rebuilt; its rows are kept, and so are
-- the requests of study_requests and parts_requests that refer to them. A batch
-- now records its effort (every earlier batch ran at 'medium') and whether it
-- is a pilot, whose notes are only written to a review file. Notes batches
-- record their requests in study_requests, as study batches do.
--
-- Dropping the old table with foreign keys on would delete its requests
-- through ON DELETE CASCADE, so they are off during the rebuild. The app's
-- connections all run with foreign keys on, which is restored at the end.
PRAGMA foreign_keys = OFF;

BEGIN;

CREATE TABLE study_batches_new (
    id                 TEXT    PRIMARY KEY,
    text_id            INTEGER REFERENCES texts(id) ON DELETE SET NULL,
    scope              TEXT    NOT NULL,
    model              TEXT    NOT NULL,
    status             TEXT    NOT NULL,
    request_count      INTEGER NOT NULL,
    submitted_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    ended_at           TEXT,
    collected_at       TEXT,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL    NOT NULL DEFAULT 0,
    kind               TEXT    NOT NULL DEFAULT 'study'
                       CHECK (kind IN ('study', 'parts', 'notes')),
    effort             TEXT    NOT NULL DEFAULT 'medium',
    pilot              INTEGER NOT NULL DEFAULT 0 CHECK (pilot IN (0, 1))
);

INSERT INTO study_batches_new(id, text_id, scope, model, status, request_count,
                              submitted_at, ended_at, collected_at, input_tokens,
                              cache_write_tokens, cache_read_tokens, output_tokens,
                              cost_usd, kind)
SELECT id, text_id, scope, model, status, request_count, submitted_at, ended_at,
       collected_at, input_tokens, cache_write_tokens, cache_read_tokens,
       output_tokens, cost_usd, kind
FROM study_batches;

DROP TABLE study_batches;
ALTER TABLE study_batches_new RENAME TO study_batches;

-- Which note prompt wrote a word's note: 1 for the Phase 5 rule, 2 for the
-- revised one (longer notes for the words worth a closer look).
ALTER TABLE word_study ADD COLUMN note_version INTEGER NOT NULL DEFAULT 1;

COMMIT;

PRAGMA foreign_keys = ON;
