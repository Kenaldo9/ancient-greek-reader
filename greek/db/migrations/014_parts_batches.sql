-- Principal parts of a treebank text's verbs, made in Message Batches (greek
-- study parts). Back up an existing database before applying this migration.
--
-- The parts themselves go to verb_parts (007), keyed by verb and model like a
-- lookup on the Verbs page. A batch is now of a kind: 'study' (glosses, notes
-- and translations, with study_requests) or 'parts' (with parts_requests).
ALTER TABLE study_batches ADD COLUMN kind TEXT NOT NULL DEFAULT 'study'
    CHECK (kind IN ('study', 'parts'));

-- One request of a parts batch: the verbs (treebank lemmas) it asked for, as
-- a JSON array in the order asked, and once collected how many were stored,
-- why the others failed, and its usage and cost.
CREATE TABLE IF NOT EXISTS parts_requests (
    batch_id           TEXT    NOT NULL REFERENCES study_batches(id) ON DELETE CASCADE,
    custom_id          TEXT    NOT NULL,
    verbs              TEXT    NOT NULL,
    verb_count         INTEGER NOT NULL,
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
