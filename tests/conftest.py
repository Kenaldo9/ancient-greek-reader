import atexit
import os
import shutil
import sqlite3
import tempfile

# Before anything imports greek.db.connection, which reads GREEK_DATA_DIR once:
# point the app at a throwaway data directory, so nothing a test does can touch
# the real data/greek.db (importing greek.web.app runs the migrations).
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="greek-tests-")
os.environ["GREEK_DATA_DIR"] = _TEST_DATA_DIR
atexit.register(shutil.rmtree, _TEST_DATA_DIR, ignore_errors=True)

import pytest  # noqa: E402
from greek.db.connection import run_migrations  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_api_keys(monkeypatch):
    """
    Hide the API keys that load_dotenv() reads from the developer's .env, so
    no test depends on them (or could reach a real API).
    """
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HUGGINGFACE_API_KEY"):
        monkeypatch.setenv(key, "")


@pytest.fixture
def db():
    """Fresh in-memory SQLite DB with all migrations applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    run_migrations(conn)
    return conn


@pytest.fixture
def db_with_text(db):
    """DB with one text and 10 lines inserted."""
    from greek.db.repos.texts import insert_text
    from greek.db.repos.lines import insert_lines

    text_id = insert_text(
        db, "Iliad", "Homer", "homer_iliad.txt", "/data/texts/homer_iliad.txt", 10
    )
    insert_lines(
        db,
        text_id,
        [f"Line {i}" for i in range(1, 11)],
        [
            f"{book}.{line}"
            for book, line in [
                (1, 1),
                (1, 2),
                (1, 3),
                (1, 4),
                (1, 5),
                (2, 1),
                (2, 2),
                (2, 3),
                (2, 4),
                (2, 5),
            ]
        ],
    )
    return db, text_id
