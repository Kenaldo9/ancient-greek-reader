from typing import Dict, List, Optional

from rich.console import Console
from rich.text import Text

from greek.models.text import Line

console = Console()


def _line_label(line: Line) -> str:
    return line.reference if line.reference else str(line.line_number)


def _label_width(lines: List[Line]) -> int:
    return max((len(_line_label(line)) for line in lines), default=4)


def _wrap_words(words: list[str], available_width: int) -> list[str]:
    """Wrap a list of words into lines that fit available_width. Returns list of lines."""
    lines = []
    cur: list[str] = []
    cur_len = 0
    for word in words:
        addition = len(word) + (1 if cur else 0)
        if cur and cur_len + addition > available_width:
            lines.append(" ".join(cur))
            cur, cur_len = [word], len(word)
        else:
            cur.append(word)
            cur_len += addition
    if cur:
        lines.append(" ".join(cur))
    return lines or [""]


def _line_glosses(line: Line, study: Dict[int, List[str]]) -> List[str]:
    """One gloss per word of the line from the study data, "·" when unknown."""
    words = line.content.split()
    found = study.get(line.line_number)
    if found and len(found) == len(words):
        return [g or "·" for g in found]
    return ["·"] * len(words)


def _interlinear_text(
    content: str, glosses: List[str], available_width: int, indent: str
) -> Text:
    """
    Word-wrap the line into visual chunks that fit available_width,
    emitting a Greek row then a translation row for each chunk.
    Translation rows are also wrapped and indented if they overflow.
    """
    pairs = list(zip(content.split(), glosses))

    # Split into chunks that fit available_width (based on Greek word lengths)
    chunks: list[tuple[list[str], list[str]]] = []
    cur_greek: list[str] = []
    cur_trans: list[str] = []
    cur_len = 0
    for greek, trans in pairs:
        addition = len(greek) + (1 if cur_greek else 0)
        if cur_greek and cur_len + addition > available_width:
            chunks.append((cur_greek, cur_trans))
            cur_greek, cur_trans, cur_len = [greek], [trans], len(greek)
        else:
            cur_greek.append(greek)
            cur_trans.append(trans)
            cur_len += addition
    if cur_greek:
        chunks.append((cur_greek, cur_trans))

    result = Text()
    for i, (greek_words, trans_words) in enumerate(chunks):
        prefix = "" if i == 0 else indent
        is_last = i == len(chunks) - 1
        result.append(prefix + " ".join(greek_words) + "\n")
        # Wrap the translation too in case it's longer than available_width
        trans_lines = _wrap_words(trans_words, available_width)
        trans_str = ("\n" + indent).join(trans_lines)
        result.append(indent + trans_str + ("" if is_last else "\n"), style="orange1")
    return result


def _print_line(
    line: Line,
    width: int,
    study: Optional[Dict[int, List[str]]] = None,
) -> None:
    label = Text(f"{_line_label(line):>{width}} ", style="dim")
    if study is not None:
        indent = " " * (width + 1)
        available = console.width - width - 1
        glosses = _line_glosses(line, study)
        console.print(
            label + _interlinear_text(line.content, glosses, available, indent)
        )
    else:
        console.print(label + Text(line.content))


def display_lines(
    lines: List[Line],
    title: str = "",
    study: Optional[Dict[int, List[str]]] = None,
) -> None:
    """
    Print lines; with study (line number → a gloss per word), each line gets
    an interlinear gloss row.
    """
    if title:
        console.print(f"\n[bold cyan]{title}[/bold cyan]\n")
    width = _label_width(lines)
    for line in lines:
        _print_line(line, width, study)
    console.print()


def paginate_lines(
    lines: List[Line],
    page_size: int = 40,
    title: str = "",
    study: Optional[Dict[int, List[str]]] = None,
) -> None:
    if title:
        console.print(f"\n[bold cyan]{title}[/bold cyan]\n")
    width = _label_width(lines)
    for i in range(0, len(lines), page_size):
        page = lines[i : i + page_size]
        for line in page:
            _print_line(line, width, study)
        if i + page_size < len(lines):
            try:
                input("\n[Press Enter for next page, Ctrl-C to quit] ")
            except (EOFError, KeyboardInterrupt):
                console.print()
                break
    console.print()
