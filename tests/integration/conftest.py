"""Fixtures for the web route tests: an in-memory DB behind FastAPI's TestClient."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from greek.db.connection import run_migrations
from greek.db.repos.lines import insert_lines
from greek.db.repos.texts import insert_text


@pytest.fixture
def web_db():
    """Fresh in-memory DB with all migrations applied.

    check_same_thread=False is required because FastAPI's TestClient runs
    route handlers in a worker thread while the DB is created in the test thread.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    run_migrations(conn)
    return conn


@pytest.fixture
def client(web_db):
    """TestClient with get_db overridden to use the in-memory web_db."""
    # Importing the app runs its startup migrations; tests/conftest.py points
    # GREEK_DATA_DIR at a temporary directory, so they never touch data/.
    from greek.web.app import app
    from greek.web.deps import get_db

    def override_db():
        yield web_db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def db_text(web_db):
    """Insert one text with 5 lines into web_db; return (db, text_id)."""
    text_id = insert_text(web_db, "Iliad", "Homer", "iliad.txt", "/data/iliad.txt", 5)
    insert_lines(
        web_db,
        text_id,
        ["Μῆνιν ἄειδε θεά", "Πηληϊάδεω Ἀχιλῆος", "οὐλομένην", "ἣ μυρί᾽", "Ἀχαιοῖς"],
        ["1.1", "1.2", "1.3", "1.4", "1.5"],
    )
    return web_db, text_id
