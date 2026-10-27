"""
The database's public export (`greek export-data`): a copy without the study
batches' bookkeeping, vacuumed, checked and compressed with xz, beside its
SHA-256, a manifest and the data's licences. The public site runs on it
(deploy/publish.sh); a GitHub Release would hold these files if the database
is published (docs/data.md).

xz (the standard library's lzma) makes the smallest file of those measured on
the live database (2026-10-16: 195 MB → xz -6 33 MB, zstd -19 42 MB, gzip -9
59 MB), so the export needs no new dependency.
"""

import hashlib
import json
import lzma
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from greek.db.connection import applied_migrations, copy_database
from greek.db.repos.parses import get_tb_source
from greek.db.repos.study import book_study
from greek.db.repos.texts import list_texts

# What each study batch asked for, its usage and its cost: the spending, not
# data. Requests first, though deleting a batch would cascade to them. The
# study data the batches saved stays.
EMPTIED = ("study_requests", "lemma_requests", "parts_requests", "study_batches")

DATABASE = "greek.db"
ASSET = "greek.db.xz"
CHECKSUMS = "SHA256SUMS"
MANIFEST = "manifest.json"
LICENCE_NAME = "DATA-LICENSE.md"
# The repository's root, or /app in the Docker image (see the Dockerfile)
LICENCE = Path(__file__).resolve().parents[2] / LICENCE_NAME
XZ_PRESET = 6  # xz's default: -9 was 2% smaller for 7 times the memory

_CHUNK = 1 << 20


class ExportError(RuntimeError):
    """The export can't be written where it was asked for, or failed its checks."""


@dataclass
class Export:
    """What export_database() wrote: the files in folder, and the manifest's facts."""

    folder: Path
    files: List[str]
    manifest: dict

    @property
    def emptied(self) -> Dict[str, int]:
        return self.manifest["emptied"]


def export_database(
    conn: sqlite3.Connection,
    folder: Path,
    commit: Optional[str],
    model: str,
    progress: Callable[[str], None] = lambda message: None,
) -> Export:
    """
    Write the export of conn's database to folder, a new or empty folder:
    greek.db.xz, SHA256SUMS, manifest.json and DATA-LICENSE.md. commit is the
    code's, and model the study data's, for the manifest. Nothing is
    overwritten; an export that fails removes what it wrote.
    """
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ExportError(f"{folder} is not a new or empty folder.")
    if not LICENCE.is_file():
        raise ExportError(f"{LICENCE} is missing: the export ships with it.")
    created = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    database = folder / DATABASE
    written = [database] + [
        database.with_name(f"{DATABASE}-{end}") for end in ("journal", "wal", "shm")
    ]
    try:
        progress("Copying the database…")
        copy_database(conn, database)
        progress(f"Emptying {', '.join(EMPTIED)} and vacuuming…")
        manifest = _clean(database, commit, model)
        database_sha = _sha256(database)
        manifest["database"] = {
            "file": DATABASE,
            "bytes": database.stat().st_size,
            "sha256": database_sha,
        }
        progress(f"Compressing with xz -{XZ_PRESET} (minutes on a Raspberry Pi)…")
        asset = folder / ASSET
        written.append(asset)
        with (
            database.open("rb") as src,
            lzma.open(asset, "wb", preset=XZ_PRESET) as dst,
        ):
            shutil.copyfileobj(src, dst, _CHUNK)
        progress("Checking that it decompresses to the same database…")
        if _sha256_of_xz(asset) != database_sha:
            raise ExportError(f"{asset} does not decompress to {database}.")
        database.unlink()
        asset_sha = _sha256(asset)
        manifest["file"] = {
            "file": ASSET,
            "bytes": asset.stat().st_size,
            "sha256": asset_sha,
        }

        files = [ASSET, CHECKSUMS, MANIFEST, LICENCE_NAME]
        written += [folder / name for name in files[1:]]
        (folder / CHECKSUMS).write_text(f"{asset_sha}  {ASSET}\n")
        (folder / MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        shutil.copyfile(LICENCE, folder / LICENCE_NAME)
        return Export(folder, files, manifest)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        if created:
            shutil.rmtree(folder, ignore_errors=True)
        raise


def _clean(database: Path, commit: Optional[str], model: str) -> dict:
    """
    Empty the batch bookkeeping in the copy, make it one self-contained file
    (no WAL), vacuum it and check it. Returns the manifest's facts about it.
    """
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        # The copy has the live database's WAL mode; a published file shouldn't
        db.execute("PRAGMA journal_mode = DELETE")
        emptied = {
            table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in EMPTIED
        }
        with db:
            for table in EMPTIED:
                db.execute(f"DELETE FROM {table}")
        db.execute("VACUUM")
        check = db.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise ExportError(f"The export failed its integrity check: {check}")
        broken = db.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise ExportError(
                f"The export has {len(broken)} rows whose foreign keys point "
                f"nowhere, the first in {broken[0][0]}."
            )
        migrations = applied_migrations(db)
        return {
            "exported": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "migration": Path(migrations[-1]).stem if migrations else None,
            "commit": commit,
            "texts": [_text(db, text, model) for text in list_texts(db)],
            "tables": _row_counts(db),
            "emptied": emptied,
        }
    finally:
        db.close()


def _text(db: sqlite3.Connection, text, model: str) -> dict:
    """A text in the manifest: its treebank and licence, and its books with study data."""
    source = get_tb_source(db, text.id)
    books = book_study(db, text.id, model)
    return {
        "id": text.id,
        "title": text.title,
        "author": text.author,
        "lines": text.line_count,
        "treebank": source.work if source else None,
        "treebank_licence": source.licence if source else None,
        "study_model": model,
        "books_translated": [b.number for b in books if b.state == "translated"],
        "books_in_part": [b.number for b in books if b.state == "partly"],
    }


def _row_counts(db: sqlite3.Connection) -> Dict[str, int]:
    """Rows in each of the database's own tables (not the search index's)."""
    tables = [
        row["name"]
        for row in db.execute("PRAGMA table_list")
        if row["schema"] == "main"
        and row["type"] == "table"
        and not row["name"].startswith("sqlite_")
    ]
    return {
        table: db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        for table in sorted(tables)
    }


def _sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _sha256_of_xz(path: Path) -> str:
    with lzma.open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()
