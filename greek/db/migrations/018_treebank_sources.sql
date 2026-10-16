-- Treebanks for every text (docs/all-texts/plan.md, Phase 1): which treebank a
-- text's words come from, and whether each sentence was annotated by hand or
-- automatically. Back up an existing database before applying this migration.

-- One row per text with treebank data, written by `greek study
-- import-treebank`. work names the treebank (iliad, odyssey, herodotus,
-- thucydides) and selects the text's labels; corpus is perseus (the Perseus
-- Ancient Greek Dependency Treebank) or glaux (the GLAUx corpus). A text
-- imported before this migration has no row until its import is run again
-- (with --nodes-only, nothing else changes); the reader then treats it as the
-- Perseus treebank, as before.
CREATE TABLE IF NOT EXISTS tb_sources (
    text_id     INTEGER PRIMARY KEY REFERENCES texts(id) ON DELETE CASCADE,
    work        TEXT    NOT NULL,
    corpus      TEXT    NOT NULL CHECK (corpus IN ('perseus', 'glaux')),
    filename    TEXT    NOT NULL,
    licence     TEXT    NOT NULL,
    imported_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- GLAUx marks each sentence as annotated by hand (manual) or by its parser
-- (auto); every Perseus sentence is manual.
ALTER TABLE tb_sentences ADD COLUMN annotation TEXT NOT NULL DEFAULT 'manual'
    CHECK (annotation IN ('manual', 'auto'));
