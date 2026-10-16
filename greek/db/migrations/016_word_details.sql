-- Word details (docs/word-details/plan.md, Pilot 1): every node of the treebank,
-- each word's link to its node, and dictionary forms of lemmas made in Message
-- Batches (greek study forms). Back up an existing database before applying
-- this migration.

-- Every node of every treebank sentence: words, punctuation, and the elliptic
-- nodes the treebank adds for an understood word (artificial = 1, form "[0]").
-- head is the node_id of the node it depends on, 0 for the sentence's root.
-- A word's role is read from these, through the punctuation, conjunctions and
-- elliptic nodes it often depends on.
CREATE TABLE IF NOT EXISTS tb_nodes (
    text_id     INTEGER NOT NULL REFERENCES texts(id) ON DELETE CASCADE,
    sentence_id INTEGER NOT NULL,
    node_id     INTEGER NOT NULL,
    head        INTEGER NOT NULL,
    relation    TEXT    NOT NULL,
    form        TEXT    NOT NULL,
    lemma       TEXT,
    postag      TEXT,
    artificial  INTEGER NOT NULL DEFAULT 0 CHECK (artificial IN (0, 1)),
    PRIMARY KEY (text_id, sentence_id, node_id)
);

-- The treebank node of a word (with sentence_id); NULL for a word without a
-- treebank word, and until `greek study import-treebank --nodes-only` links it.
ALTER TABLE word_parses ADD COLUMN node_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_word_parses_node
    ON word_parses(text_id, sentence_id, node_id);

-- Lemma frequencies, counted per word click
CREATE INDEX IF NOT EXISTS idx_word_parses_lemma ON word_parses(text_id, lemma);

-- A model's dictionary form of a lemma (keyed by the treebank lemma), as a
-- commentary's vocabulary gives it ("μῆνις, -ιος, ἡ"), with a note on its
-- Homeric forms, an adjective's irregular comparison, and, from Pilot 2, a
-- verb's construction ("" when there is nothing to say).
CREATE TABLE IF NOT EXISTS lemma_forms (
    lemma        TEXT NOT NULL,
    model_used   TEXT NOT NULL,
    pos          TEXT NOT NULL,
    headword     TEXT NOT NULL,
    forms        TEXT NOT NULL DEFAULT '',
    comparison   TEXT NOT NULL DEFAULT '',
    construction TEXT,
    made_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (lemma, model_used)
);

-- study_batches.kind also allows 'forms' (dictionary forms, with
-- lemma_requests) and 'uses' (use in context, Pilot 2, with study_requests).
-- SQLite can't change a CHECK constraint in place, so the table is rebuilt as
-- in 015: foreign keys are off during the rebuild, so dropping the old table
-- doesn't delete its requests, and every row is kept.
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
                       CHECK (kind IN ('study', 'parts', 'notes', 'forms', 'uses')),
    effort             TEXT    NOT NULL DEFAULT 'medium',
    pilot              INTEGER NOT NULL DEFAULT 0 CHECK (pilot IN (0, 1))
);

INSERT INTO study_batches_new(id, text_id, scope, model, status, request_count,
                              submitted_at, ended_at, collected_at, input_tokens,
                              cache_write_tokens, cache_read_tokens, output_tokens,
                              cost_usd, kind, effort, pilot)
SELECT id, text_id, scope, model, status, request_count, submitted_at, ended_at,
       collected_at, input_tokens, cache_write_tokens, cache_read_tokens,
       output_tokens, cost_usd, kind, effort, pilot
FROM study_batches;

DROP TABLE study_batches;
ALTER TABLE study_batches_new RENAME TO study_batches;

COMMIT;

PRAGMA foreign_keys = ON;

-- One request of a forms batch: the lemmas it asked for, as a JSON array in the
-- order asked, and once collected how many were stored, why the others failed,
-- and its usage and cost.
CREATE TABLE IF NOT EXISTS lemma_requests (
    batch_id           TEXT    NOT NULL REFERENCES study_batches(id) ON DELETE CASCADE,
    custom_id          TEXT    NOT NULL,
    lemmas             TEXT    NOT NULL,
    lemma_count        INTEGER NOT NULL,
    stored_count       INTEGER NOT NULL DEFAULT 0,
    status             TEXT    NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'succeeded', 'failed')),
    failure            TEXT,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL    NOT NULL DEFAULT 0,
    CHECK ((status = 'failed') = (failure IS NOT NULL)),
    PRIMARY KEY (batch_id, custom_id)
);
