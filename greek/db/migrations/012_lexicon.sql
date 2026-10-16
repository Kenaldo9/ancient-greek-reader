-- Dictionary entries for treebank lemmas (greek study import-lsj). Back up an
-- existing database before applying this migration.

-- Entries of a dictionary that some lemma needs: the source's key (Beta Code
-- for LSJ, e.g. "ba/llw"), the headword in Unicode, a short definition, and
-- the whole entry as HTML.
CREATE TABLE IF NOT EXISTS lexicon_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT    NOT NULL,
    key        TEXT    NOT NULL,
    headword   TEXT    NOT NULL,
    short_def  TEXT    NOT NULL,
    entry_html TEXT    NOT NULL,
    UNIQUE(source, key)
);

-- The dictionary entry for each lemma in word_parses, and how it was found
-- (headword, variant, form, alias, spelling, cross-reference); or, when the
-- dictionary lacks the lemma, a model's short identification. A lemma with
-- neither is still to be identified.
CREATE TABLE IF NOT EXISTS lemma_entries (
    lemma      TEXT    PRIMARY KEY,
    entry_id   INTEGER REFERENCES lexicon_entries(id),
    matched_by TEXT,
    model_def  TEXT,
    model_used TEXT,
    CHECK ((entry_id IS NULL) = (matched_by IS NULL)),
    CHECK ((model_def IS NULL) = (model_used IS NULL))
);
