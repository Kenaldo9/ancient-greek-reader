-- Accent-insensitive search. unicode61 folds case but not Greek accents, so
-- each line gets an accent-free lowercase copy (`plain`, filled here by
-- greek_plain(), which run_migrations() registers, and by insert_lines() for
-- new lines) and the full-text index is rebuilt over that copy.
DROP TRIGGER IF EXISTS lines_ai;
DROP TRIGGER IF EXISTS lines_ad;
DROP TRIGGER IF EXISTS lines_au;
DROP TABLE IF EXISTS lines_fts;

ALTER TABLE lines ADD COLUMN plain TEXT;
UPDATE lines SET plain = greek_plain(content);

CREATE VIRTUAL TABLE lines_fts
USING fts5(plain, content='lines', content_rowid='id', tokenize='unicode61');
INSERT INTO lines_fts(lines_fts) VALUES ('rebuild');

CREATE TRIGGER lines_ai AFTER INSERT ON lines BEGIN
    INSERT INTO lines_fts(rowid, plain) VALUES (new.id, new.plain);
END;
CREATE TRIGGER lines_ad AFTER DELETE ON lines BEGIN
    INSERT INTO lines_fts(lines_fts, rowid, plain) VALUES ('delete', old.id, old.plain);
END;
CREATE TRIGGER lines_au AFTER UPDATE ON lines BEGIN
    INSERT INTO lines_fts(lines_fts, rowid, plain) VALUES ('delete', old.id, old.plain);
    INSERT INTO lines_fts(rowid, plain) VALUES (new.id, new.plain);
END;
