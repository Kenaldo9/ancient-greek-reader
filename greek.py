#!/usr/bin/env python3
from dotenv import load_dotenv

load_dotenv()

import click

from greek.db.connection import DatabaseUnusable, get_connection, run_migrations
from greek.cli.backup_cmd import backup_cmd
from greek.cli.export_data_cmd import export_data_cmd
from greek.cli.import_cmd import import_cmd
from greek.cli.library_cmd import library_cmd
from greek.cli.migrate_cmd import migrate_cmd
from greek.cli.read_cmd import read_cmd
from greek.cli.search_cmd import search_cmd
from greek.cli.fetch_cmd import fetch_cmd
from greek.cli.delete_cmd import delete_cmd
from greek.cli.shell_cmd import shell_cmd
from greek.cli.toc_cmd import toc_cmd
from greek.cli.web_cmd import web_cmd
from greek.cli.study_cmd import study_cmd
from greek.cli.update_text_cmd import update_text_cmd
from greek.version import version_text


def show_version(ctx, param, value):
    if not value or ctx.resilient_parsing:
        return
    click.echo(version_text())
    ctx.exit()


@click.group()
@click.option(
    "--version",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=show_version,
    help="Show the code's and the database's versions and exit.",
)
@click.pass_context
def cli(ctx):
    """Ancient Greek Reader — import, read, and study Ancient Greek texts."""
    # `greek web` migrates as the app loads (greek/web/app.py), which shows
    # why on every page when the database is newer than the code
    if ctx.invoked_subcommand not in (None, "web"):
        conn = get_connection()
        try:
            upgrade = run_migrations(conn)
        except DatabaseUnusable as e:
            raise click.ClickException(str(e))
        finally:
            conn.close()
        if upgrade.backup:
            click.echo(
                f"Backed up the database to {upgrade.backup}, then applied "
                f"{', '.join(upgrade.applied)}.",
                err=True,
            )


cli.add_command(import_cmd)
cli.add_command(library_cmd)
cli.add_command(read_cmd)
cli.add_command(search_cmd)
cli.add_command(fetch_cmd)
cli.add_command(delete_cmd)
cli.add_command(shell_cmd)
cli.add_command(toc_cmd)
cli.add_command(web_cmd)
cli.add_command(study_cmd)
cli.add_command(update_text_cmd)
cli.add_command(backup_cmd)
cli.add_command(export_data_cmd)
cli.add_command(migrate_cmd)

if __name__ == "__main__":
    cli()
