"""
Which code is running and which database it uses.

The code's version is the git commit it was built from: the Docker image
records it when it is built (build.json, see the Dockerfile), and a checkout
reads it from .git. Uncommitted changes aren't shown. The database's version
is the last migration it has had (greek/db/migrations/, recorded in
_migrations), e.g. 019_dialects.
"""

import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import greek.db.connection as connection

ROOT = Path(__file__).resolve().parent.parent
BUILD_FILE = Path(__file__).resolve().parent / "build.json"


def git_commit(root: Path) -> Optional[str]:
    """The short commit hash a checkout is at, read from .git without git; None if unknown."""
    git = root / ".git"
    try:
        if git.is_file():  # a worktree's .git: "gitdir: <path>"
            git = root / git.read_text().split(":", 1)[1].strip()
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head[:7] or None  # detached
        ref = head[5:]
        common = git
        if (git / "commondir").is_file():
            common = git / (git / "commondir").read_text().strip()
        for base in (git, common):
            if (base / ref).is_file():
                return (base / ref).read_text().strip()[:7] or None
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                sha, _, name = line.partition(" ")
                if name == ref:
                    return sha[:7]
    except (OSError, IndexError):
        pass
    return None


def write_build_file(root: Path, out: Path = BUILD_FILE) -> dict:
    """Record the commit the code at root is from, and the time, for the Docker image."""
    build = {
        "commit": git_commit(root),
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
    out.write_text(json.dumps(build) + "\n")
    return build


def code_version() -> dict:
    """{"commit": "13b8682" or None, "built": SQLite-style UTC time or None (a checkout)}."""
    try:
        build = json.loads(BUILD_FILE.read_text())
        return {"commit": build.get("commit"), "built": build.get("built")}
    except (OSError, ValueError, AttributeError):
        return {"commit": git_commit(ROOT), "built": None}


@dataclass
class Versions:
    commit: Optional[str]  # None: unknown
    built: Optional[str]  # the Docker image's build time (UTC); None for a checkout
    database: Optional[str]  # its last migration, e.g. "019_dialects"; None: none yet
    known: str  # this code's last migration
    pending: List[str]  # migrations the next start applies
    unknown: List[str]  # migrations this code doesn't have: the database is newer

    @property
    def database_number(self) -> Optional[str]:
        return self.database.split("_", 1)[0] if self.database else None


def read_versions(db_path: Optional[Path] = None) -> Versions:
    """The code's and the database's versions. Reads the database only: no migrations."""
    db_path = db_path or connection._DB_PATH
    applied: List[str] = []
    if db_path.is_file():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            applied = connection.applied_migrations(conn)
        finally:
            conn.close()
    known = connection.known_migrations()
    code = code_version()
    return Versions(
        commit=code["commit"],
        built=code["built"],
        database=Path(applied[-1]).stem if applied else None,
        known=Path(known[-1]).stem,
        pending=[m for m in known if m not in applied],
        unknown=sorted(set(applied) - set(known)),
    )


def version_text(db_path: Optional[Path] = None) -> str:
    """`greek --version`: the code and the database, one line each."""
    db_path = db_path or connection._DB_PATH
    v = read_versions(db_path)
    code = v.commit or "unknown commit"
    if v.built:
        built = datetime.strptime(v.built, "%Y-%m-%d %H:%M:%S")
        local = built.replace(tzinfo=timezone.utc).astimezone()
        code += f" (built {local:%Y-%m-%d %H:%M})"
    if v.unknown:
        db = (
            f"{v.database} ({db_path}), newer than this code: it has "
            f"{', '.join(v.unknown)}"
        )
    elif v.database is None:
        db = f"none yet ({db_path})"
    else:
        db = f"{v.database} ({db_path})"
        if v.pending:
            db += f"; the next start applies {', '.join(v.pending)}"
    return f"code      {code}\ndatabase  {db}"


if __name__ == "__main__":
    # The Docker build: python -m greek.version build <checkout>
    if sys.argv[1:2] == ["build"] and len(sys.argv) == 3:
        print(write_build_file(Path(sys.argv[2])))
    else:
        sys.exit("usage: python -m greek.version build <checkout>")
