from pathlib import Path

import click

from greek.api.study import MODEL
from greek.db.connection import get_connection
from greek.db.export import ExportError, export_database
from greek.version import code_version


@click.command("export-data")
@click.argument("out_dir", type=click.Path(path_type=Path))
def export_data_cmd(out_dir):
    """Write the database's public export to OUT_DIR, a new or empty folder.

    \b
    A checked copy, as greek backup makes it, without the study batches'
    bookkeeping (each request, its usage and its cost): the study data they
    saved stays. Vacuumed, checked again and compressed:
      greek.db.xz      the database (xz -d greek.db.xz gives greek.db)
      SHA256SUMS       its checksum (sha256sum -c SHA256SUMS)
      manifest.json    its last migration, the code's commit, its texts and
                       the rows in each table
      DATA-LICENSE.md  each part of the data's licence
    The web app keeps running meanwhile. On a server in Docker, write it into
    the data volume, e.g.:
      docker exec CONTAINER python greek.py export-data /data/export
    """
    conn = get_connection()
    try:
        export = export_database(
            conn,
            out_dir,
            code_version()["commit"],
            MODEL,
            progress=lambda message: click.echo(message, err=True),
        )
    except ExportError as e:
        raise click.ClickException(str(e))
    finally:
        conn.close()
    manifest = export.manifest
    database, asset = manifest["database"], manifest["file"]
    click.echo(
        f"Exported the database at {manifest['migration']} "
        f"(code {manifest['commit'] or 'of an unknown commit'}) to {out_dir}:"
    )
    click.echo(
        f"  {asset['file']}  {_mb(asset['bytes'])} "
        f"({database['file']} {_mb(database['bytes'])})"
    )
    for name in export.files[1:]:
        click.echo(f"  {name}")
    click.echo(
        "Emptied "
        + ", ".join(
            f"{table} ({rows:,} rows)" for table, rows in export.emptied.items()
        )
        + "."
    )


def _mb(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"
