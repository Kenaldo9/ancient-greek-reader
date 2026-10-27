"""
greek export-data: the database's public export. A copy without the study
batches' bookkeeping (their requests, usage and cost), vacuumed, checked and
compressed with xz, beside its SHA-256, a manifest and DATA-LICENSE.md.

The live database here is a file in WAL mode, as the app keeps it, with rows
in every table: the Iliad treebank excerpt, its study data as collected
batches leave it, the dictionary, forms, principal parts, use labels and the
batches' bookkeeping.
"""

import hashlib
import json
import lzma
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import greek.db.export as export_module
from greek.api.study import MODEL
from greek.db.connection import known_migrations, run_migrations
from greek.db.export import ExportError, export_database
from greek.db.repos.lexicon import save_lexicon
from greek.db.repos.parses import save_tb_source
from greek.db.repos.study import save_forms_result, save_uses_result
from greek.db.repos.verbs import save_verb_parts
from greek.fetch.treebank import WORKS
from greek.models import LemmaForms, LexiconEntry, TreebankSource, WordUse
from tests.integration.test_backup import _database
from tests.integration.test_notes_batch import _save_study
from tests.integration.test_word_details import TITHEMI, _import_excerpt

REPO = Path(__file__).resolve().parents[2]
FILES = ["greek.db.xz", "SHA256SUMS", "manifest.json", "DATA-LICENSE.md"]
# The batches' requests, usage and cost: the plan's list, not the module's
BOOKKEEPING = {"study_batches", "study_requests", "lemma_requests", "parts_requests"}


def _fill(db: sqlite3.Connection) -> int:
    """A row in every table; returns the Iliad's id."""
    _, text_id = _import_excerpt(db)
    save_tb_source(
        db,
        TreebankSource(
            text_id,
            "iliad",
            "perseus",
            "tlg0012.tlg001.perseus-grc1.tb.xml",
            "CC BY-SA 3.0",
        ),
    )
    _save_study(db, text_id)  # Books 1 and 2, the model's parse of Ἀΐδης
    save_lexicon(
        db,
        "LSJ",
        [LexiconEntry("LSJ", "mh=nis", "μῆνις", "wrath", "<p>wrath</p>")],
        {"μῆνις": ("mh=nis", "headword")},
    )
    save_forms_result(db, [LemmaForms("μῆνις", MODEL, "noun", "μῆνις, -ιος, ἡ")])
    save_verb_parts(db, "τίθημι", MODEL, TITHEMI, "homeric")
    save_uses_result(
        db,
        text_id,
        MODEL,
        1,
        1,
        [WordUse(text_id, 1, 0, MODEL, "direct object", "high", False)],
    )
    db.executescript(
        f"""
        INSERT INTO study_batches(id, text_id, scope, model, status, request_count,
            cost_usd, kind)
        VALUES ('msgbatch_study', {text_id}, '1', '{MODEL}', 'ended', 1, 1.25, 'study'),
               ('msgbatch_forms', NULL, 'lemmas', '{MODEL}', 'ended', 1, 0.5, 'forms'),
               ('msgbatch_parts', NULL, 'verbs', '{MODEL}', 'ended', 1, 0.25, 'parts');
        INSERT INTO study_requests(batch_id, custom_id, first_line, last_line,
            word_count, status, cost_usd)
        VALUES ('msgbatch_study', '{text_id}_1-5', 1, 5, 24, 'succeeded', 1.25);
        INSERT INTO lemma_requests(batch_id, custom_id, lemmas, lemma_count,
            status, cost_usd)
        VALUES ('msgbatch_forms', 'forms_1', '["μῆνις"]', 1, 'succeeded', 0.5);
        INSERT INTO parts_requests(batch_id, custom_id, verbs, verb_count, status,
            cost_usd)
        VALUES ('msgbatch_parts', 'parts_1', '["τίθημι"]', 1, 'succeeded', 0.25);
        """
    )
    db.commit()
    return text_id


@pytest.fixture
def live(tmp_path):
    """The live database, filled; and its Iliad's id."""
    db = _database(tmp_path / "data")
    text_id = _fill(db)
    yield db, text_id
    db.close()


def _export(db, folder, commit="abc1234"):
    return export_database(db, folder, commit, MODEL)


def _unpacked(folder: Path) -> sqlite3.Connection:
    """The exported database, decompressed beside the export, opened."""
    target = folder.parent / "unpacked.db"
    with lzma.open(folder / "greek.db.xz") as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _tables(db) -> list:
    """Every table with rows of its own: the search index's shadow tables and
    sqlite_sequence too, not the schema (whose pages VACUUM moves)."""
    return sorted(
        row[1]
        for row in db.execute("PRAGMA table_list")
        if row[0] == "main"
        and row[2] in ("table", "shadow")
        and row[1] not in ("sqlite_schema", "sqlite_temp_schema")
    )


def _rows(db, table) -> list:
    return sorted(
        tuple(row) for row in db.execute(f'SELECT * FROM "{table}"').fetchall()
    )


def _schema(db) -> set:
    return {
        tuple(row) for row in db.execute("SELECT type, name, sql FROM sqlite_master")
    }


# --- What the export keeps and empties ---------------------------------------------


def test_the_export_keeps_everything_but_the_batch_bookkeeping(live, tmp_path):
    db, _ = live
    tables = _tables(db)
    # Every table has rows to lose, so a table added later is checked too
    assert [
        t for t in tables if not db.execute(f'SELECT 1 FROM "{t}"').fetchone()
    ] == []
    before = {table: _rows(db, table) for table in tables}

    _export(db, tmp_path / "export")

    copy = _unpacked(tmp_path / "export")
    assert _schema(copy) == _schema(db)
    for table in tables:
        expected = [] if table in BOOKKEEPING else before[table]
        assert _rows(copy, table) == expected, table
    # The live database keeps its bookkeeping: only the copy was emptied
    for table in tables:
        assert _rows(db, table) == before[table], table


def test_the_export_is_checked_and_self_contained(live, tmp_path):
    db, _ = live
    _export(db, tmp_path / "export")

    copy = _unpacked(tmp_path / "export")
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copy.execute("PRAGMA foreign_key_check").fetchall() == []
    # Not in WAL mode, as the live database is: one file, readable as it is
    assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert copy.execute("PRAGMA freelist_count").fetchone()[0] == 0  # vacuumed
    hits = copy.execute(
        "SELECT count(*) FROM lines_fts WHERE lines_fts MATCH 'μηνιν'"
    ).fetchone()[0]
    assert hits == 1
    assert sorted(p.name for p in (tmp_path / "export").iterdir()) == sorted(FILES)


def test_the_export_opens_in_the_app_at_the_codes_version(live, tmp_path):
    from fastapi.testclient import TestClient

    from greek.web.app import app
    from greek.web.deps import get_db

    db, text_id = live
    _export(db, tmp_path / "export")
    copy = _unpacked(tmp_path / "export")

    upgrade = run_migrations(copy)
    assert upgrade.applied == [] and upgrade.backup is None

    def override_db():
        yield copy

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            library = client.get("/")
            page = client.get(
                f"/texts/{text_id}/read", params={"at": "1.1", "interlinear": "true"}
            )
            word = client.get(f"/texts/{text_id}/word", params={"line": 1, "i": 0})
    finally:
        app.dependency_overrides.clear()
    assert library.status_code == page.status_code == word.status_code == 200
    assert "Iliad" in library.text and "Complete" in library.text
    assert '<span class="w-t">gloss 1.1 0</span>' in page.text
    assert "wrath" in word.text  # LSJ's entry
    assert "AI</span> direct object</dd>" in word.text  # its use label


# --- What lies beside it ---------------------------------------------------------------


def test_the_checksum_and_the_manifest(live, tmp_path):
    db, text_id = live
    export = _export(db, tmp_path / "export", commit="abc1234")

    folder = tmp_path / "export"
    assert export.files == FILES
    asset = (folder / "greek.db.xz").read_bytes()
    asset_sha = hashlib.sha256(asset).hexdigest()
    assert (folder / "SHA256SUMS").read_text() == f"{asset_sha}  greek.db.xz\n"

    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == export.manifest
    assert manifest["file"] == {
        "file": "greek.db.xz",
        "bytes": len(asset),
        "sha256": asset_sha,
    }
    database = lzma.decompress(asset)
    assert manifest["database"] == {
        "file": "greek.db",
        "bytes": len(database),
        "sha256": hashlib.sha256(database).hexdigest(),
    }
    assert manifest["migration"] == Path(known_migrations()[-1]).stem
    assert manifest["commit"] == "abc1234"
    assert manifest["texts"] == [
        {
            "id": text_id,
            "title": "Iliad",
            "author": "Homer",
            "lines": 7,
            "treebank": "iliad",
            "treebank_licence": "CC BY-SA 3.0",
            "study_model": MODEL,
            "books_translated": ["1", "2"],
            "books_in_part": [],
        }
    ]
    copy = _unpacked(folder)
    assert manifest["tables"] == {
        table: copy.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        for table in sorted(
            row[1]
            for row in copy.execute("PRAGMA table_list")
            if row[0] == "main"
            and row[2] == "table"
            and not row[1].startswith("sqlite_")
        )
    }
    assert manifest["tables"]["study_batches"] == 0
    assert manifest["tables"]["word_study"] == 31
    assert "lines_fts_data" not in manifest["tables"]
    assert manifest["emptied"] == {
        "study_requests": 1,
        "lemma_requests": 1,
        "parts_requests": 1,
        "study_batches": 3,
    }
    assert (folder / "DATA-LICENSE.md").read_bytes() == (
        REPO / "DATA-LICENSE.md"
    ).read_bytes()


@pytest.mark.skipif(shutil.which("xz") is None, reason="no xz command")
def test_xz_reads_the_export(live, tmp_path):
    db, _ = live
    _export(db, tmp_path / "export")
    result = subprocess.run(
        ["xz", "--test", str(tmp_path / "export" / "greek.db.xz")],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_the_data_licence_names_every_treebank_and_ships_in_the_image():
    licence = (REPO / "DATA-LICENSE.md").read_text()
    for work in WORKS.values():
        assert work.name.removeprefix("the ") in licence, work.key
    for source in ("canonical-greekLit", "treebank_data", "glaux", "lexica"):
        assert source in licence
    for hand in ("PROIEL", "Gorman"):
        assert hand in licence
    assert "COPY DATA-LICENSE.md ." in (REPO / "Dockerfile").read_text()


# --- Never overwriting, and failing cleanly ------------------------------------------


def test_an_empty_folder_will_do(live, tmp_path):
    db, _ = live
    folder = tmp_path / "export"
    folder.mkdir()
    _export(db, folder)
    assert sorted(p.name for p in folder.iterdir()) == sorted(FILES)


def test_a_folder_with_files_is_left_alone(live, tmp_path):
    db, _ = live
    folder = tmp_path / "export"
    folder.mkdir()
    (folder / "greek.db.xz").write_bytes(b"last month's")

    with pytest.raises(ExportError, match="is not a new or empty folder"):
        _export(db, folder)

    assert [p.name for p in folder.iterdir()] == ["greek.db.xz"]
    assert (folder / "greek.db.xz").read_bytes() == b"last month's"


def test_a_file_is_not_a_folder(live, tmp_path):
    db, _ = live
    target = tmp_path / "export"
    target.write_bytes(b"keep me")
    with pytest.raises(ExportError, match="is not a new or empty folder"):
        _export(db, target)
    assert target.read_bytes() == b"keep me"


@pytest.mark.parametrize("existing", [False, True])
def test_an_export_that_fails_leaves_nothing_behind(
    live, tmp_path, monkeypatch, existing
):
    db, _ = live
    folder = tmp_path / "export"
    if existing:
        folder.mkdir()
    monkeypatch.setattr(export_module, "_sha256_of_xz", lambda path: "0" * 64)

    with pytest.raises(ExportError, match="does not decompress"):
        _export(db, folder)

    if existing:
        assert list(folder.iterdir()) == []
    else:
        assert not folder.exists()


def test_a_missing_licence_file_stops_the_export_before_it_starts(
    live, tmp_path, monkeypatch
):
    db, _ = live
    monkeypatch.setattr(export_module, "LICENCE", tmp_path / "DATA-LICENSE.md")
    with pytest.raises(ExportError, match="is missing"):
        _export(db, tmp_path / "export")
    assert not (tmp_path / "export").exists()


# --- The command ---------------------------------------------------------------------


def _greek(tmp_path, *args):
    env = dict(os.environ, GREEK_DATA_DIR=str(tmp_path / "data"))
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HUGGINGFACE_API_KEY"):
        env[key] = ""
    return subprocess.run(
        [sys.executable, str(REPO / "greek.py"), "export-data", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_command_writes_the_export_and_says_what_it_emptied(live, tmp_path):
    db, _ = live
    db.close()
    folder = tmp_path / "out" / "export"

    result = _greek(tmp_path, str(folder))

    assert result.returncode == 0, result.stderr
    migration = Path(known_migrations()[-1]).stem
    assert result.stdout.startswith(f"Exported the database at {migration} (code ")
    assert f"to {folder}:\n  greek.db.xz  " in result.stdout
    assert "  SHA256SUMS\n  manifest.json\n  DATA-LICENSE.md\n" in result.stdout
    assert (
        "Emptied study_requests (1 rows), lemma_requests (1 rows), "
        "parts_requests (1 rows), study_batches (3 rows)." in result.stdout
    )
    assert "Compressing with xz -6" in result.stderr
    assert sorted(p.name for p in folder.iterdir()) == sorted(FILES)


def test_the_command_refuses_a_folder_with_files(live, tmp_path):
    db, _ = live
    db.close()
    folder = tmp_path / "export"
    folder.mkdir()
    (folder / "notes.txt").write_text("mine")

    result = _greek(tmp_path, str(folder))

    assert result.returncode == 1
    assert "is not a new or empty folder" in result.stderr
    assert [p.name for p in folder.iterdir()] == ["notes.txt"]
