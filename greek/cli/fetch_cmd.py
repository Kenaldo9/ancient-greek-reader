import os
import socket
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from greek.db.connection import get_connection
from greek.db.repos.texts import insert_text, list_texts
from greek.db.repos.lines import insert_lines
from greek.fetch.catalog import load_catalog, search_catalog, CatalogEntry, fetch_url
from greek.fetch.parser import extract_entries

console = Console()


def _txt_filename(entry: CatalogEntry) -> str:
    """Generate a clean .txt filename from author and title."""
    name = f"{entry.author}_{entry.title}".lower()
    name = name.replace(" ", "_").replace("'", "").replace(",", "")
    # Keep only alphanumeric, underscore, hyphen
    name = "".join(c for c in name if c.isalnum() or c in "_-")
    return f"{name}.txt"


def _describe_network_error(e: urllib.error.URLError, url: str) -> str:
    """Return a user-friendly description of a network error."""
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code} error fetching {url}: {e.msg}"
    reason = e.reason
    if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
        return f"Network timeout fetching {url}. Check your connection."
    return f"Network error fetching {url}: {reason}"


def _import_entry(
    conn, entry: CatalogEntry, data_dir: Path, force: bool = False
) -> None:
    txt_filename = _txt_filename(entry)
    existing = [t for t in list_texts(conn) if t.filename == txt_filename]
    if existing:
        if Path(existing[0].filepath).exists():
            if not force:
                console.print(
                    f"[yellow]Already imported:[/yellow] '{existing[0].title}' (ID {existing[0].id}) — use --force to re-import"
                )
                return
            console.print(
                f"[dim]Removing existing '{existing[0].title}' (ID {existing[0].id})...[/dim]"
            )
            Path(existing[0].filepath).unlink(missing_ok=True)
        # Stale record or forced removal — clean up DB so we can re-import
        conn.execute("DELETE FROM texts WHERE id=?", (existing[0].id,))
        conn.commit()

    console.print(f"[dim]Downloading {entry}...[/dim]")
    try:
        xml_bytes = fetch_url(entry.raw_url, timeout=60)
    except urllib.error.URLError as e:
        console.print(f"[red]Error: {_describe_network_error(e, entry.raw_url)}[/red]")
        return
    except OSError as e:
        console.print(f"[red]Error: Could not connect to Perseus: {e}[/red]")
        return

    console.print("[dim]Parsing...[/dim]")
    try:
        pairs = extract_entries(xml_bytes)
    except ET.ParseError as e:
        console.print(
            f"[red]Error: Could not parse XML from {entry.raw_url}: {e}[/red]"
        )
        return

    if not pairs:
        console.print(
            "[red]Warning: No lines found for this text. The file structure may not be supported.[/red]"
        )
        return

    # Save as tab-separated reference\tcontent text file
    dest_dir = data_dir / "texts"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / txt_filename
    dest.write_text(
        "\n".join(f"{ref}\t{content}" for ref, content in pairs),
        encoding="utf-8",
    )

    contents = [p[1] for p in pairs]
    references = [p[0] for p in pairs]

    text_id = insert_text(
        conn, entry.title, entry.author, txt_filename, str(dest), len(contents)
    )
    insert_lines(conn, text_id, contents, references)

    console.print(
        f"[green]Imported[/green] '{entry.title}' by {entry.author} "
        f"(ID: {text_id}) — {len(contents)} lines → {dest.name}"
    )


@click.command("fetch")
@click.argument("query", required=False)
@click.option(
    "--list", "show_list", is_flag=True, help="List all available texts in catalog"
)
@click.option("--refresh", is_flag=True, help="Rebuild catalog from Perseus GitHub")
@click.option(
    "--all", "download_all", is_flag=True, help="Download all matching results"
)
@click.option(
    "--force",
    is_flag=True,
    help="Re-import even if already present (replaces lines and file)",
)
def fetch_cmd(query, show_list, refresh, download_all, force):
    """Search and download Greek texts from the Perseus Digital Library.

    \b
    Examples:
      greek fetch Homer
      greek fetch "Peloponnesian War"
      greek fetch Plato
      greek fetch --list
      greek fetch --refresh
    """
    conn = get_connection()
    data_dir = Path(os.environ.get("GREEK_DATA_DIR", "./data"))
    catalog_path = data_dir / "catalog.json"

    if refresh or not catalog_path.exists():
        if not refresh:
            console.print(
                "[dim]No catalog yet. Building from Perseus GitHub "
                "(one-time, takes ~1 minute)...[/dim]"
            )
        try:
            catalog = load_catalog(data_dir, refresh=True, console=console)
        except urllib.error.HTTPError as e:
            click.echo(
                f"Error: HTTP {e.code} fetching catalog from Perseus GitHub: {e.msg}",
                err=True,
            )
            raise SystemExit(1)
        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
                click.echo(
                    "Error: Network timeout fetching catalog from Perseus GitHub. Check your connection.",
                    err=True,
                )
            else:
                click.echo(f"Error: Could not reach Perseus GitHub: {reason}", err=True)
            raise SystemExit(1)
        console.print(f"[green]Catalog ready:[/green] {len(catalog)} texts available")
    else:
        catalog = load_catalog(data_dir)

    if show_list or not query:
        table = Table(title=f"Perseus Greek Texts ({len(catalog)} available)")
        table.add_column("Author", style="cyan")
        table.add_column("Title", style="bold")
        for e in catalog:
            table.add_row(e.author, e.title)
        console.print(table)
        return

    results = search_catalog(catalog, query)
    if not results:
        console.print(f"[yellow]No results for '{query}'.[/yellow]")
        console.print(
            "[dim]Tip: run 'greek fetch --refresh' to update the catalog.[/dim]"
        )
        return

    if len(results) == 1:
        entry = results[0]
        console.print(f"Found: [bold]{entry}[/bold]")
        if download_all or click.confirm("Download and import?"):
            _import_entry(conn, entry, data_dir, force=force)
        return

    # Multiple results
    console.print(f"\nFound {len(results)} matches for '[cyan]{query}[/cyan]':\n")
    for i, e in enumerate(results, 1):
        console.print(f"  [dim]{i:>3}[/dim]  {e.author} — {e.title}")
    console.print()

    if download_all:
        for e in results:
            _import_entry(conn, e, data_dir, force=force)
        return

    choice = click.prompt(
        "Enter number to download (or 'all', 'q' to quit)", default="q"
    )
    if choice.strip().lower() == "q":
        return
    if choice.strip().lower() == "all":
        for e in results:
            _import_entry(conn, e, data_dir, force=force)
        return
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(results):
            _import_entry(conn, results[idx], data_dir, force=force)
        else:
            console.print("[red]Invalid number.[/red]")
    except ValueError:
        console.print("[red]Invalid input.[/red]")
