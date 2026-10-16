from typing import List

from rich.console import Console
from rich.table import Table

from greek.models.text import TextMeta

console = Console()


def display_library(texts: List[TextMeta]) -> None:
    if not texts:
        console.print("[dim]No texts imported yet.[/dim]")
        return
    table = Table(title="Library", show_lines=False)
    table.add_column("ID", style="dim", justify="right")
    table.add_column("Title", style="bold")
    table.add_column("Author", style="cyan")
    table.add_column("Lines", justify="right")
    table.add_column("Imported", style="dim")
    for t in texts:
        table.add_row(
            str(t.id), t.title, t.author or "—", str(t.line_count), t.imported_at
        )
    console.print(table)
