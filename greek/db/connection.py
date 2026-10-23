import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

from greek.search import plain

_DATA_DIR = Path(os.environ.get("GREEK_DATA_DIR", "./data"))
_DB_PATH = _DATA_DIR / "greek.db"
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class DatabaseUnusable(RuntimeError):
    """A database this code can't use as it is; the message says why."""

    heading = "The database can't be used"


class DatabaseNewerThanCode(DatabaseUnusable):
    """The database has migrations this code doesn't have: newer code upgraded it."""

    heading = "The database is newer than this code"


class DatabaseNeedsMigrations(DatabaseUnusable):
    """A read-only database lacks migrations this code has: a writable copy needs them."""

    heading = "The database needs this code's migrations"


@dataclass
class Upgrade:
    """What run_migrations() did: the migrations applied, and the backup made first."""

    applied: List[str] = field(default_factory=list)
    backup: Optional[Path] = None


def is_read_only(path: Path) -> bool:
    """Whether a database file exists and can't be written, e.g. on a read-only mount."""
    return path.is_file() and not os.access(path, os.W_OK)


def database_is_read_only() -> bool:
    """Whether this process's database is read-only: the public site's published copy."""
    return is_read_only(_DB_PATH)


def get_connection() -> sqlite3.Connection:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if is_read_only(_DB_PATH):
        # The public site's published copy (deploy/README.md): read in its own
        # journal mode, since switching to WAL is a write
        conn = sqlite3.connect(
            f"{_DB_PATH.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
        )
    else:
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def known_migrations() -> List[str]:
    """This code's migrations in order, e.g. ['001_initial.sql', …, '019_dialects.sql']."""
    return sorted(mf.name for mf in _MIGRATIONS_DIR.glob("*.sql"))


def applied_migrations(conn: sqlite3.Connection) -> List[str]:
    """The migrations a database has had, in order; [] for one never migrated."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_migrations'"
    ).fetchone():
        return []
    return [
        row[0] for row in conn.execute("SELECT name FROM _migrations ORDER BY name")
    ]


def backup_database(conn: sqlite3.Connection, before: str) -> Path:
    """
    Copy the database beside itself as greek-backup-<date>-pre-<migration>.db
    (copy_database). An earlier backup with the same name is kept: the new
    one gets -2, -3, …
    """
    source = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    name = f"{source.stem}-backup-{date.today().isoformat()}-pre-{before}"
    target = source.with_name(f"{name}.db")
    n = 2
    while target.exists():
        target = source.with_name(f"{name}-{n}.db")
        n += 1
    copy_database(conn, target)
    return target


def copy_database(conn: sqlite3.Connection, target: Path) -> None:
    """
    Copy the database to target with SQLite's backup API (a consistent copy,
    committed WAL data included, while others keep writing) and check the
    copy. A copy that fails is removed.
    """
    dest = sqlite3.connect(target)
    try:
        conn.backup(dest)
        check = dest.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"The copy {target} failed its integrity check: {check}")
    except BaseException:
        dest.close()
        target.unlink(missing_ok=True)
        raise
    dest.close()


def run_migrations(conn: sqlite3.Connection) -> Upgrade:
    """
    Apply this code's migrations that the database hasn't had. An existing
    database file is backed up first; a database that has migrations this
    code doesn't know is left alone (DatabaseNewerThanCode), and so is a
    read-only one that lacks some (DatabaseNeedsMigrations).
    """
    # Migrations may call greek_plain() to fill in the accent-free search text
    conn.create_function("greek_plain", 1, plain, deterministic=True)
    path = conn.execute("PRAGMA database_list").fetchone()[2]  # "" in memory
    applied = applied_migrations(conn)
    known = known_migrations()
    unknown = sorted(set(applied) - set(known))
    if unknown:
        raise DatabaseNewerThanCode(
            f"{path or 'The database'} has migrations this code doesn't have "
            f"({', '.join(unknown)}): newer code has upgraded it. Update this "
            "checkout, or rebuild the Docker image (docker compose up -d --build)."
        )
    pending = [name for name in known if name not in applied]
    if path and is_read_only(Path(path)):
        if pending:
            raise DatabaseNeedsMigrations(
                f"{path} is read-only and lacks this code's migrations "
                f"({', '.join(pending)}): migrate a writable copy with "
                "`greek migrate` and put it in its place."
            )
        return Upgrade()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.commit()
    upgrade = Upgrade()
    # A new database has nothing to lose, and one in memory has no file to copy
    if pending and applied and path:
        upgrade.backup = backup_database(conn, Path(pending[0]).stem)
    for name in pending:
        conn.executescript((_MIGRATIONS_DIR / name).read_text())
        conn.execute("INSERT INTO _migrations(name) VALUES (?)", (name,))
        conn.commit()
        upgrade.applied.append(name)
    return upgrade
