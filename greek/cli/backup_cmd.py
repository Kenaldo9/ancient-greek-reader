import shutil
import sys
import tempfile
from pathlib import Path

import click

from greek.db.connection import copy_database, get_connection


@click.command("backup")
@click.argument("target")
def backup_cmd(target):
    """Copy the database to TARGET, a new file, or with - to standard output.

    \b
    The copy is made with SQLite's backup API while the web app keeps
    running, and checked with integrity_check; a copy that fails is removed
    and nothing is written. To fetch a copy from a server where the app runs
    in Docker:
      ssh HOST docker exec CONTAINER python greek.py backup - > greek.db
    """
    conn = get_connection()
    try:
        if target != "-":
            path = Path(target)
            if path.exists():
                raise click.ClickException(f"{path} already exists.")
            copy_database(conn, path)
            click.echo(f"Copied the database to {path}.", err=True)
            return
        folder = Path(tempfile.mkdtemp(prefix="greek-backup-"))
        try:
            copy = folder / "greek.db"
            copy_database(conn, copy)
            with copy.open("rb") as f:
                shutil.copyfileobj(f, sys.stdout.buffer)
            sys.stdout.buffer.flush()
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    finally:
        conn.close()
