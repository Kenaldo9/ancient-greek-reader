import click
from rich.console import Console

from greek.db.connection import get_connection
from greek.db.repos.texts import get_text
from greek.db.repos.lines import get_lines, resolve_line_range
from greek.db.repos.study import study_line_glosses
from greek.api.models import model_label
from greek.api.study import MODEL as STUDY_MODEL
from greek.display.reader import display_lines, paginate_lines

console = Console()


@click.command("read")
@click.argument("text_id", type=int)
@click.option("--lines", "-l", default=None, help="Line range: 1-20 or 1.1-1.20")
@click.option("--page-size", default=40, show_default=True, help="Lines per page")
@click.option(
    "--interlinear",
    "-i",
    is_flag=True,
    default=False,
    help="Show the study data's gloss beneath each word (lines without study "
    "data show '·')",
)
def read_cmd(text_id, lines, page_size, interlinear):
    """Read a text from the library."""
    conn = get_connection()
    text = get_text(conn, text_id)
    if text is None:
        console.print(f"[red]Text #{text_id} not found.[/red]")
        raise SystemExit(1)

    start = end = None
    if lines:
        start, end, err = resolve_line_range(conn, text_id, lines)
        if err:
            console.print(f"[red]{err}[/red]")
            raise SystemExit(1)

    db_lines = get_lines(conn, text_id, start, end)
    if not db_lines:
        console.print("[yellow]No lines found for the given range.[/yellow]")
        return

    parts = [text.title]
    if text.author:
        parts.append(text.author)
    title = " — ".join(parts)
    if lines:
        title += f" | Lines {lines}"

    study = None
    if interlinear:
        contents = {ln.line_number: ln.content for ln in db_lines}
        study = study_line_glosses(
            conn,
            text_id,
            contents,
            db_lines[0].line_number,
            db_lines[-1].line_number,
            STUDY_MODEL,
        )
        rest = [ln for ln in db_lines if ln.line_number not in study]
        if study:
            counted = f" for {len(study)} of {len(db_lines)} lines" if rest else ""
            console.print(
                f"[dim]Glosses from the {model_label(STUDY_MODEL)} study data"
                f"{counted}.[/dim]"
            )

    range_requested = start is not None
    if range_requested:
        display_lines(db_lines, title=title, study=study)
    else:
        paginate_lines(
            db_lines,
            page_size=page_size,
            title=title,
            study=study,
        )
