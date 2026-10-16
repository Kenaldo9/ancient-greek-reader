import sqlite3
import shutil
import tempfile
from pathlib import Path

import pytest

from greek.db.connection import _DB_PATH, _MIGRATIONS_DIR, run_migrations


def test_tests_use_a_temporary_data_dir():
    """Tests must never open (or migrate) the real data/greek.db."""
    assert Path(tempfile.gettempdir()).resolve() in _DB_PATH.resolve().parents


def test_fresh_db_gets_all_migrations():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_migrations(conn)
    applied = {row[0] for row in conn.execute("SELECT name FROM _migrations")}
    assert "001_initial.sql" in applied
    assert "002_fts.sql" in applied
    assert "003_reference.sql" in applied
    assert "004_dialect.sql" in applied
    assert "005_word_translations.sql" in applied
    assert "006_reading_positions.sql" in applied
    assert "007_verb_parts.sql" in applied
    assert "008_questions.sql" in applied
    assert "009_search_plain.sql" in applied
    assert "010_delete_orphan_lines.sql" in applied
    assert "011_treebank.sql" in applied
    assert "012_lexicon.sql" in applied
    assert "013_study.sql" in applied
    assert "014_parts_batches.sql" in applied
    assert "015_notes_batches.sql" in applied
    assert "020_reading_app.sql" in applied


def test_migrations_idempotent():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_migrations(conn)
    run_migrations(conn)  # second run must not raise
    count = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    assert count == len(list(_MIGRATIONS_DIR.glob("*.sql")))  # no duplicates


def test_migrations_table_tracks_names():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_migrations(conn)
    names = [
        row[0] for row in conn.execute("SELECT name FROM _migrations ORDER BY name")
    ]
    assert names == sorted(names)  # applied in order


def test_migrations_creates_texts_table(db):
    tables = {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "texts" in tables


def test_migrations_creates_lines_table(db):
    tables = {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "lines" in tables


# The reading app (020) keeps no one user's state and no one-off model output
DROPPED = (
    "analyses",
    "history",
    "questions",
    "bookmarks",
    "reading_positions",
    "word_translations",
)


def _tables(conn):
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def test_a_fresh_database_has_no_tables_of_one_user_or_one_off_answers(db):
    assert not _tables(db) & set(DROPPED)


def test_the_reading_app_migration_keeps_texts_and_study_data(tmp_path, monkeypatch):
    """A database at 019 with rows in every dropped table, and principal parts
    from a parts batch (homeric) and from the Verbs page ('')."""
    import greek.db.connection as connection

    old_dir = tmp_path / "old-migrations"
    old_dir.mkdir()
    for migration in _MIGRATIONS_DIR.glob("*.sql"):
        if migration.name < "020":
            shutil.copyfile(migration, old_dir / migration.name)
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    with monkeypatch.context() as patch:
        patch.setattr(connection, "_MIGRATIONS_DIR", old_dir)
        run_migrations(conn)
    conn.executescript(
        """
        INSERT INTO texts(id, title, filename, filepath, line_count)
            VALUES (1, 'Iliad', 'i.txt', '/d/i.txt', 1);
        INSERT INTO lines(text_id, line_number, reference, content, plain)
            VALUES (1, 1, '1.1', 'Μῆνιν ἄειδε θεά', 'μηνιν αειδε θεα');
        INSERT INTO analyses(passage_hash, source_text_id, line_start, line_end,
            raw_passage, fluent_translation, literal_translation,
            grammatical_json, model_used)
            VALUES ('saved', 1, 1, 1, 'Μῆνιν ἄειδε θεά', 'Sing', 'Wrath', '[]', 'm');
        INSERT INTO history(analysis_id) VALUES (1);
        INSERT INTO word_translations(passage_hash, source_text_id, line_start,
            line_end, words_json, model_used) VALUES ('gloss', 1, 1, 1, '[]', 'm');
        INSERT INTO bookmarks(text_id, line_start, line_end, note)
            VALUES (1, 1, 1, 'Opening');
        INSERT INTO reading_positions(text_id, line_number) VALUES (1, 1);
        INSERT INTO questions(text_id, line_start, line_end, question, answer,
            model_used) VALUES (1, 1, 1, 'Why?', 'Because.', 'm');
        INSERT INTO verb_parts(verb, dialect, model_used, parts_json)
            VALUES ('λύω', 'homeric', 'm', '[]'), ('ἄγω', '', 'm', '[]');
        INSERT INTO line_translations(text_id, line_number, literal, model_used)
            VALUES (1, 1, 'Wrath sing, goddess', 'm');
        """
    )
    conn.commit()

    run_migrations(conn)

    assert not _tables(conn) & set(DROPPED)
    assert conn.execute("SELECT title FROM texts").fetchall() == [("Iliad",)]
    assert conn.execute("SELECT literal FROM line_translations").fetchall() == [
        ("Wrath sing, goddess",)
    ]
    assert conn.execute("SELECT verb, dialect FROM verb_parts").fetchall() == [
        ("λύω", "homeric")
    ]
    assert (
        conn.execute(
            "SELECT count(*) FROM lines_fts WHERE lines_fts MATCH 'μηνιν'"
        ).fetchone()[0]
        == 1
    )
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_migrations_lines_has_reference_column(db):
    cols = {row[1] for row in db.execute("PRAGMA table_info(lines)")}
    assert "reference" in cols


@pytest.mark.parametrize(
    "old_version", ["005_word_translations.sql", "009_search_plain.sql"]
)
def test_cleanup_upgrade_preserves_saved_data_and_search(
    tmp_path, monkeypatch, old_version
):
    """Upgrade old and redesigned DBs, including legacy lines left with FK off."""
    import greek.db.connection as connection

    old_dir = tmp_path / "old-migrations"
    old_dir.mkdir()
    for migration in _MIGRATIONS_DIR.glob("*.sql"):
        if migration.name <= old_version:
            shutil.copyfile(migration, old_dir / migration.name)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with monkeypatch.context() as patch:
        patch.setattr(connection, "_MIGRATIONS_DIR", old_dir)
        run_migrations(conn)
    conn.execute(
        "INSERT INTO texts(id, title, filename, filepath, line_count) VALUES (1, 'Iliad', 'i.txt', '/d/i.txt', 1)"
    )
    conn.execute(
        "INSERT INTO lines(text_id, line_number, reference, content) VALUES (1, 1, '1.1', 'Μῆνιν ἄειδε θεά')"
    )
    conn.execute(
        "INSERT INTO analyses(passage_hash, source_text_id, line_start, line_end, raw_passage, fluent_translation, literal_translation, grammatical_json, model_used) VALUES ('saved', 1, 1, 1, 'Μῆνιν ἄειδε θεά', 'Sing', 'Wrath', '[]', 'test')"
    )
    conn.execute(
        "INSERT INTO bookmarks(text_id, line_start, line_end, note) VALUES (1, 1, 1, 'Opening')"
    )
    conn.execute("INSERT INTO history(analysis_id) VALUES (1)")
    conn.execute(
        "INSERT INTO word_translations(passage_hash, source_text_id, line_start, line_end, words_json, model_used) VALUES ('gloss', 1, 1, 1, '[]', 'test')"
    )
    saved_tables = ["texts", "analyses", "bookmarks", "history", "word_translations"]
    if old_version == "009_search_plain.sql":
        conn.execute("UPDATE lines SET plain=greek_plain(content)")
        conn.execute(
            "INSERT INTO reading_positions(text_id, line_number) VALUES (1, 1)"
        )
        conn.execute(
            "INSERT INTO questions(text_id, line_start, line_end, question, answer, model_used) VALUES (1, 1, 1, 'Why?', 'Because.', 'test')"
        )
        conn.execute(
            "INSERT INTO verb_parts(verb, model_used, parts_json) VALUES ('λύω', 'test', '[]')"
        )
        saved_tables += ["reading_positions", "questions", "verb_parts"]
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    # Include a missing ID outside the original list: cleanup follows the FK,
    # while the valid text at legacy ID 1 must survive.
    conn.executemany(
        "INSERT INTO lines(text_id, line_number, content) VALUES (?, 1, 'ὀρφανός')",
        [(2,), (999,)],
    )
    if old_version == "009_search_plain.sql":
        conn.execute("UPDATE lines SET plain=greek_plain(content) WHERE text_id!=1")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    # the columns each table had; later migrations may add more (019 adds
    # verb_parts.dialect)
    columns = {
        table: ", ".join(r[1] for r in conn.execute(f"PRAGMA table_info({table})"))
        for table in saved_tables
    }
    before = {
        table: [
            tuple(row) for row in conn.execute(f"SELECT {columns[table]} FROM {table}")
        ]
        for table in saved_tables
    }

    run_migrations(conn)
    run_migrations(conn)

    assert conn.execute("SELECT text_id FROM lines").fetchall()[0][0] == 1
    assert conn.execute("SELECT count(*) FROM lines").fetchone()[0] == 1
    # the text is kept; 020 drops the one-user tables and the Verbs page's lookup
    after = conn.execute(f"SELECT {columns['texts']} FROM texts")
    assert [tuple(row) for row in after] == before["texts"]
    assert conn.execute("SELECT count(*) FROM verb_parts").fetchone()[0] == 0
    assert conn.execute("SELECT plain FROM lines").fetchone()[0] == "μηνιν αειδε θεα"
    assert (
        conn.execute(
            "SELECT count(*) FROM lines_fts WHERE lines_fts MATCH 'μηνιν'"
        ).fetchone()[0]
        == 1
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM lines_fts WHERE lines_fts MATCH 'ορφανοσ'"
        ).fetchone()[0]
        == 0
    )
    conn.execute("INSERT INTO lines_fts(lines_fts, rank) VALUES ('integrity-check', 1)")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "verb_parts" in tables
    assert not tables & set(DROPPED)
    conn.close()
