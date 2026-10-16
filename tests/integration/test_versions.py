"""
Which code is running and which database it uses (greek/version.py), and the
migration guard in run_migrations(): an existing database is backed up before
new migrations, and one that is newer than the code is left alone.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import greek.db.connection as connection
from greek import version
from greek.db.connection import (
    DatabaseNewerThanCode,
    Upgrade,
    applied_migrations,
    known_migrations,
    run_migrations,
)
from greek.version import git_commit, read_versions, version_text

REPO = Path(__file__).resolve().parents[2]
NAMES = known_migrations()
LAST = Path(NAMES[-1]).stem
TODAY = date.today().isoformat()


def _old_db(tmp_path, monkeypatch, upto=NAMES[-2]) -> Path:
    """A greek.db file with the migrations up to `upto` and a saved text."""
    old = tmp_path / "old-migrations"
    old.mkdir()
    for name in NAMES:
        if name <= upto:
            shutil.copyfile(connection._MIGRATIONS_DIR / name, old / name)
    path = tmp_path / "greek.db"
    conn = sqlite3.connect(path)
    with monkeypatch.context() as patch:
        patch.setattr(connection, "_MIGRATIONS_DIR", old)
        run_migrations(conn)
    conn.execute(
        "INSERT INTO texts(title, filename, filepath, line_count) VALUES ('Iliad', 'i.txt', '/d/i.txt', 1)"
    )
    conn.commit()
    conn.close()
    return path


def _backups(tmp_path):
    return sorted(p.name for p in tmp_path.glob("greek-backup-*.db"))


# -- the migration guard ------------------------------------------------------


def test_an_existing_database_is_backed_up_before_new_migrations(tmp_path, monkeypatch):
    path = _old_db(tmp_path, monkeypatch)
    conn = sqlite3.connect(path)

    upgrade = run_migrations(conn)

    assert upgrade.applied == [NAMES[-1]]
    assert upgrade.backup == tmp_path / f"greek-backup-{TODAY}-pre-{LAST}.db"
    # the copy is the database as it was, saved data and all
    backup = sqlite3.connect(upgrade.backup)
    assert applied_migrations(backup) == NAMES[:-1]
    assert backup.execute("SELECT title FROM texts").fetchone()[0] == "Iliad"
    assert applied_migrations(conn) == NAMES


def test_no_backup_when_there_is_nothing_to_lose_or_to_apply(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "greek.db")
    assert run_migrations(conn) == Upgrade(applied=NAMES)  # a new database
    assert run_migrations(conn) == Upgrade()  # nothing new
    # a database in memory has no file to copy
    old = tmp_path / "old-migrations"
    old.mkdir()
    shutil.copyfile(connection._MIGRATIONS_DIR / NAMES[0], old / NAMES[0])
    memory = sqlite3.connect(":memory:")
    with monkeypatch.context() as patch:
        patch.setattr(connection, "_MIGRATIONS_DIR", old)
        run_migrations(memory)
    assert run_migrations(memory) == Upgrade(applied=NAMES[1:])
    assert _backups(tmp_path) == []


def test_an_earlier_backup_of_the_day_is_kept(tmp_path, monkeypatch):
    path = _old_db(tmp_path, monkeypatch, upto=NAMES[-3])
    stem = Path(NAMES[-2]).stem
    earlier = tmp_path / f"greek-backup-{TODAY}-pre-{stem}.db"
    earlier.write_text("an earlier try")

    upgrade = run_migrations(sqlite3.connect(path))

    assert upgrade.applied == NAMES[-2:]
    assert upgrade.backup.name == f"greek-backup-{TODAY}-pre-{stem}-2.db"
    assert earlier.read_text() == "an earlier try"


def test_a_database_newer_than_the_code_is_left_alone(tmp_path, monkeypatch):
    path = _old_db(tmp_path, monkeypatch)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO _migrations(name) VALUES ('999_future.sql')")
    conn.commit()

    with pytest.raises(DatabaseNewerThanCode, match=r"\(999_future\.sql\)"):
        run_migrations(conn)

    # not even this code's missing migration is applied
    assert NAMES[-1] not in applied_migrations(conn)
    assert _backups(tmp_path) == []


def test_no_migration_without_its_backup(tmp_path, monkeypatch):
    path = _old_db(tmp_path, monkeypatch)
    conn = sqlite3.connect(path)

    def full_disk(conn, before):
        raise OSError("No space left on device")

    monkeypatch.setattr(connection, "backup_database", full_disk)
    with pytest.raises(OSError):
        run_migrations(conn)
    assert applied_migrations(conn) == NAMES[:-1]


# -- the code's version -------------------------------------------------------

SHA = "13b8682a0c4f6f2d6c1d7e0e5b9f3a1c2d3e4f50"


def _checkout(root: Path, head: str) -> Path:
    (root / ".git" / "refs" / "heads" / "feat").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text(head + "\n")
    return root / ".git"


def test_git_commit_reads_a_branch_from_its_ref_or_packed_refs(tmp_path):
    git = _checkout(tmp_path, "ref: refs/heads/feat/version")
    assert git_commit(tmp_path) is None  # a branch with no commits
    (git / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{SHA} refs/heads/feat/version\n"
    )
    assert git_commit(tmp_path) == "13b8682"
    (git / "refs" / "heads" / "feat" / "version").write_text("f" * 40 + "\n")
    assert git_commit(tmp_path) == "fffffff"  # a loose ref is newer than packed-refs


def test_git_commit_reads_a_detached_head_and_a_worktree(tmp_path):
    _checkout(tmp_path / "repo", SHA)
    assert git_commit(tmp_path / "repo") == "13b8682"

    # a worktree: its own HEAD, the branches in the main repository
    main = _checkout(tmp_path / "main", "ref: refs/heads/main")
    (main / "refs" / "heads" / "main").write_text(SHA + "\n")
    tree_git = main / "worktrees" / "wt"
    tree_git.mkdir(parents=True)
    (tree_git / "HEAD").write_text("ref: refs/heads/main\n")
    (tree_git / "commondir").write_text("../..\n")
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / ".git").write_text(f"gitdir: {tree_git}\n")
    assert git_commit(tmp_path / "wt") == "13b8682"

    assert git_commit(tmp_path / "nowhere") is None


def test_the_docker_build_records_its_commit(tmp_path, monkeypatch):
    git = _checkout(tmp_path, "ref: refs/heads/main")
    (git / "refs" / "heads" / "main").write_text(SHA + "\n")
    build_file = tmp_path / "build.json"
    monkeypatch.setattr(version, "BUILD_FILE", build_file)

    version.write_build_file(tmp_path, build_file)

    build = json.loads(build_file.read_text())
    assert build["commit"] == "13b8682"
    built = datetime.strptime(build["built"], "%Y-%m-%d %H:%M:%S")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert timedelta(0) <= now - built < timedelta(minutes=1)  # UTC
    assert version.code_version() == build  # the record wins over .git


def test_a_checkout_reads_its_commit_from_git(tmp_path, monkeypatch):
    monkeypatch.setattr(version, "BUILD_FILE", tmp_path / "no-build.json")
    assert version.code_version() == {"commit": git_commit(REPO), "built": None}


# -- both versions ------------------------------------------------------------


def test_read_versions_reads_the_database_without_migrating_it(tmp_path, monkeypatch):
    monkeypatch.setattr(
        version, "code_version", lambda: {"commit": "13b8682", "built": None}
    )
    path = tmp_path / "greek.db"

    none = read_versions(path)
    assert (none.database, none.pending, path.exists()) == (None, NAMES, False)
    assert version_text(path).splitlines() == [
        "code      13b8682",
        f"database  none yet ({path})",
    ]

    _old_db(tmp_path, monkeypatch)
    old = read_versions(path)
    assert old.database == Path(NAMES[-2]).stem
    assert old.pending == [NAMES[-1]]
    assert version_text(path).splitlines()[1] == (
        f"database  {Path(NAMES[-2]).stem} ({path}); the next start applies {NAMES[-1]}"
    )
    assert applied_migrations(sqlite3.connect(path)) == NAMES[:-1]  # untouched

    conn = sqlite3.connect(path)
    run_migrations(conn)
    conn.execute("INSERT INTO _migrations(name) VALUES ('999_future.sql')")
    conn.commit()
    newer = read_versions(path)
    assert (newer.database, newer.known, newer.unknown) == (
        "999_future",
        LAST,
        ["999_future.sql"],
    )
    assert newer.database_number == "999"
    assert version_text(path).splitlines()[1] == (
        f"database  999_future ({path}), newer than this code: it has 999_future.sql"
    )


def test_version_text_shows_the_docker_build_time_in_local_time(monkeypatch, tmp_path):
    monkeypatch.setattr(
        version,
        "code_version",
        lambda: {"commit": "13b8682", "built": "2026-10-13 07:02:11"},
    )
    local = datetime(2026, 10, 13, 7, 2, 11, tzinfo=timezone.utc).astimezone()
    assert version_text(tmp_path / "greek.db").splitlines()[0] == (
        f"code      13b8682 (built {local:%Y-%m-%d %H:%M})"
    )


# -- the CLI ------------------------------------------------------------------


def _greek(tmp_path, *args):
    env = dict(os.environ, GREEK_DATA_DIR=str(tmp_path))
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HUGGINGFACE_API_KEY"):
        env[key] = ""
    return subprocess.run(
        [sys.executable, str(REPO / "greek.py"), *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_greek_version_shows_both_without_making_a_database(tmp_path):
    result = _greek(tmp_path, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"code      {version.code_version()['commit'] or 'unknown commit'}",
        f"database  none yet ({tmp_path / 'greek.db'})",
    ]
    assert not (tmp_path / "greek.db").exists()


def test_a_command_backs_up_the_database_before_migrating_it(tmp_path, monkeypatch):
    _old_db(tmp_path, monkeypatch)
    result = _greek(tmp_path, "library")
    assert result.returncode == 0, result.stderr
    backup = tmp_path / f"greek-backup-{TODAY}-pre-{LAST}.db"
    assert (
        f"Backed up the database to {backup}, then applied {NAMES[-1]}."
        in result.stderr
    )
    assert backup.exists()


def test_a_command_refuses_a_database_newer_than_the_code(tmp_path, monkeypatch):
    path = _old_db(tmp_path, monkeypatch)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO _migrations(name) VALUES ('999_future.sql')")
    conn.commit()
    conn.close()

    result = _greek(tmp_path, "library")

    assert result.returncode == 1
    assert "Error: " in result.stderr
    assert "has migrations this code doesn't have (999_future.sql)" in result.stderr
    assert "Iliad" not in result.stdout
    assert NAMES[-1] not in applied_migrations(sqlite3.connect(path))


# -- the web app --------------------------------------------------------------


def test_version_route(client):
    data = client.get("/version").json()
    assert data["code"] == version.code_version()
    assert data["database"] == {
        "version": LAST,
        "code_knows": LAST,
        "pending": [],
        "newer_than_code": [],
    }
    assert data["serving"] is True


def test_library_and_the_drawer_show_the_versions(client, db_text):
    _, text_id = db_text
    commit = version.code_version()["commit"] or "unknown"
    number = LAST.split("_")[0]
    for html in (
        client.get("/library").text,
        client.get(f"/texts/{text_id}/read").text,
    ):
        assert f'Code <span class="ref">{commit}</span>' in html
        assert f'Database <span class="ref">{number}</span></p>' in html


def test_the_version_line_warns_of_a_newer_database(client, tmp_path, monkeypatch):
    path = _old_db(tmp_path, monkeypatch, upto=NAMES[-1])
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO _migrations(name) VALUES ('999_future.sql')")
    conn.commit()
    monkeypatch.setattr(connection, "_DB_PATH", path)
    monkeypatch.setattr(
        version,
        "code_version",
        lambda: {"commit": "13b8682", "built": "2026-10-13 07:02:11"},
    )
    local = datetime(2026, 10, 13, 7, 2, 11, tzinfo=timezone.utc).astimezone()

    html = client.get("/library").text

    assert f"· built {local.day} {local:%b %Y %H:%M}" in html
    assert (
        'Database <span class="ref">999</span><span class="version-warn">, newer than this code: rebuild</span>'
        in html
    )


def test_a_newer_database_closes_every_page_but_says_why(client, monkeypatch):
    import greek.web.app as web_app

    message = "/data/greek.db has migrations this code doesn't have (999_future.sql): newer code has upgraded it."
    monkeypatch.setattr(web_app, "startup_error", message)

    for page in ("/", "/library", "/texts/1/read", "/verbs"):
        response = client.get(page, follow_redirects=False)
        assert response.status_code == 503
        assert "The database is newer than this code" in response.text
        assert "(999_future.sql)" in response.text
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/version").json()["serving"] is False
