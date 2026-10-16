CREATE VIRTUAL TABLE IF NOT EXISTS lines_fts
USING fts5(content, content='lines', content_rowid='id', tokenize='unicode61');

CREATE TRIGGER IF NOT EXISTS lines_ai AFTER INSERT ON lines BEGIN
    INSERT INTO lines_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS lines_ad AFTER DELETE ON lines BEGIN
    INSERT INTO lines_fts(lines_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS lines_au AFTER UPDATE ON lines BEGIN
    INSERT INTO lines_fts(lines_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO lines_fts(rowid, content) VALUES (new.id, new.content);
END;
