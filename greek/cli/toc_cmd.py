from collections import OrderedDict

import click
from rich.console import Console
from rich.table import Table

from greek.db.connection import get_connection
from greek.db.repos.texts import get_text

console = Console()


@click.command("toc")
@click.argument("text_id", type=int)
def toc_cmd(text_id):
    """Show the structure (table of contents) of an imported text."""
    conn = get_connection()
    text = get_text(conn, text_id)
    if text is None:
        console.print(f"[red]No text with ID {text_id}.[/red]")
        raise SystemExit(1)

    rows = conn.execute(
        "SELECT reference, line_number FROM lines WHERE text_id=? AND reference IS NOT NULL ORDER BY line_number",
        (text_id,),
    ).fetchall()

    if not rows:
        console.print(
            f"[yellow]No references found for '{text.title}' — "
            f"imported without reference data. Use integer ranges instead (e.g. 1-10).[/yellow]"
        )
        return

    books = OrderedDict()
    for row in rows:
        ref = row["reference"]
        book = ref.split(".")[0] if "." in ref else ref
        if book not in books:
            books[book] = {
                "first_line": row["line_number"],
                "first_ref": ref,
                "last_ref": ref,
                "last_line": row["line_number"],
                "count": 0,
            }
        books[book]["last_ref"] = ref
        books[book]["last_line"] = row["line_number"]
        books[book]["count"] += 1

    table = Table(title=f"{text.author} — {text.title} (ID {text_id})")
    table.add_column("Book", style="cyan")
    table.add_column("Sections", justify="right")
    table.add_column("First ref", style="dim")
    table.add_column("Last ref", style="dim")
    table.add_column("Lines", justify="right", style="dim")

    for book, info in books.items():
        table.add_row(
            book,
            str(info["count"]),
            info["first_ref"],
            info["last_ref"],
            f"{info['first_line']}–{info['last_line']}",
        )

    console.print(table)
