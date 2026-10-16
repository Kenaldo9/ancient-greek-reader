-- Word details (docs/word-details/plan.md, Pilot 2): how a word works in its
-- sentence ("dative of disadvantage", "optative of wish"), from a model, made
-- per translation unit in Message Batches (greek study uses) and recorded in
-- study_requests like notes batches. Back up an existing database before
-- applying this migration.

-- A model's use label for a word, at the effort its batch ran at. extra = 1
-- for a word the request didn't mark but the model labelled anyway (an
-- accusative of respect the treebank calls an object).
CREATE TABLE IF NOT EXISTS word_uses (
    text_id     INTEGER NOT NULL REFERENCES texts(id) ON DELETE CASCADE,
    line_number INTEGER NOT NULL,
    token_index INTEGER NOT NULL,
    model_used  TEXT    NOT NULL,
    use_label   TEXT    NOT NULL,
    effort      TEXT    NOT NULL,
    extra       INTEGER NOT NULL DEFAULT 0 CHECK (extra IN (0, 1)),
    made_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (text_id, line_number, token_index, model_used)
);
