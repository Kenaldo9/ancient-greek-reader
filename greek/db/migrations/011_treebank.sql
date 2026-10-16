-- Perseus treebank data aligned with an imported text (greek study
-- import-treebank). Back up an existing database before applying this migration.

-- Treebank sentences as ranges of the text's sequential line numbers.
CREATE TABLE IF NOT EXISTS tb_sentences (
    text_id     INTEGER NOT NULL REFERENCES texts(id) ON DELETE CASCADE,
    sentence_id INTEGER NOT NULL,
    first_line  INTEGER NOT NULL,
    last_line   INTEGER NOT NULL,
    PRIMARY KEY (text_id, sentence_id)
);

CREATE INDEX IF NOT EXISTS idx_tb_sentences_lines ON tb_sentences(text_id, first_line);

-- One row per treebank word aligned to a word of the text (a whitespace token
-- of lines.content, counted from 0). A word the treebank splits (οὐδʼ = οὐ + δ̓)
-- has parts 0, 1, …. Every word of an imported text has a row: a word with no
-- treebank word (a variant reading, a line the treebank omits) has sentence_id
-- NULL. A missing parse has lemma or postag NULL until a model supplies one,
-- which sets source = 'model' and model_used.
CREATE TABLE IF NOT EXISTS word_parses (
    text_id     INTEGER NOT NULL REFERENCES texts(id) ON DELETE CASCADE,
    line_number INTEGER NOT NULL,
    token_index INTEGER NOT NULL,
    part        INTEGER NOT NULL DEFAULT 0,
    form        TEXT    NOT NULL,
    lemma       TEXT,
    postag      TEXT,
    sentence_id INTEGER,
    source      TEXT    NOT NULL CHECK (source IN ('treebank', 'model')),
    model_used  TEXT,
    CHECK ((source = 'model') = (model_used IS NOT NULL)),
    PRIMARY KEY (text_id, line_number, token_index, part)
);
