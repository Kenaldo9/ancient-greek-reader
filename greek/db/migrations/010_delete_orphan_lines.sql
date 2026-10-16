-- Remove legacy lines imported before foreign-key enforcement. Deleting through
-- lines lets its AFTER DELETE trigger remove the corresponding FTS entries.
-- Back up an existing database before applying this migration.
DELETE FROM lines
WHERE NOT EXISTS (SELECT 1 FROM texts WHERE texts.id = lines.text_id);
