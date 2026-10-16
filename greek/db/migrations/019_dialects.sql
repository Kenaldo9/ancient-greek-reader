-- Dictionary forms, constructions and principal parts per dialect
-- (docs/all-texts/plan.md, Phase 3, decision 7): the Iliad and the Odyssey
-- share Homeric ones, Herodotus gets Ionic ones and Thucydides Attic ones,
-- each made with its work's prompt. Back up an existing database before
-- applying this migration.
--
-- lemma_forms and verb_parts get a dialect in their key. SQLite can't change
-- a key in place, so both tables are rebuilt as in 015–016, keeping every row
-- (and verb_parts' ids). Foreign keys are off during the rebuild and restored
-- at the end.
PRAGMA foreign_keys = OFF;

BEGIN;

-- A lemma's dictionary form or construction in a dialect (homeric, ionic,
-- attic). Every row so far was made for the Iliad.
CREATE TABLE lemma_forms_new (
    lemma        TEXT NOT NULL,
    dialect      TEXT NOT NULL DEFAULT 'homeric',
    model_used   TEXT NOT NULL,
    pos          TEXT NOT NULL,
    headword     TEXT NOT NULL,
    forms        TEXT NOT NULL DEFAULT '',
    comparison   TEXT NOT NULL DEFAULT '',
    construction TEXT,
    made_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (lemma, dialect, model_used)
);

INSERT INTO lemma_forms_new(lemma, dialect, model_used, pos, headword, forms,
                            comparison, construction, made_at)
SELECT lemma, 'homeric', model_used, pos, headword, forms, comparison,
       construction, made_at
FROM lemma_forms;

DROP TABLE lemma_forms;
ALTER TABLE lemma_forms_new RENAME TO lemma_forms;

-- A verb's principal parts: from a parts batch, in its work's dialect; or from
-- a lookup on the Verbs page, with no dialect (''). A row a parts batch of the
-- same model asked for is from that batch (all of them were for the Iliad); any
-- other row is a lookup.
CREATE TABLE verb_parts_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    verb         TEXT    NOT NULL,
    dialect      TEXT    NOT NULL DEFAULT '',
    model_used   TEXT    NOT NULL,
    parts_json   TEXT    NOT NULL,
    looked_up_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(verb, dialect, model_used)
);

INSERT INTO verb_parts_new(id, verb, dialect, model_used, parts_json, looked_up_at)
SELECT v.id, v.verb,
       CASE WHEN EXISTS (
           SELECT 1 FROM parts_requests r
           JOIN study_batches b ON b.id = r.batch_id, json_each(r.verbs) j
           WHERE j.value = v.verb AND b.model = v.model_used
             AND r.status != 'pending'
       ) THEN 'homeric' ELSE '' END,
       v.model_used, v.parts_json, v.looked_up_at
FROM verb_parts v;

DROP TABLE verb_parts;
ALTER TABLE verb_parts_new RENAME TO verb_parts;

-- The dialect a forms or parts batch was made for (NULL for the other kinds),
-- so its results are stored under it and lemmas waiting in it are known.
ALTER TABLE study_batches ADD COLUMN dialect TEXT;
UPDATE study_batches SET dialect = 'homeric' WHERE kind IN ('forms', 'parts');

COMMIT;

PRAGMA foreign_keys = ON;
