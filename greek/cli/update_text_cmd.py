import os
import urllib.error
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Tuple

import click
from rich.console import Console
from rich.table import Table

from greek.cli.fetch_cmd import _describe_network_error, _txt_filename
from greek.db.connection import get_connection
from greek.db.repos.lines import (
    get_lines,
    lines_with_saved_data,
    update_line_contents,
)
from greek.db.repos.parses import get_tb_source, has_word_parses
from greek.db.repos.texts import get_text
from greek.fetch.catalog import fetch_url, load_catalog
from greek.fetch.parser import extract_entries

console = Console()


def _fail(message: str):
    console.print(f"[red]{message}[/red]")
    raise SystemExit(1)


def _clip(words: List[str], most: int = 8) -> str:
    if len(words) <= most:
        return " ".join(words)
    return f"{' '.join(words[:3])} … {' '.join(words[-2:])} ({len(words)} words)"


def _word_changes(old: str, new: str) -> Tuple[str, str]:
    """The runs of words removed from `old` and added in `new`, joined by " · "."""
    a, b = old.split(), new.split()
    removed, added = [], []
    matcher = SequenceMatcher(a=a, b=b, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        if i2 > i1:
            removed.append(_clip(a[i1:i2]))
        if j2 > j1:
            added.append(_clip(b[j1:j2]))
    return " · ".join(removed), " · ".join(added)


@click.command("update-text")
@click.argument("text_id", type=int)
@click.option(
    "--file",
    "file_",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read a local TEI file instead of downloading the text's Perseus file",
)
@click.option("--yes", "-y", is_flag=True, help="Update without asking")
def update_text_cmd(text_id, file_, yes):
    """Update an imported text's lines in place from its TEI file.

    \b
    Downloads the text's current Perseus file (or reads --file), parses it
    and replaces the content of every line whose text differs: Perseus's
    corrections since the import, and editorial matter the parser now
    leaves out (footnote markers, citations of quoted passages). Line
    numbers and references never change, so readers' saved places stay
    right.

    \b
    Nothing is changed when the file's references differ from the saved
    text's, or when a changed line has saved data tied to its words:
    study glosses, notes and translations, use labels or a model's parses.
    A treebank's parses are replaced by running 'greek study
    import-treebank' again afterwards.

    \b
    Example:
      greek update-text 16
    """
    conn = get_connection()
    text = get_text(conn, text_id)
    if text is None:
        _fail(f"No text with ID {text_id}.")
    data_dir = Path(os.environ.get("GREEK_DATA_DIR", "./data"))

    if file_ is not None:
        source = file_.name
        xml_bytes = file_.read_bytes()
    else:
        entry = next(
            (e for e in load_catalog(data_dir) if _txt_filename(e) == text.filename),
            None,
        )
        if entry is None:
            _fail(
                f"'{text.title}' wasn't fetched from Perseus; pass its TEI file "
                "with --file."
            )
        source = entry.raw_url
        console.print(f"[dim]Downloading {source}...[/dim]")
        try:
            xml_bytes = fetch_url(source, timeout=60)
        except urllib.error.URLError as e:
            _fail(f"Error: {_describe_network_error(e, source)}")
    try:
        pairs = extract_entries(xml_bytes)
    except ET.ParseError as e:
        _fail(f"Error: could not parse {source}: {e}")

    lines = get_lines(conn, text_id)
    references = [line.reference for line in lines]
    if references != [ref for ref, _ in pairs]:
        first = next(
            (
                i
                for i, (saved, new) in enumerate(zip(references, pairs))
                if saved != new[0]
            ),
            min(len(references), len(pairs)),
        )
        _fail(
            f"The file has {len(pairs):,} lines and '{text.title}' {len(lines):,}, "
            f"with references differing from line {first + 1}: updating would "
            "move line numbers. Nothing was changed."
        )

    changes = {
        line.line_number: content
        for line, (_, content) in zip(lines, pairs)
        if line.content != content
    }
    if not changes:
        console.print(f"'{text.title}' (ID {text_id}) is up to date with {source}.")
        return

    table = Table(title=f"Changed lines: {text.title} (ID {text_id})")
    table.add_column("Line", style="bold")
    table.add_column("Removed", style="red")
    table.add_column("Added", style="green")
    ref_of = {line.line_number: line.reference for line in lines}
    old_of = {line.line_number: line.content for line in lines}
    for number, content in changes.items():
        table.add_row(ref_of[number], *_word_changes(old_of[number], content))
    console.print(table)

    saved = lines_with_saved_data(conn, text_id, changes)
    if saved:
        for kind, numbers in saved.items():
            refs = ", ".join(ref_of[n] for n in numbers[:10])
            more = f" and {len(numbers) - 10:,} more" if len(numbers) > 10 else ""
            console.print(f"  {kind}: {refs}{more}")
        _fail(
            "Saved data is tied to the words of these changed lines; nothing was "
            "changed."
        )

    if not yes and not click.confirm(f"Update {len(changes):,} lines?"):
        console.print("Nothing was changed.")
        return
    update_line_contents(conn, text_id, changes)

    # The text's file copy, as `greek fetch` writes it
    copy = data_dir / "texts" / text.filename
    if copy.exists():
        new_of = {**old_of, **changes}
        copy.write_text(
            "\n".join(f"{ref_of[n]}\t{new_of[n]}" for n in sorted(new_of)),
            encoding="utf-8",
        )
    console.print(
        f"[green]Updated[/green] {len(changes):,} lines of '{text.title}'; line "
        "numbers and references are unchanged."
    )
    if any(has_word_parses(conn, text_id, n, n) for n in changes):
        tb = get_tb_source(conn, text_id)
        work = f" --work {tb.work}" if tb else ""
        console.print(
            "[yellow]The treebank's words on these lines are out of date: run "
            f"'greek study import-treebank {text_id}{work}' again.[/yellow]"
        )
