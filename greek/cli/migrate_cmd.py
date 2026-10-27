import click

from greek.version import read_versions


@click.command("migrate")
def migrate_cmd():
    """Apply the database's pending migrations, backing it up first.

    \b
    Every command does this as it starts; this one does nothing else, for a
    database that is served read-only elsewhere (the public site's published
    copy: deploy/README.md) or an export made by older code.
    """
    v = read_versions()
    click.echo(f"The database is at {v.database}, the code's last migration.")
