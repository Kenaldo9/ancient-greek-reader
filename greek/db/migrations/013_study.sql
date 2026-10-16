-- A model's study data for treebank texts, made in Message Batches (greek study
-- batch). Back up an existing database before applying this migration.
--
-- Each result is stored at the smallest unit that makes sense, so any selection
-- can be assembled from the pieces: a fluent translation per translation unit
-- (whole treebank sentences, up to 10 lines), a literal translation per line,
-- and a gloss and note per word. Model identity is part of every key.

-- A fluent translation of lines first_line–last_line. One model's units never
-- overlap: saving a unit removes that model's overlapping units.
CREATE TABLE IF NOT EXISTS translation_units (
    text_id    INTEGER NOT NULL REFERENCES texts(id) ON DELETE CASCADE,
    first_line INTEGER NOT NULL,
    last_line  INTEGER NOT NULL,
    fluent     TEXT    NOT NULL,
    model_used TEXT    NOT NULL,
    CHECK (first_line <= last_line),
    PRIMARY KEY (text_id, first_line, model_used)
);

-- A literal translation of one line.
CREATE TABLE IF NOT EXISTS line_translations (
    text_id     INTEGER NOT NULL REFERENCES texts(id) ON DELETE CASCADE,
    line_number INTEGER NOT NULL,
    literal     TEXT    NOT NULL,
    model_used  TEXT    NOT NULL,
    PRIMARY KEY (text_id, line_number, model_used)
);

-- A contextual gloss and a note for one word (a whitespace token of
-- lines.content, counted from 0, as in word_parses). parse_check is the
-- model's remark when it disagrees with the treebank's parse.
CREATE TABLE IF NOT EXISTS word_study (
    text_id     INTEGER NOT NULL REFERENCES texts(id) ON DELETE CASCADE,
    line_number INTEGER NOT NULL,
    token_index INTEGER NOT NULL,
    gloss       TEXT    NOT NULL,
    note        TEXT    NOT NULL,
    parse_check TEXT,
    model_used  TEXT    NOT NULL,
    PRIMARY KEY (text_id, line_number, token_index, model_used)
);

-- A submitted Message Batch: its API id, what it covers, its processing status
-- as last seen (in_progress, canceling, ended), and, once collected, its token
-- usage and cost in US dollars. The record outlives a deleted text.
CREATE TABLE IF NOT EXISTS study_batches (
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
    cost_usd           REAL    NOT NULL DEFAULT 0
);

-- One request of a batch: the lines it covers and their number of words, and
-- once collected whether it was stored (succeeded) or failed and why, with its
-- usage and cost (which give the measured cost per word).
CREATE TABLE IF NOT EXISTS study_requests (
    batch_id           TEXT    NOT NULL REFERENCES study_batches(id) ON DELETE CASCADE,
    custom_id          TEXT    NOT NULL,
    first_line         INTEGER NOT NULL,
    last_line          INTEGER NOT NULL,
    word_count         INTEGER NOT NULL,
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
