ALTER TABLE lines ADD COLUMN reference TEXT;
CREATE INDEX IF NOT EXISTS idx_lines_reference ON lines(text_id, reference);
