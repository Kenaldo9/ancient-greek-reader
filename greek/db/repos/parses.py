import sqlite3
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from greek.display.analysis import _PUNCT_STRIP
from greek.models.study import (
    TreebankNode,
    TreebankSentence,
    TreebankSource,
    WordParse,
)
from greek.search import citation_form

_PARSE_COLUMNS = (
    "text_id, line_number, token_index, part, form, lemma, postag, "
    "sentence_id, source, model_used, node_id"
)
_NODE_COLUMNS = (
    "text_id, sentence_id, node_id, head, relation, form, lemma, postag, artificial"
)


def _parse_values(p: WordParse) -> tuple:
    return (
        p.text_id,
        p.line_number,
        p.token_index,
        p.part,
        p.form,
        p.lemma,
        p.postag,
        p.sentence_id,
        p.source,
        p.model_used,
        p.node_id,
    )


def _replace_nodes(
    conn: sqlite3.Connection, text_id: int, nodes: Sequence[TreebankNode]
) -> None:
    conn.execute("DELETE FROM tb_nodes WHERE text_id=?", (text_id,))
    conn.executemany(
        f"INSERT INTO tb_nodes({_NODE_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                text_id,
                n.sentence_id,
                n.node_id,
                n.head,
                n.relation,
                n.form,
                n.lemma,
                n.postag,
                int(n.artificial),
            )
            for n in nodes
        ],
    )


def save_treebank(
    conn: sqlite3.Connection,
    text_id: int,
    sentences: Sequence[TreebankSentence],
    parses: Sequence[WordParse],
    nodes: Sequence[TreebankNode] = (),
) -> None:
    """
    Replace a text's treebank sentences, nodes and treebank parses in one
    transaction. A parse a model supplied for a word the treebank lacks is
    kept (and linked to its node), unless the treebank now has a parse for
    that word.
    """
    with conn:
        conn.execute("DELETE FROM tb_sentences WHERE text_id=?", (text_id,))
        conn.execute(
            "DELETE FROM word_parses WHERE text_id=? AND source='treebank'",
            (text_id,),
        )
        conn.executemany(
            """INSERT INTO tb_sentences(
                   text_id, sentence_id, first_line, last_line, annotation)
               VALUES (?,?,?,?,?)""",
            [
                (text_id, s.sentence_id, s.first_line, s.last_line, s.annotation)
                for s in sentences
            ],
        )
        _replace_nodes(conn, text_id, nodes)
        insert = (
            f"INSERT INTO word_parses({_PARSE_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        )
        conn.executemany(
            insert
            + """ ON CONFLICT(text_id, line_number, token_index, part) DO UPDATE SET
                  form=excluded.form, lemma=excluded.lemma, postag=excluded.postag,
                  sentence_id=excluded.sentence_id, source=excluded.source,
                  model_used=excluded.model_used, node_id=excluded.node_id""",
            [_parse_values(p) for p in parses if p.has_parse],
        )
        conn.executemany(
            insert
            + """ ON CONFLICT(text_id, line_number, token_index, part) DO UPDATE SET
                  node_id=excluded.node_id""",
            [_parse_values(p) for p in parses if not p.has_parse],
        )


def save_tb_nodes(
    conn: sqlite3.Connection,
    text_id: int,
    nodes: Sequence[TreebankNode],
    links: Sequence[Tuple[int, int, int, Optional[int]]],
) -> int:
    """
    Replace a text's treebank nodes and link its saved words to them, as
    (line_number, token_index, part, node_id), in one transaction; nothing
    else of a word changes. Returns the number of word rows linked.
    """
    with conn:
        _replace_nodes(conn, text_id, nodes)
        conn.execute("UPDATE word_parses SET node_id=NULL WHERE text_id=?", (text_id,))
        linked = 0
        for line_number, token_index, part, node_id in links:
            linked += conn.execute(
                """UPDATE word_parses SET node_id=?
                   WHERE text_id=? AND line_number=? AND token_index=? AND part=?""",
                (node_id, text_id, line_number, token_index, part),
            ).rowcount
    return linked


def list_word_parses(
    conn: sqlite3.Connection,
    text_id: int,
    start: Optional[int] = None,
    end: Optional[int] = None,
) -> List[WordParse]:
    """A text's word parses (lines start–end if given), in text order."""
    query = f"SELECT {_PARSE_COLUMNS} FROM word_parses WHERE text_id=?"
    params: list = [text_id]
    if start is not None and end is not None:
        query += " AND line_number BETWEEN ? AND ?"
        params += [start, end]
    rows = conn.execute(
        query + " ORDER BY line_number, token_index, part", params
    ).fetchall()
    return [WordParse(**dict(r)) for r in rows]


def verb_lemmas(
    conn: sqlite3.Connection,
    text_id: int,
    start: Optional[int] = None,
    end: Optional[int] = None,
) -> List[str]:
    """
    The lemmas of a text's verbs (lines start–end if given), treebank or
    model parses, each once, in the order they first appear.
    """
    query = """SELECT lemma FROM word_parses
               WHERE text_id=? AND lemma IS NOT NULL AND postag LIKE 'v%'"""
    params: list = [text_id]
    if start is not None and end is not None:
        query += " AND line_number BETWEEN ? AND ?"
        params += [start, end]
    rows = conn.execute(
        query + " GROUP BY lemma ORDER BY min(line_number * 1000 + token_index)",
        params,
    ).fetchall()
    return [r[0] for r in rows]


_POS_NAMES = {"n": "noun", "a": "adjective", "v": "verb"}


def lemmas_with_pos(
    conn: sqlite3.Connection,
    text_id: int,
    ranges: Sequence[Tuple[int, int]],
    letters: str = "nav",
) -> List[Tuple[str, str]]:
    """
    The lemmas of a text's lines in the ranges (start, end) tagged with one
    of the parts of speech `letters` (n noun, a adjective, v verb), each
    once, in the order they first appear, with the one of them ("noun",
    "adjective" or "verb") the lemma has most often in the whole text.
    """
    letters = "".join(c for c in letters if c in _POS_NAMES)
    if not letters:
        return []
    marks = ",".join("?" * len(letters))
    seen: Dict[str, int] = {}
    for start, end in ranges:
        for lemma, first in conn.execute(
            f"""SELECT lemma, min(line_number * 1000 + token_index) FROM word_parses
                WHERE text_id=? AND lemma IS NOT NULL
                  AND substr(postag, 1, 1) IN ({marks})
                  AND line_number BETWEEN ? AND ?
                GROUP BY lemma""",
            (text_id, *letters, start, end),
        ):
            seen[lemma] = min(first, seen.get(lemma, first))
    if not seen:
        return []
    counts: Dict[str, Dict[str, int]] = {}
    for lemma, letter, n in conn.execute(
        f"""SELECT lemma, substr(postag, 1, 1), count(*) FROM word_parses
            WHERE text_id=? AND lemma IN ({",".join("?" * len(seen))})
              AND substr(postag, 1, 1) IN ({marks})
            GROUP BY lemma, substr(postag, 1, 1)""",
        (text_id, *seen, *letters),
    ):
        counts.setdefault(lemma, {})[letter] = n
    return [
        (lemma, _POS_NAMES[max(counts[lemma], key=lambda k: (counts[lemma][k], k))])
        for lemma in sorted(seen, key=seen.get)
    ]


def nominal_lemmas(
    conn: sqlite3.Connection, text_id: int, ranges: Sequence[Tuple[int, int]]
) -> List[Tuple[str, str]]:
    """
    The noun and adjective lemmas of a text's lines in the ranges (start,
    end), each once, in the order they first appear, with the part of speech
    ("noun" or "adjective") the lemma has most often in the whole text.
    """
    return lemmas_with_pos(conn, text_id, ranges, "na")


def lemma_forms_seen(
    conn: sqlite3.Connection, text_id: int, lemmas: Sequence[str], limit: int = 6
) -> Dict[str, List[str]]:
    """
    Up to `limit` distinct forms of each lemma in a text, as printed with a
    final grave accent made acute (words the treebank splits are left out),
    the most frequent first. A lemma that isn't a name is compared in lower
    case.
    """
    lemmas = list(dict.fromkeys(lemmas))
    if not lemmas:
        return {}
    rows = conn.execute(
        f"""SELECT w.lemma, w.token_index, l.content FROM word_parses w
            JOIN lines l ON l.text_id = w.text_id AND l.line_number = w.line_number
            WHERE w.text_id=? AND w.lemma IN ({",".join("?" * len(lemmas))})
              AND w.part = 0
              AND NOT EXISTS (SELECT 1 FROM word_parses o
                              WHERE o.text_id = w.text_id
                                AND o.line_number = w.line_number
                                AND o.token_index = w.token_index AND o.part = 1)""",
        (text_id, *lemmas),
    ).fetchall()
    counts: Dict[str, Dict[str, int]] = {}
    for lemma, index, content in rows:
        words = content.split()
        if index >= len(words):
            continue
        form = citation_form(words[index].strip(_PUNCT_STRIP))
        if not lemma[:1].isupper():
            form = form.lower()
        by_form = counts.setdefault(lemma, {})
        by_form[form] = by_form.get(form, 0) + 1
    return {
        lemma: sorted(forms, key=lambda f: (-forms[f], f))[:limit]
        for lemma, forms in counts.items()
    }


def has_word_parses(
    conn: sqlite3.Connection, text_id: int, start: int, end: int
) -> bool:
    """Whether any line in [start, end] of a text has treebank data."""
    return (
        conn.execute(
            """SELECT 1 FROM word_parses
               WHERE text_id=? AND line_number BETWEEN ? AND ? LIMIT 1""",
            (text_id, start, end),
        ).fetchone()
        is not None
    )


def list_missing_parses(conn: sqlite3.Connection, text_id: int) -> List[WordParse]:
    """A text's words, or parts of words, without a lemma and parse."""
    rows = conn.execute(
        f"""SELECT {_PARSE_COLUMNS} FROM word_parses
            WHERE text_id=? AND (lemma IS NULL OR postag IS NULL)
            ORDER BY line_number, token_index, part""",
        (text_id,),
    ).fetchall()
    return [WordParse(**dict(r)) for r in rows]


def count_tb_nodes(conn: sqlite3.Connection, text_id: int) -> int:
    """How many treebank nodes a text has saved."""
    return conn.execute(
        "SELECT count(*) FROM tb_nodes WHERE text_id=?", (text_id,)
    ).fetchone()[0]


def list_sentence_nodes(
    conn: sqlite3.Connection, text_id: int, sentence_id: int
) -> List[TreebankNode]:
    """Every node of a treebank sentence, in order."""
    rows = conn.execute(
        f"""SELECT {_NODE_COLUMNS} FROM tb_nodes WHERE text_id=? AND sentence_id=?
            ORDER BY node_id""",
        (text_id, sentence_id),
    ).fetchall()
    return [
        TreebankNode(**{**dict(r), "artificial": bool(r["artificial"])}) for r in rows
    ]


def list_tb_nodes(
    conn: sqlite3.Connection, text_id: int, sentence_ids: Iterable[int]
) -> List[TreebankNode]:
    """Every node of some treebank sentences of a text, by sentence and node."""
    wanted = sorted(set(sentence_ids))
    nodes: List[TreebankNode] = []
    for i in range(0, len(wanted), 500):  # SQLite's limit on parameters
        chunk = wanted[i : i + 500]
        rows = conn.execute(
            f"""SELECT {_NODE_COLUMNS} FROM tb_nodes
                WHERE text_id=? AND sentence_id IN ({",".join("?" * len(chunk))})
                ORDER BY sentence_id, node_id""",
            (text_id, *chunk),
        ).fetchall()
        nodes += [
            TreebankNode(**{**dict(r), "artificial": bool(r["artificial"])})
            for r in rows
        ]
    return nodes


def node_positions(
    conn: sqlite3.Connection, text_id: int, sentence_id: int
) -> Dict[int, Tuple[int, int]]:
    """node_id → (line_number, token_index) of the word of the text it belongs to."""
    rows = conn.execute(
        """SELECT node_id, line_number, token_index FROM word_parses
           WHERE text_id=? AND sentence_id=? AND node_id IS NOT NULL""",
        (text_id, sentence_id),
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def lemma_frequency(
    conn: sqlite3.Connection, text_id: int, lemma: str, start: int, end: int
) -> Tuple[int, int]:
    """
    How many words of a text have a lemma, and how many of them are in lines
    start–end (a book). A homograph digit is ignored (ἄγω and ἄγω2 count
    together), as first_in_book() ignores it.
    """
    key = unicodedata.normalize("NFC", lemma).rstrip("0123456789")
    row = conn.execute(
        """SELECT count(*), coalesce(sum(line_number BETWEEN ? AND ?), 0)
           FROM word_parses
           WHERE text_id=? AND (lemma=? OR lemma GLOB ? || '[0-9]*')""",
        (start, end, text_id, key, key),
    ).fetchone()
    return row[0], row[1]


def list_tb_sentences(conn: sqlite3.Connection, text_id: int) -> List[TreebankSentence]:
    """A text's treebank sentences, in text order."""
    rows = conn.execute(
        """SELECT text_id, sentence_id, first_line, last_line, annotation
           FROM tb_sentences
           WHERE text_id=? ORDER BY first_line, last_line, sentence_id""",
        (text_id,),
    ).fetchall()
    return [TreebankSentence(**dict(r)) for r in rows]


def list_tb_sentences_between(
    conn: sqlite3.Connection, text_id: int, start: int, end: int
) -> List[TreebankSentence]:
    """The treebank sentences with words in lines start–end, in text order."""
    rows = conn.execute(
        """SELECT text_id, sentence_id, first_line, last_line, annotation
           FROM tb_sentences
           WHERE text_id=? AND first_line <= ? AND last_line >= ?
           ORDER BY first_line, last_line, sentence_id""",
        (text_id, end, start),
    ).fetchall()
    return [TreebankSentence(**dict(r)) for r in rows]


def sentence_annotations(
    conn: sqlite3.Connection, text_id: int, sentence_ids: Iterable[int]
) -> Dict[int, str]:
    """sentence_id → "manual" or "auto", for some sentences of a text."""
    wanted = sorted(set(sentence_ids))
    found: Dict[int, str] = {}
    for i in range(0, len(wanted), 500):  # SQLite's limit on parameters
        chunk = wanted[i : i + 500]
        found.update(
            conn.execute(
                f"""SELECT sentence_id, annotation FROM tb_sentences
                    WHERE text_id=? AND sentence_id IN ({",".join("?" * len(chunk))})""",
                (text_id, *chunk),
            ).fetchall()
        )
    return found


def annotation_counts(conn: sqlite3.Connection, text_id: int) -> Dict[str, int]:
    """How many of a text's treebank sentences are "manual" and how many "auto"."""
    return dict(
        conn.execute(
            """SELECT annotation, count(*) FROM tb_sentences WHERE text_id=?
               GROUP BY annotation""",
            (text_id,),
        ).fetchall()
    )


def save_tb_source(conn: sqlite3.Connection, source: TreebankSource) -> None:
    """Record (or replace) which treebank a text's words come from."""
    with conn:
        conn.execute(
            """INSERT INTO tb_sources(text_id, work, corpus, filename, licence)
               VALUES (?,?,?,?,?)
               ON CONFLICT(text_id) DO UPDATE SET
                 work=excluded.work, corpus=excluded.corpus,
                 filename=excluded.filename, licence=excluded.licence,
                 imported_at=datetime('now')""",
            (
                source.text_id,
                source.work,
                source.corpus,
                source.filename,
                source.licence,
            ),
        )


def get_tb_source(conn: sqlite3.Connection, text_id: int) -> Optional[TreebankSource]:
    """Which treebank a text's words come from; None when no import recorded it."""
    row = conn.execute(
        """SELECT text_id, work, corpus, filename, licence, imported_at
           FROM tb_sources WHERE text_id=?""",
        (text_id,),
    ).fetchone()
    return TreebankSource(**dict(row)) if row else None
