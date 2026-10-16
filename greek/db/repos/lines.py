import sqlite3
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from greek.models.text import Book, Line
from greek.search import fts_query, plain

_LINE_COLUMNS = "l.id, l.text_id, l.line_number, l.content, l.reference"


def insert_lines(
    conn: sqlite3.Connection,
    text_id: int,
    lines: List[str],
    references: Optional[List[Optional[str]]] = None,
) -> None:
    if references is None:
        references = [None] * len(lines)
    conn.executemany(
        "INSERT INTO lines(text_id, line_number, content, reference, plain) VALUES (?,?,?,?,?)",
        [
            (text_id, i + 1, line, ref, plain(line))
            for i, (line, ref) in enumerate(zip(lines, references))
        ],
    )
    conn.commit()


def update_line_contents(
    conn: sqlite3.Connection, text_id: int, contents: Mapping[int, str]
) -> None:
    """
    Replace the content of some of a text's lines, by line number, in one
    transaction. Line numbers and references stay; the lines_au trigger
    updates the search index.
    """
    with conn:
        conn.executemany(
            "UPDATE lines SET content=?, plain=? WHERE text_id=? AND line_number=?",
            [(c, plain(c), text_id, n) for n, c in contents.items()],
        )


# Saved data that depends on a line's words (their positions or exact text):
# per line, or over a range of lines. A treebank's own parses are left out
# (an import replaces them).
_SAVED_PER_LINE = {
    "study glosses and notes": "SELECT DISTINCT line_number FROM word_study WHERE text_id=?",
    "literal translations": "SELECT DISTINCT line_number FROM line_translations WHERE text_id=?",
    "use labels": "SELECT DISTINCT line_number FROM word_uses WHERE text_id=?",
    "parses a model supplied": """SELECT DISTINCT line_number FROM word_parses
                                  WHERE text_id=? AND source <> 'treebank'""",
}
_SAVED_RANGES = {
    "fluent translations": """SELECT first_line, last_line FROM translation_units
                              WHERE text_id=?""",
}


def lines_with_saved_data(
    conn: sqlite3.Connection, text_id: int, line_numbers: Iterable[int]
) -> Dict[str, List[int]]:
    """
    Which of the given lines have saved data tied to their words, by kind
    ("use labels": [12, 13]); kinds without any are left out.
    """
    wanted = set(line_numbers)
    found: Dict[str, List[int]] = {}
    for kind, sql in _SAVED_PER_LINE.items():
        hits = wanted & {row[0] for row in conn.execute(sql, (text_id,))}
        if hits:
            found[kind] = sorted(hits)
    for kind, sql in _SAVED_RANGES.items():
        hits = set()
        for start, end in conn.execute(sql, (text_id,)):
            if start is not None and end is not None:
                hits |= {n for n in wanted if start <= n <= end}
        if hits:
            found[kind] = sorted(hits)
    return found


def get_lines(
    conn: sqlite3.Connection,
    text_id: int,
    start: Optional[int] = None,
    end: Optional[int] = None,
) -> List[Line]:
    if start is not None and end is not None:
        rows = conn.execute(
            f"SELECT {_LINE_COLUMNS} FROM lines l WHERE text_id=? AND line_number BETWEEN ? AND ? ORDER BY line_number",
            (text_id, start, end),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_LINE_COLUMNS} FROM lines l WHERE text_id=? ORDER BY line_number",
            (text_id,),
        ).fetchall()
    return [Line(**dict(r)) for r in rows]


def search_lines(
    conn: sqlite3.Connection, text_id: int, query: str, limit: int = 200
) -> Tuple[List[Line], int]:
    """
    Lines of a text matching a search (see greek.search: accents and case are
    ignored, every word must match), in text order and at most `limit` of
    them, plus how many lines match in all.
    """
    match = fts_query(query)
    if match is None:
        return [], 0
    # CROSS JOIN keeps the full-text hits as the outer loop. Otherwise SQLite
    # walks the text's lines and runs the MATCH again for each: about 6 s to
    # count δέ in the Iliad instead of 4 ms.
    hits = """FROM lines_fts CROSS JOIN lines l ON l.id = lines_fts.rowid
              WHERE lines_fts MATCH ? AND l.text_id = ?"""
    rows = conn.execute(
        f"SELECT {_LINE_COLUMNS} {hits} ORDER BY l.line_number LIMIT ?",
        (match, text_id, limit),
    ).fetchall()
    total = conn.execute(f"SELECT count(*) {hits}", (match, text_id)).fetchone()[0]
    return [Line(**dict(r)) for r in rows], total


def resolve_line_range(
    conn: sqlite3.Connection,
    text_id: int,
    lines_arg: str,
) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """
    Parse a --lines argument and return (start_lineno, end_lineno, error_message).
    Accepts:
      - Integer range:   "1-10"
      - Reference range: "1.1-1.10" or "1.1-24.804"
    """
    if "." in lines_arg:
        # Reference mode — split on the "-" between the two refs
        # Supported formats:
        #   "1.1-1.10"  → ref_start=1.1,  ref_end=1.10
        #   "1.1-10"    → ref_start=1.1,  ref_end=1.10  (same book shorthand)
        #   "1.100-2.1" → ref_start=1.100, ref_end=2.1
        parts = lines_arg.split("-")
        ref_start = ref_end = None
        for i in range(1, len(parts)):
            candidate_start = "-".join(parts[:i])
            candidate_end = "-".join(parts[i:])
            if "." in candidate_start:
                ref_start = candidate_start
                # If end has no dot, treat it as same book as start
                if "." not in candidate_end:
                    book = candidate_start.rsplit(".", 1)[0]
                    ref_end = f"{book}.{candidate_end}"
                else:
                    ref_end = candidate_end
                break
        if ref_start is None or ref_end is None:
            return (
                None,
                None,
                f"Invalid reference range '{lines_arg}'. Use format like 1.1-1.10 or 1.1-10",
            )

        ref_map = {
            r["reference"]: r["line_number"]
            for r in conn.execute(
                "SELECT reference, line_number FROM lines WHERE text_id=? AND reference IN (?,?)",
                (text_id, ref_start, ref_end),
            ).fetchall()
        }

        if ref_start not in ref_map:
            return None, None, f"Reference '{ref_start}' not found in this text"
        if ref_end not in ref_map:
            return None, None, f"Reference '{ref_end}' not found in this text"

        return ref_map[ref_start], ref_map[ref_end], None
    else:
        try:
            parts = lines_arg.split("-")
            return int(parts[0]), int(parts[1]), None
        except (ValueError, IndexError):
            return (
                None,
                None,
                f"Invalid line range '{lines_arg}'. Use format like 1-10 or 1.1-1.10",
            )


def resolve_line(conn: sqlite3.Connection, text_id: int, at: str) -> Optional[int]:
    """
    Return the line_number for a single position: a reference ("1.42", "1.1.0")
    or, failing that, a plain line number ("42"). None if it doesn't exist.
    """
    at = at.strip()
    row = conn.execute(
        "SELECT line_number FROM lines WHERE text_id=? AND reference=?",
        (text_id, at),
    ).fetchone()
    if row is None and at.isdigit():
        row = conn.execute(
            "SELECT line_number FROM lines WHERE text_id=? AND line_number=?",
            (text_id, int(at)),
        ).fetchone()
    return row["line_number"] if row else None


def line_lengths(
    conn: sqlite3.Connection, text_id: int, start: int, end: int
) -> List[Tuple[int, Optional[str], int]]:
    """(line_number, reference, characters) of each line in [start, end], in order."""
    rows = conn.execute(
        """SELECT line_number, reference, LENGTH(content) AS length FROM lines
           WHERE text_id=? AND line_number BETWEEN ? AND ? ORDER BY line_number""",
        (text_id, start, end),
    ).fetchall()
    return [(r["line_number"], r["reference"], r["length"]) for r in rows]


def book_span(
    conn: sqlite3.Connection, text_id: int, line_number: int
) -> Optional[Tuple[str, int, int]]:
    """
    The book of a line (the part of its reference before the first dot) and
    the book's first and last line numbers; None for a line without a
    reference.
    """
    row = conn.execute(
        "SELECT reference FROM lines WHERE text_id=? AND line_number=?",
        (text_id, line_number),
    ).fetchone()
    if row is None or not row[0]:
        return None
    book = row[0].split(".")[0]
    first, last = conn.execute(
        """SELECT min(line_number), max(line_number) FROM lines
           WHERE text_id=? AND (reference=? OR reference GLOB ? || '.*')""",
        (text_id, book, book),
    ).fetchone()
    return book, first, last


def list_books(conn: sqlite3.Connection, text_id: int) -> List[Book]:
    """
    Group a text's lines into books by the part of the reference before the
    first dot ("1.42" and "1.1.0" both belong to book "1"). Lines without a
    reference are ignored, so a text imported without references has no books.
    """
    rows = conn.execute(
        """WITH b AS (
               SELECT CASE WHEN instr(reference, '.') > 0
                           THEN substr(reference, 1, instr(reference, '.') - 1)
                           ELSE reference END AS number,
                      MIN(line_number) AS line_start,
                      MAX(line_number) AS line_end,
                      COUNT(*) AS count
               FROM lines
               WHERE text_id = ? AND reference IS NOT NULL
               GROUP BY number
           )
           SELECT b.number, ls.reference AS ref_start, le.reference AS ref_end,
                  b.line_start, b.line_end, b.count
           FROM b
           JOIN lines ls ON ls.text_id = ? AND ls.line_number = b.line_start
           JOIN lines le ON le.text_id = ? AND le.line_number = b.line_end
           ORDER BY b.line_start""",
        (text_id, text_id, text_id),
    ).fetchall()
    return [Book(**dict(r)) for r in rows]
