import sqlite3
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from greek.models.study import LemmaEntry, LexiconEntry

_ENTRY_COLUMNS = "e.id, e.source, e.key, e.headword, e.short_def"


def lemma_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Every lemma with a parse in word_parses (any text), with its number of words."""
    rows = conn.execute(
        """SELECT lemma, count(*) FROM word_parses
           WHERE lemma IS NOT NULL AND postag IS NOT NULL
           GROUP BY lemma"""
    ).fetchall()
    return {lemma: count for lemma, count in rows}


def save_lexicon(
    conn: sqlite3.Connection,
    source: str,
    entries: Sequence[LexiconEntry],
    matches: Mapping[str, Optional[Tuple[str, str]]],
) -> None:
    """
    Save a dictionary's entries and each lemma's match, in one transaction.
    matches maps a lemma to (entry key, how it was found), or None when the
    dictionary lacks it. A model's identification of a lemma is kept. Entries
    of this source that no lemma uses any more are removed.
    """
    with conn:
        conn.executemany(
            """INSERT INTO lexicon_entries(source, key, headword, short_def, entry_html)
               VALUES (?,?,?,?,?)
               ON CONFLICT(source, key) DO UPDATE SET headword=excluded.headword,
                   short_def=excluded.short_def, entry_html=excluded.entry_html""",
            [(source, e.key, e.headword, e.short_def, e.entry_html) for e in entries],
        )
        ids = dict(
            conn.execute(
                "SELECT key, id FROM lexicon_entries WHERE source=?", (source,)
            ).fetchall()
        )
        conn.executemany(
            """INSERT INTO lemma_entries(lemma, entry_id, matched_by) VALUES (?,?,?)
               ON CONFLICT(lemma) DO UPDATE SET
                   entry_id=excluded.entry_id, matched_by=excluded.matched_by""",
            [
                (lemma, ids[match[0]], match[1]) if match else (lemma, None, None)
                for lemma, match in matches.items()
            ],
        )
        conn.execute(
            """DELETE FROM lexicon_entries WHERE source=? AND id NOT IN
               (SELECT entry_id FROM lemma_entries WHERE entry_id IS NOT NULL)""",
            (source,),
        )


def _lemma_entry(row) -> LemmaEntry:
    entry = None
    if row["id"] is not None:
        entry = LexiconEntry(
            id=row["id"],
            source=row["source"],
            key=row["key"],
            headword=row["headword"],
            short_def=row["short_def"],
            entry_html=row["entry_html"],
        )
    return LemmaEntry(
        lemma=row["lemma"],
        entry=entry,
        matched_by=row["matched_by"],
        model_def=row["model_def"],
        model_used=row["model_used"],
    )


def get_lemma_entries(
    conn: sqlite3.Connection, lemmas: Sequence[str], with_html: bool = True
) -> Dict[str, LemmaEntry]:
    """
    The saved entries of the given lemmas, by lemma (lemmas without one are
    left out). Without with_html, entry_html is "" (a list needs only the
    short definitions).
    """
    lemmas = list(dict.fromkeys(lemmas))
    if not lemmas:
        return {}
    html = "e.entry_html" if with_html else "''"
    rows = conn.execute(
        f"""SELECT l.lemma, l.matched_by, l.model_def, l.model_used,
                   {_ENTRY_COLUMNS}, {html} AS entry_html
            FROM lemma_entries l LEFT JOIN lexicon_entries e ON e.id = l.entry_id
            WHERE l.lemma IN ({",".join("?" * len(lemmas))})""",
        lemmas,
    ).fetchall()
    return {row["lemma"]: _lemma_entry(row) for row in rows}


def list_unidentified_lemmas(conn: sqlite3.Connection) -> List[str]:
    """Lemmas without a dictionary entry or a model's identification, most frequent first."""
    rows = conn.execute(
        """SELECT l.lemma FROM lemma_entries l
           LEFT JOIN (SELECT lemma, count(*) AS n FROM word_parses GROUP BY lemma) w
               ON w.lemma = l.lemma
           WHERE l.entry_id IS NULL AND l.model_def IS NULL
           ORDER BY coalesce(w.n, 0) DESC, l.lemma"""
    ).fetchall()
    return [r[0] for r in rows]
