"""
greek backup: a consistent, checked copy of the database, to a new file or to
standard output, while the web app keeps running. A server's copy is fetched
with `ssh HOST docker exec CONTAINER python greek.py backup - > greek.db`.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from click.testing import CliRunner

from greek.cli.backup_cmd import backup_cmd
from greek.db.connection import run_migrations
from greek.db.repos.texts import insert_text

REPO = Path(__file__).resolve().parents[2]


def _database(data: Path) -> sqlite3.Connection:
    """A migrated database file in WAL mode, as the app keeps it."""
    data.mkdir(exist_ok=True)
    conn = sqlite3.connect(data / "greek.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    run_migrations(conn)
    return conn


def _greek(tmp_path, *args, stdout=subprocess.PIPE):
    temp = tmp_path / "temp"
    temp.mkdir(exist_ok=True)
    env = dict(os.environ, GREEK_DATA_DIR=str(tmp_path / "data"), TMPDIR=str(temp))
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HUGGINGFACE_API_KEY"):
        env[key] = ""
    return subprocess.run(
        [sys.executable, str(REPO / "greek.py"), "backup", *args],
        cwd=REPO,
        env=env,
        stdout=stdout,
        stderr=subprocess.PIPE,
        timeout=60,
    )


def _opened(path: Path) -> sqlite3.Connection:
    copy = sqlite3.connect(path)
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    return copy


def test_a_copy_to_standard_output_is_the_whole_database(tmp_path):
    conn = _database(tmp_path / "data")
    text_id = insert_text(conn, "Iliad", "Homer", "iliad.txt", "/data/iliad.txt", 0)
    conn.close()

    result = _greek(tmp_path, "-")

    assert result.returncode == 0, result.stderr.decode()
    saved = tmp_path / "downloaded.db"
    saved.write_bytes(result.stdout)
    assert _opened(saved).execute("SELECT id, title FROM texts").fetchall() == [
        (text_id, "Iliad")
    ]
    assert list((tmp_path / "temp").iterdir()) == []


def test_the_copy_has_what_another_connection_committed_to_the_wal(tmp_path):
    writer = _database(tmp_path / "data")
    writer.execute("PRAGMA wal_autocheckpoint = 0")  # keep the commit in the WAL
    insert_text(writer, "Odyssey", "Homer", "odyssey.txt", "/data/odyssey.txt", 0)
    assert (tmp_path / "data" / "greek.db-wal").stat().st_size > 0

    result = _greek(tmp_path, "-")
    writer.close()

    assert result.returncode == 0, result.stderr.decode()
    saved = tmp_path / "downloaded.db"
    saved.write_bytes(result.stdout)
    assert _opened(saved).execute("SELECT title FROM texts").fetchall() == [
        ("Odyssey",)
    ]


def test_a_copy_to_a_new_file(tmp_path):
    conn = _database(tmp_path / "data")
    insert_text(conn, "Iliad", "Homer", "iliad.txt", "/data/iliad.txt", 0)
    conn.close()
    target = tmp_path / "copy.db"

    result = _greek(tmp_path, str(target))

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b""
    assert _opened(target).execute("SELECT title FROM texts").fetchall() == [("Iliad",)]


def test_an_existing_file_is_never_overwritten(tmp_path):
    _database(tmp_path / "data").close()
    target = tmp_path / "copy.db"
    target.write_bytes(b"keep me")

    result = _greek(tmp_path, str(target))

    assert result.returncode == 1
    assert b"already exists" in result.stderr
    assert target.read_bytes() == b"keep me"


def test_a_copy_that_fails_leaves_nothing_behind(db, monkeypatch, tmp_path):
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setattr("greek.cli.backup_cmd.get_connection", lambda: db)

    def fail(conn, target):
        target.write_bytes(b"half a copy")
        raise RuntimeError(f"The copy {target} failed its integrity check: page 3")

    monkeypatch.setattr("greek.cli.backup_cmd.copy_database", fail)

    result = CliRunner().invoke(backup_cmd, ["-"])

    assert isinstance(result.exception, RuntimeError)
    assert result.stdout_bytes == b""
    assert list(temp.iterdir()) == []
