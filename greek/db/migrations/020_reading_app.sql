-- A reading app (docs/public-release/plan.md, Phase 1): the web app and the
-- CLI no longer call models one passage at a time, and keep no one user's
-- state. Their tables go: saved analyses and their history, interlinear
-- glosses made on screen, Ask answers, bookmarks and reading positions (now
-- in each browser). The study data made in batches stays, and so do the
-- principal parts its batches made; parts looked up one verb at a time on
-- the Verbs page (dialect '') go. run_migrations() backs up an existing
-- database first.
BEGIN;

DROP TABLE IF EXISTS history;
DROP TABLE IF EXISTS questions;
DROP TABLE IF EXISTS bookmarks;
DROP TABLE IF EXISTS reading_positions;
DROP TABLE IF EXISTS word_translations;
DROP TABLE IF EXISTS analyses;

DELETE FROM verb_parts WHERE dialect = '';

COMMIT;
