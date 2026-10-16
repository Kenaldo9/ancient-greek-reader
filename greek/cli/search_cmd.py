import click
from rich.console import Console
from rich.text import Text

from greek.db.connection import get_connection
from greek.db.repos.lines import search_lines
from greek.db.repos.texts import get_text

console = Console()


@click.command("search")
@click.argument("text_id", type=int)
@click.argument("query")
@click.option(
    "--context",
    "-c",
    default=2,
    show_default=True,
    help="Lines of context around match",
)
@click.option(
    "--limit", "-n", default=20, show_default=True, help="Max number of matches"
)
def search_cmd(text_id, query, context, limit):
    """Search for a word or phrase in a text. Accents and capitals are ignored.

    \b
    Examples:
      greek search 12 μηνιν          (finds μῆνιν, Μῆνιν, …)
      greek search 12 "μῆνιν ἄειδε"  (a phrase)
      greek search 12 "μην*"         (words starting with μην)
    """
    conn = get_connection()
    text = get_text(conn, text_id)
    if text is None:
        console.print(f"[red]Text #{text_id} not found.[/red]")
        raise SystemExit(1)

    rows, total = search_lines(conn, text_id, query, limit)
    if not rows:
        console.print(
            f"[yellow]No results for '{query}' in '{text.title}' "
            "(accents and capitals are ignored).[/yellow]"
        )
        return

    shown = f", showing the first {len(rows)}" if total > len(rows) else ""
    console.print(
        f"\n[bold]{total} {'match' if total == 1 else 'matches'} for '[cyan]{query}[/cyan]'"
        f" in '{text.title}'[/bold]"
        f" [dim](accents and capitals ignored{shown})[/dim]\n"
    )
    needed = set()
    for row in rows:
        ln = row.line_number
        for n in range(max(1, ln - context), ln + context + 1):
            needed.add(n)

    placeholders = ",".join("?" * len(needed))
    ctx_all = {
        r["line_number"]: r
        for r in conn.execute(
            f"SELECT line_number, content, reference FROM lines WHERE text_id=? AND line_number IN ({placeholders}) ORDER BY line_number",
            (text_id, *sorted(needed)),
        ).fetchall()
    }
    width = max(len(r["reference"] or str(r["line_number"])) for r in ctx_all.values())

    for row in rows:
        ln = row.line_number
        for n in range(max(1, ln - context), ln + context + 1):
            if n not in ctx_all:
                continue
            cr = ctx_all[n]
            hit = cr["line_number"] == ln
            label = cr["reference"] or str(cr["line_number"])
            num = Text(f"{label:>{width}} ", style="bold yellow" if hit else "dim")
            content = Text(cr["content"], style="bold" if hit else "dim")
            console.print(num + content)
        console.print()
