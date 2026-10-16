from pathlib import Path

import click
from rich.console import Console

from greek.db.connection import get_connection
from greek.db.repos.texts import get_text

console = Console()


@click.command("delete")
@click.argument("text_id", type=int)
@click.confirmation_option(prompt="Delete this text and all its lines and study data?")
def delete_cmd(text_id):
    """Delete an imported text by ID."""
    conn = get_connection()
    text = get_text(conn, text_id)
    if text is None:
        console.print(f"[red]No text with ID {text_id}.[/red]")
        raise SystemExit(1)

    console.print(
        f"Deleting [bold]{text.title}[/bold] (ID {text_id}, {text.line_count} lines)..."
    )

    filepath = Path(text.filepath)
    if filepath.exists():
        filepath.unlink()

    conn.execute("DELETE FROM texts WHERE id=?", (text_id,))
    conn.commit()

    console.print("[green]Deleted.[/green]")
