import click
from rich.console import Console

from greek.db.connection import get_connection
from greek.db.repos.texts import list_texts
from greek.display.tables import display_library

console = Console()


@click.command("library")
@click.option(
    "--sort",
    "-s",
    default="id",
    type=click.Choice(["id", "title", "author", "imported_at"]),
    help="Sort field",
)
@click.option("--author", "-a", default=None, help="Filter by author")
@click.option("--short", is_flag=True, default=False, help="Show only ID and title")
def library_cmd(sort, author, short):
    """List all imported texts."""
    conn = get_connection()
    texts = list_texts(conn, sort=sort, author=author)
    if short:
        for t in texts:
            console.print(
                f"[dim]{t.id:>3}[/dim]  {t.title}"
                + (f"  [dim]— {t.author}[/dim]" if t.author else "")
            )
    else:
        display_library(texts)
