import os
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

import click
from rich.console import Console

from greek.db.connection import get_connection
from greek.db.repos.texts import insert_text
from greek.db.repos.lines import insert_lines

console = Console()


def _parse_lines(raw: str) -> Tuple[List[str], Optional[List[Optional[str]]]]:
    """
    Detect whether the file has 'reference\\tcontent' format.
    Returns (contents, references) where references is None if no refs found.
    """
    raw_lines = raw.splitlines()
    while raw_lines and not raw_lines[-1].strip():
        raw_lines.pop()

    has_refs = all(
        "\t" in line and line.split("\t", 1)[0].replace(".", "").isdigit()
        for line in raw_lines[: min(5, len(raw_lines))]
        if line.strip()
    )

    if has_refs:
        contents = []
        references = []
        for line in raw_lines:
            if "\t" in line:
                ref, content = line.split("\t", 1)
                references.append(ref.strip())
                contents.append(content)
            else:
                references.append(None)
                contents.append(line)
        return contents, references
    else:
        return raw_lines, None


@click.command("import")
@click.argument("file", type=click.Path(exists=True, readable=True))
@click.option("--title", "-t", required=True, help="Title of the text")
@click.option("--author", "-a", default=None, help="Author of the text")
def import_cmd(file, title, author):
    """Import a Greek text file into the library."""
    src = Path(file)
    conn = get_connection()
    data_dir = Path(os.environ.get("GREEK_DATA_DIR", "./data"))
    dest_dir = data_dir / "texts"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name

    if src.resolve() != dest.resolve():
        if dest.exists():
            if not click.confirm(
                f"File '{src.name}' already exists in texts/. Overwrite?"
            ):
                raise click.Abort()
        shutil.copy2(src, dest)

    raw = dest.read_text(encoding="utf-8")
    contents, references = _parse_lines(raw)

    text_id = insert_text(conn, title, author, src.name, str(dest), len(contents))
    insert_lines(conn, text_id, contents, references)

    ref_note = " (with line references)" if references else ""
    console.print(
        f"[green]Imported[/green] '{title}' (ID: {text_id}) — {len(contents)} lines{ref_note}"
    )
