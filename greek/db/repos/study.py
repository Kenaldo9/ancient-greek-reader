import json
import sqlite3
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from greek.db.repos.lines import list_books
from greek.db.repos.verbs import normalize_verb
from greek.models.study import (
    BookStudy,
    LemmaForms,
    LemmaRequest,
    LineTranslation,
    PartsRequest,
    StudyBatch,
    StudyRequest,
    TranslationUnit,
    Usage,
    WordStudy,
    WordUse,
)

_BATCH_COLUMNS = (
    "id, text_id, scope, model, status, request_count, submitted_at, ended_at, "
    "collected_at, input_tokens, cache_write_tokens, cache_read_tokens, "
    "output_tokens, cost_usd, kind, effort, pilot, dialect"
)
_REQUEST_COLUMNS = (
    "batch_id, custom_id, first_line, last_line, word_count, status, failure, "
    "input_tokens, cache_write_tokens, cache_read_tokens, output_tokens, cost_usd"
)
_PARTS_COLUMNS = (
    "batch_id, custom_id, verbs, stored_count, status, failure, input_tokens, "
    "cache_write_tokens, cache_read_tokens, output_tokens, cost_usd"
)
_LEMMA_REQUEST_COLUMNS = (
    "batch_id, custom_id, lemmas, stored_count, status, failure, input_tokens, "
    "cache_write_tokens, cache_read_tokens, output_tokens, cost_usd"
)
_FORMS_COLUMNS = (
    "lemma, model_used, pos, headword, forms, comparison, construction, dialect"
)
_USE_COLUMNS = "text_id, line_number, token_index, model_used, use_label, effort, extra"
_USAGE = ("input_tokens", "cache_write_tokens", "cache_read_tokens", "output_tokens")
_REQUEST_TABLES = {
    "study": "study_requests",
    "parts": "parts_requests",
    "notes": "study_requests",
    "forms": "lemma_requests",
    "uses": "study_requests",
}


# --- Batches and their requests ----------------------------------------------


def _insert_batch(conn: sqlite3.Connection, batch: StudyBatch) -> None:
    conn.execute(
        """INSERT INTO study_batches(id, text_id, scope, model, status, request_count,
                                     kind, effort, pilot, dialect)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            batch.id,
            batch.text_id,
            batch.scope,
            batch.model,
            batch.status,
            batch.request_count,
            batch.kind,
            batch.effort,
            int(batch.pilot),
            batch.dialect,
        ),
    )


def save_submitted_batch(
    conn: sqlite3.Connection, batch: StudyBatch, requests: Sequence[StudyRequest]
) -> None:
    """Record a study or notes batch the API accepted and its requests, all pending."""
    with conn:
        _insert_batch(conn, batch)
        conn.executemany(
            """INSERT INTO study_requests(batch_id, custom_id, first_line, last_line,
                                          word_count)
               VALUES (?,?,?,?,?)""",
            [
                (batch.id, r.custom_id, r.first_line, r.last_line, r.word_count)
                for r in requests
            ],
        )


def save_submitted_parts_batch(
    conn: sqlite3.Connection, batch: StudyBatch, requests: Sequence[PartsRequest]
) -> None:
    """Record a principal-parts batch the API accepted and its requests, all pending."""
    with conn:
        _insert_batch(conn, batch)
        conn.executemany(
            """INSERT INTO parts_requests(batch_id, custom_id, verbs, verb_count)
               VALUES (?,?,?,?)""",
            [
                (
                    batch.id,
                    r.custom_id,
                    json.dumps(r.verbs, ensure_ascii=False),
                    len(r.verbs),
                )
                for r in requests
            ],
        )


def _batch(row) -> StudyBatch:
    values = dict(row)
    values["pilot"] = bool(values["pilot"])
    return StudyBatch(**values)


def get_study_batch(conn: sqlite3.Connection, batch_id: str) -> Optional[StudyBatch]:
    row = conn.execute(
        f"SELECT {_BATCH_COLUMNS} FROM study_batches WHERE id=?", (batch_id,)
    ).fetchone()
    return _batch(row) if row else None


def list_study_batches(conn: sqlite3.Connection) -> List[StudyBatch]:
    """Every submitted batch, oldest first."""
    rows = conn.execute(
        f"SELECT {_BATCH_COLUMNS} FROM study_batches ORDER BY submitted_at, rowid"
    ).fetchall()
    return [_batch(r) for r in rows]


def list_study_requests(conn: sqlite3.Connection, batch_id: str) -> List[StudyRequest]:
    """A batch's requests, in text order."""
    rows = conn.execute(
        f"""SELECT {_REQUEST_COLUMNS} FROM study_requests WHERE batch_id=?
            ORDER BY first_line""",
        (batch_id,),
    ).fetchall()
    return [StudyRequest(**dict(r)) for r in rows]


def _parts_request(row) -> PartsRequest:
    values = dict(row)
    values["verbs"] = json.loads(values["verbs"])
    return PartsRequest(**values)


def list_parts_requests(conn: sqlite3.Connection, batch_id: str) -> List[PartsRequest]:
    """A parts batch's requests, in the order submitted."""
    rows = conn.execute(
        f"""SELECT {_PARTS_COLUMNS} FROM parts_requests WHERE batch_id=?
            ORDER BY rowid""",
        (batch_id,),
    ).fetchall()
    return [_parts_request(r) for r in rows]


def set_batch_status(
    conn: sqlite3.Connection, batch_id: str, status: str, ended_at: Optional[str]
) -> None:
    """The batch's processing status as the API last reported it."""
    with conn:
        conn.execute(
            "UPDATE study_batches SET status=?, ended_at=? WHERE id=?",
            (status, ended_at, batch_id),
        )


def fail_study_request(
    conn: sqlite3.Connection,
    batch_id: str,
    custom_id: str,
    failure: str,
    usage: Optional[Usage] = None,
) -> None:
    """Mark a request failed and why, with what it was billed."""
    with conn:
        _finish_request(conn, batch_id, custom_id, "failed", failure, usage or Usage())


def _finish_request(conn, batch_id, custom_id, status, failure, usage: Usage):
    conn.execute(
        f"""UPDATE study_requests SET status=?, failure=?,
                {", ".join(f"{c}=?" for c in _USAGE)}, cost_usd=?
            WHERE batch_id=? AND custom_id=?""",
        (
            status,
            failure,
            *(getattr(usage, c) for c in _USAGE),
            usage.cost_usd,
            batch_id,
            custom_id,
        ),
    )


def finish_collect(conn: sqlite3.Connection, batch_id: str) -> StudyBatch:
    """Total a collected batch's usage and cost from its requests."""
    sums = ", ".join(f"coalesce(sum({c}), 0)" for c in (*_USAGE, "cost_usd"))
    table = _REQUEST_TABLES[get_study_batch(conn, batch_id).kind]
    with conn:
        totals = conn.execute(
            f"SELECT {sums} FROM {table} WHERE batch_id=?", (batch_id,)
        ).fetchone()
        conn.execute(
            f"""UPDATE study_batches SET {", ".join(f"{c}=?" for c in _USAGE)},
                    cost_usd=?, collected_at=datetime('now')
                WHERE id=?""",
            (*totals, batch_id),
        )
    return get_study_batch(conn, batch_id)


def pending_ranges(
    conn: sqlite3.Connection,
    text_id: int,
    model: str,
    kind: str = "study",
    pilot: bool = False,
    effort: Optional[str] = None,
) -> List[Tuple[int, int]]:
    """
    Line ranges of a text's requests of a kind still waiting to be collected:
    of pilot batches (at an effort, if given) when pilot, else of the others,
    since a pilot stores nothing.
    """
    query = """SELECT r.first_line, r.last_line FROM study_requests r
               JOIN study_batches b ON b.id = r.batch_id
               WHERE b.text_id=? AND b.model=? AND b.kind=? AND b.pilot=?
                 AND r.status='pending'"""
    params = [text_id, model, kind, int(pilot)]
    if effort is not None:
        query += " AND b.effort=?"
        params.append(effort)
    rows = conn.execute(query, params).fetchall()
    return [(r[0], r[1]) for r in rows]


def measured_output_per_word(
    conn: sqlite3.Connection,
    model: str,
    kind: str = "study",
    effort: Optional[str] = None,
) -> Optional[float]:
    """
    Output tokens per word over a model's succeeded requests of a kind (and
    effort, if given); None before any.
    """
    query = """SELECT sum(r.output_tokens), sum(r.word_count) FROM study_requests r
               JOIN study_batches b ON b.id = r.batch_id
               WHERE b.model=? AND b.kind=? AND r.status='succeeded'"""
    params = [model, kind]
    if effort is not None:
        query += " AND b.effort=?"
        params.append(effort)
    row = conn.execute(query, params).fetchone()
    return row[0] / row[1] if row[1] else None


# --- Results -----------------------------------------------------------------


def save_study_result(
    conn: sqlite3.Connection,
    unit: TranslationUnit,
    literal: Sequence[LineTranslation],
    words: Sequence[WordStudy],
    parses: Sequence[Tuple[int, int, str, str]],
    request: Optional[StudyRequest] = None,
    usage: Optional[Usage] = None,
) -> None:
    """
    Store one unit's results in one transaction: its fluent translation
    (replacing the same model's units that overlap it), literal lines, word
    glosses and notes, and the model's parses as (line_number, token_index,
    lemma, postag) for words the treebank lacks (only parts without a parse
    change). With a request, mark it succeeded with its usage and cost.
    """
    model = unit.model_used
    with conn:
        conn.execute(
            """DELETE FROM translation_units WHERE text_id=? AND model_used=?
               AND first_line <= ? AND last_line >= ?""",
            (unit.text_id, model, unit.last_line, unit.first_line),
        )
        conn.execute(
            """INSERT INTO translation_units(text_id, first_line, last_line, fluent,
                                             model_used)
               VALUES (?,?,?,?,?)""",
            (unit.text_id, unit.first_line, unit.last_line, unit.fluent, model),
        )
        conn.executemany(
            """INSERT INTO line_translations(text_id, line_number, literal, model_used)
               VALUES (?,?,?,?)
               ON CONFLICT(text_id, line_number, model_used)
               DO UPDATE SET literal=excluded.literal""",
            [(t.text_id, t.line_number, t.literal, t.model_used) for t in literal],
        )
        conn.executemany(
            """INSERT INTO word_study(text_id, line_number, token_index, gloss, note,
                                      parse_check, model_used, note_version)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(text_id, line_number, token_index, model_used)
               DO UPDATE SET gloss=excluded.gloss, note=excluded.note,
                             parse_check=excluded.parse_check,
                             note_version=excluded.note_version""",
            [
                (
                    w.text_id,
                    w.line_number,
                    w.token_index,
                    w.gloss,
                    w.note,
                    w.parse_check,
                    w.model_used,
                    w.note_version,
                )
                for w in words
            ],
        )
        conn.executemany(
            """UPDATE word_parses SET lemma=?, postag=?, source='model', model_used=?
               WHERE text_id=? AND line_number=? AND token_index=?
                 AND (lemma IS NULL OR postag IS NULL)""",
            [
                (lemma, postag, model, unit.text_id, line_number, token_index)
                for line_number, token_index, lemma, postag in parses
            ],
        )
        if request is not None:
            _finish_request(
                conn,
                request.batch_id,
                request.custom_id,
                "succeeded",
                None,
                usage or Usage(),
            )


def covered_units(
    conn: sqlite3.Connection,
    units: Sequence[TranslationUnit],
    word_counts: Dict[int, int],
    model: str,
) -> Set[Tuple[int, int]]:
    """
    The (first_line, last_line) of the units (all of one text) that already
    have a model's full results: the unit's fluent translation, every line's
    literal one, and a gloss for every word (word_counts: words per line).
    """
    if not units:
        return set()
    text_id = units[0].text_id
    saved = {
        (r[0], r[1])
        for r in conn.execute(
            """SELECT first_line, last_line FROM translation_units
               WHERE text_id=? AND model_used=?""",
            (text_id, model),
        )
    }
    literal = {
        r[0]
        for r in conn.execute(
            """SELECT line_number FROM line_translations
               WHERE text_id=? AND model_used=?""",
            (text_id, model),
        )
    }
    glossed: Dict[int, int] = dict(
        conn.execute(
            """SELECT line_number, count(*) FROM word_study
               WHERE text_id=? AND model_used=? GROUP BY line_number""",
            (text_id, model),
        ).fetchall()
    )
    covered = set()
    for u in units:
        lines = range(u.first_line, u.last_line + 1)
        if (
            (u.first_line, u.last_line) in saved
            and all(n in literal for n in lines)
            and all(glossed.get(n, 0) >= word_counts.get(n, 0) for n in lines)
        ):
            covered.add((u.first_line, u.last_line))
    return covered


def save_notes_result(
    conn: sqlite3.Connection,
    text_id: int,
    model: str,
    notes: Sequence[Tuple[int, int, str]],
    version: int,
    request: Optional[StudyRequest] = None,
    usage: Optional[Usage] = None,
) -> None:
    """
    Store a notes request's notes, as (line_number, token_index, note), in one
    transaction: only each word's note and note_version change. With a
    request, mark it succeeded with its usage and cost. Raises ValueError,
    storing nothing, when a word has no saved study data to update.
    """
    with conn:
        updated = 0
        for line_number, token_index, note in notes:
            updated += conn.execute(
                """UPDATE word_study SET note=?, note_version=?
                   WHERE text_id=? AND line_number=? AND token_index=?
                     AND model_used=?""",
                (note, version, text_id, line_number, token_index, model),
            ).rowcount
        if updated != len(notes):
            raise ValueError(
                f"{len(notes) - updated} of {len(notes)} words have no saved gloss"
            )
        if request is not None:
            _finish_request(
                conn,
                request.batch_id,
                request.custom_id,
                "succeeded",
                None,
                usage or Usage(),
            )


def succeed_study_request(
    conn: sqlite3.Connection, batch_id: str, custom_id: str, usage: Usage
) -> None:
    """Mark a request succeeded with its usage, storing nothing (a pilot's)."""
    with conn:
        _finish_request(conn, batch_id, custom_id, "succeeded", None, usage)


def units_with_notes(
    conn: sqlite3.Connection,
    units: Sequence[TranslationUnit],
    word_counts: Dict[int, int],
    model: str,
    version: int,
) -> Set[Tuple[int, int]]:
    """
    The (first_line, last_line) of the units (all of one text) whose every
    word (word_counts: words per line) has a model's note of a version.
    """
    if not units:
        return set()
    noted: Dict[int, int] = dict(
        conn.execute(
            """SELECT line_number, count(*) FROM word_study
               WHERE text_id=? AND model_used=? AND note_version=?
               GROUP BY line_number""",
            (units[0].text_id, model, version),
        ).fetchall()
    )
    return {
        (u.first_line, u.last_line)
        for u in units
        if all(
            noted.get(n, 0) >= word_counts.get(n, 0)
            for n in range(u.first_line, u.last_line + 1)
        )
    }


def list_translation_units(
    conn: sqlite3.Connection, text_id: int, start: int, end: int, model: str
) -> List[TranslationUnit]:
    """A model's fluent translations of the units overlapping lines start–end."""
    rows = conn.execute(
        """SELECT text_id, first_line, last_line, fluent, model_used
           FROM translation_units
           WHERE text_id=? AND model_used=? AND first_line <= ? AND last_line >= ?
           ORDER BY first_line""",
        (text_id, model, end, start),
    ).fetchall()
    return [TranslationUnit(**dict(r)) for r in rows]


def list_line_translations(
    conn: sqlite3.Connection, text_id: int, start: int, end: int, model: str
) -> List[LineTranslation]:
    """A model's literal translations of lines start–end."""
    rows = conn.execute(
        """SELECT text_id, line_number, literal, model_used FROM line_translations
           WHERE text_id=? AND model_used=? AND line_number BETWEEN ? AND ?
           ORDER BY line_number""",
        (text_id, model, start, end),
    ).fetchall()
    return [LineTranslation(**dict(r)) for r in rows]


def list_word_study(
    conn: sqlite3.Connection, text_id: int, start: int, end: int, model: str
) -> List[WordStudy]:
    """A model's glosses and notes for the words of lines start–end, in order."""
    rows = conn.execute(
        """SELECT text_id, line_number, token_index, gloss, note, parse_check,
                  model_used, note_version
           FROM word_study
           WHERE text_id=? AND model_used=? AND line_number BETWEEN ? AND ?
           ORDER BY line_number, token_index""",
        (text_id, model, start, end),
    ).fetchall()
    return [WordStudy(**dict(r)) for r in rows]


def study_line_glosses(
    conn: sqlite3.Connection,
    text_id: int,
    contents: Dict[int, str],
    start: int,
    end: int,
    model: str,
) -> Dict[int, List[str]]:
    """
    line_number → a model's gloss of each token (a line's whitespace-separated
    words, as the reader splits them), for the lines of start–end whose every
    token has one; contents maps those lines' numbers to their text.
    """
    by_line: Dict[int, Dict[int, str]] = {}
    for w in list_word_study(conn, text_id, start, end, model):
        by_line.setdefault(w.line_number, {})[w.token_index] = w.gloss
    glosses = {}
    for n, found in by_line.items():
        count = len(contents.get(n, "").split())
        if count and all(i in found for i in range(count)):
            glosses[n] = [found[i] for i in range(count)]
    return glosses


def book_study(conn: sqlite3.Connection, text_id: int, model: str) -> List[BookStudy]:
    """
    Each book's study data by a model, in order (list_books' books; a text
    without books counts as one, numbered ""). Units of one model never
    overlap (save_study_result replaces them), so their lines inside a book
    are summed.
    """
    books = [
        (b.number, b.line_start, b.line_end, b.count) for b in list_books(conn, text_id)
    ]
    if not books:
        first, last, count = conn.execute(
            "SELECT min(line_number), max(line_number), count(*) FROM lines "
            "WHERE text_id=?",
            (text_id,),
        ).fetchone()
        books = [("", first, last, count)] if count else []
    result = []
    for number, first, last, count in books:
        params = {"text": text_id, "model": model, "first": first, "last": last}
        literal, translated, glossed = conn.execute(
            """SELECT
                 (SELECT count(*) FROM line_translations
                  WHERE text_id=:text AND model_used=:model
                    AND line_number BETWEEN :first AND :last),
                 (SELECT coalesce(sum(min(last_line, :last)
                                      - max(first_line, :first) + 1), 0)
                  FROM translation_units
                  WHERE text_id=:text AND model_used=:model
                    AND first_line <= :last AND last_line >= :first),
                 (SELECT count(*) FROM word_study
                  WHERE text_id=:text AND model_used=:model
                    AND line_number BETWEEN :first AND :last)""",
            params,
        ).fetchone()
        result.append(
            BookStudy(number, first, last, count, literal, translated, glossed)
        )
    return result


# --- Principal parts -----------------------------------------------------------


def _finish_parts_request(conn, request: PartsRequest, stored, failure, usage: Usage):
    conn.execute(
        f"""UPDATE parts_requests SET status=?, failure=?, stored_count=?,
                {", ".join(f"{c}=?" for c in _USAGE)}, cost_usd=?
            WHERE batch_id=? AND custom_id=?""",
        (
            "failed" if failure else "succeeded",
            failure,
            stored,
            *(getattr(usage, c) for c in _USAGE),
            usage.cost_usd,
            request.batch_id,
            request.custom_id,
        ),
    )


def save_parts_result(
    conn: sqlite3.Connection,
    model: str,
    parts: Dict[str, list],
    request: Optional[PartsRequest] = None,
    failure: Optional[str] = None,
    usage: Optional[Usage] = None,
    dialect: str = "homeric",
) -> None:
    """
    Store a parts request's checked results in one transaction: each verb's
    parts (a list of {part, name, form, notes}) in verb_parts under the
    model and the batch's dialect, replacing that model's earlier parts in
    it. With a request, mark it succeeded, or failed with `failure` when
    some of its verbs were not stored, with its usage and cost.
    """
    with conn:
        conn.executemany(
            """INSERT INTO verb_parts(verb, dialect, model_used, parts_json)
               VALUES (?,?,?,?)
               ON CONFLICT(verb, dialect, model_used) DO UPDATE
               SET parts_json = excluded.parts_json, looked_up_at = datetime('now')""",
            [
                (
                    normalize_verb(verb),
                    dialect,
                    model,
                    json.dumps(p, ensure_ascii=False),
                )
                for verb, p in parts.items()
            ],
        )
        if request is not None:
            _finish_parts_request(conn, request, len(parts), failure, usage or Usage())


def fail_parts_request(
    conn: sqlite3.Connection,
    request: PartsRequest,
    failure: str,
    usage: Optional[Usage] = None,
) -> None:
    """Mark a parts request failed (nothing stored) and why, with what it was billed."""
    with conn:
        _finish_parts_request(conn, request, 0, failure, usage or Usage())


def pending_parts_verbs(conn: sqlite3.Connection, dialect: str, model: str) -> Set[str]:
    """Verbs of parts requests in a dialect (of any text) still waiting to be collected."""
    rows = conn.execute(
        """SELECT r.verbs FROM parts_requests r
           JOIN study_batches b ON b.id = r.batch_id
           WHERE b.dialect=? AND b.model=? AND r.status='pending'""",
        (dialect, model),
    ).fetchall()
    return {verb for row in rows for verb in json.loads(row[0])}


def measured_output_per_verb(conn: sqlite3.Connection, model: str) -> Optional[float]:
    """Output tokens per verb asked for, over a model's collected parts requests."""
    row = conn.execute(
        """SELECT sum(r.output_tokens), sum(r.verb_count) FROM parts_requests r
           JOIN study_batches b ON b.id = r.batch_id
           WHERE b.model=? AND r.status != 'pending' AND r.output_tokens > 0""",
        (model,),
    ).fetchone()
    return row[0] / row[1] if row[1] else None


# --- Dictionary forms -------------------------------------------------------------


def save_submitted_forms_batch(
    conn: sqlite3.Connection, batch: StudyBatch, requests: Sequence[LemmaRequest]
) -> None:
    """Record a dictionary-forms batch the API accepted and its requests, all pending."""
    with conn:
        _insert_batch(conn, batch)
        conn.executemany(
            """INSERT INTO lemma_requests(batch_id, custom_id, lemmas, lemma_count)
               VALUES (?,?,?,?)""",
            [
                (
                    batch.id,
                    r.custom_id,
                    json.dumps(r.lemmas, ensure_ascii=False),
                    len(r.lemmas),
                )
                for r in requests
            ],
        )


def list_lemma_requests(conn: sqlite3.Connection, batch_id: str) -> List[LemmaRequest]:
    """A forms batch's requests, in the order submitted."""
    rows = conn.execute(
        f"""SELECT {_LEMMA_REQUEST_COLUMNS} FROM lemma_requests WHERE batch_id=?
            ORDER BY rowid""",
        (batch_id,),
    ).fetchall()
    return [
        LemmaRequest(**{**dict(r), "lemmas": json.loads(r["lemmas"])}) for r in rows
    ]


def _finish_lemma_request(conn, request: LemmaRequest, stored, failure, usage: Usage):
    conn.execute(
        f"""UPDATE lemma_requests SET status=?, failure=?, stored_count=?,
                {", ".join(f"{c}=?" for c in _USAGE)}, cost_usd=?
            WHERE batch_id=? AND custom_id=?""",
        (
            "failed" if failure else "succeeded",
            failure,
            stored,
            *(getattr(usage, c) for c in _USAGE),
            usage.cost_usd,
            request.batch_id,
            request.custom_id,
        ),
    )


def save_forms_result(
    conn: sqlite3.Connection,
    forms: Sequence[LemmaForms],
    request: Optional[LemmaRequest] = None,
    failure: Optional[str] = None,
    usage: Optional[Usage] = None,
) -> None:
    """
    Store a forms request's checked results in one transaction, replacing
    each lemma's earlier form by the same model in the same dialect (a
    construction already stored is kept). With a request, mark it
    succeeded, or failed with `failure` when some of its lemmas were not
    stored, with its usage and cost.
    """
    with conn:
        conn.executemany(
            f"""INSERT INTO lemma_forms({_FORMS_COLUMNS}) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(lemma, dialect, model_used) DO UPDATE SET
                    pos=excluded.pos, headword=excluded.headword,
                    forms=excluded.forms, comparison=excluded.comparison,
                    construction=coalesce(excluded.construction, construction),
                    made_at=datetime('now')""",
            [
                (
                    f.lemma,
                    f.model_used,
                    f.pos,
                    f.headword,
                    f.forms,
                    f.comparison,
                    f.construction,
                    f.dialect,
                )
                for f in forms
            ],
        )
        if request is not None:
            _finish_lemma_request(conn, request, len(forms), failure, usage or Usage())


def fail_lemma_request(
    conn: sqlite3.Connection,
    request: LemmaRequest,
    failure: str,
    usage: Optional[Usage] = None,
) -> None:
    """Mark a forms request failed (nothing stored) and why, with what it was billed."""
    with conn:
        _finish_lemma_request(conn, request, 0, failure, usage or Usage())


def lemmas_with_forms(
    conn: sqlite3.Connection, lemmas: Iterable[str], model: str, dialect: str
) -> Set[str]:
    """The given lemmas that have a dictionary form by the model in the dialect."""
    saved = {
        r[0]
        for r in conn.execute(
            "SELECT lemma FROM lemma_forms WHERE model_used=? AND dialect=?",
            (model, dialect),
        )
    }
    return {lemma for lemma in lemmas if lemma in saved}


def pending_forms_lemmas(
    conn: sqlite3.Connection, dialect: str, model: str
) -> Set[str]:
    """Lemmas of forms requests in a dialect (of any text) still waiting to be collected."""
    rows = conn.execute(
        """SELECT r.lemmas FROM lemma_requests r
           JOIN study_batches b ON b.id = r.batch_id
           WHERE b.dialect=? AND b.model=? AND r.status='pending'""",
        (dialect, model),
    ).fetchall()
    return {lemma for row in rows for lemma in json.loads(row[0])}


def measured_output_per_lemma(conn: sqlite3.Connection, model: str) -> Optional[float]:
    """Output tokens per lemma asked for, over a model's collected forms requests."""
    row = conn.execute(
        """SELECT sum(r.output_tokens), sum(r.lemma_count) FROM lemma_requests r
           JOIN study_batches b ON b.id = r.batch_id
           WHERE b.model=? AND r.status != 'pending' AND r.output_tokens > 0""",
        (model,),
    ).fetchone()
    return row[0] / row[1] if row[1] else None


def get_lemma_forms(
    conn: sqlite3.Connection,
    lemmas: Sequence[str],
    model: str,
    dialect: Optional[str] = None,
) -> Dict[str, LemmaForms]:
    """
    The saved dictionary forms of the given lemmas, by lemma: in the dialect
    (if given) by the model, else in the dialect by any model, else the
    model's in another dialect, else the newest. Lemmas without one are
    left out.
    """
    lemmas = list(dict.fromkeys(lemmas))
    if not lemmas:
        return {}
    rows = conn.execute(
        f"""SELECT {_FORMS_COLUMNS} FROM lemma_forms
            WHERE lemma IN ({",".join("?" * len(lemmas))})
            ORDER BY dialect IS ? DESC, model_used = ? DESC, made_at DESC""",
        (*lemmas, dialect, model),
    ).fetchall()
    found: Dict[str, LemmaForms] = {}
    for r in rows:
        found.setdefault(r["lemma"], LemmaForms(**dict(r)))
    return found


# --- Use in context ---------------------------------------------------------------


def save_uses_result(
    conn: sqlite3.Connection,
    text_id: int,
    model: str,
    first_line: int,
    last_line: int,
    uses: Sequence[WordUse],
    request: Optional[StudyRequest] = None,
    usage: Optional[Usage] = None,
) -> None:
    """
    Store a unit's use labels in one transaction, replacing the model's
    earlier labels for lines first_line–last_line (extras it no longer gives
    included). With a request, mark it succeeded with its usage and cost.
    """
    with conn:
        conn.execute(
            """DELETE FROM word_uses WHERE text_id=? AND model_used=?
               AND line_number BETWEEN ? AND ?""",
            (text_id, model, first_line, last_line),
        )
        conn.executemany(
            f"INSERT INTO word_uses({_USE_COLUMNS}) VALUES (?,?,?,?,?,?,?)",
            [
                (
                    u.text_id,
                    u.line_number,
                    u.token_index,
                    u.model_used,
                    u.use_label,
                    u.effort,
                    int(u.extra),
                )
                for u in uses
            ],
        )
        if request is not None:
            _finish_request(
                conn,
                request.batch_id,
                request.custom_id,
                "succeeded",
                None,
                usage or Usage(),
            )


def labelled_words(
    conn: sqlite3.Connection, text_id: int, model: str
) -> Set[Tuple[int, int]]:
    """The (line_number, token_index) of a text's words with a model's use label."""
    return {
        (r[0], r[1])
        for r in conn.execute(
            """SELECT line_number, token_index FROM word_uses
               WHERE text_id=? AND model_used=?""",
            (text_id, model),
        )
    }


def _word_use(row) -> WordUse:
    values = dict(row)
    values["extra"] = bool(values["extra"])
    return WordUse(**values)


def list_word_uses(
    conn: sqlite3.Connection, text_id: int, start: int, end: int, model: str
) -> List[WordUse]:
    """A model's use labels for the words of lines start–end, in order."""
    rows = conn.execute(
        f"""SELECT {_USE_COLUMNS} FROM word_uses
            WHERE text_id=? AND model_used=? AND line_number BETWEEN ? AND ?
            ORDER BY line_number, token_index""",
        (text_id, model, start, end),
    ).fetchall()
    return [_word_use(r) for r in rows]


def saved_word_uses(
    conn: sqlite3.Connection, text_id: int, start: int, end: int, model: str
) -> Dict[Tuple[int, int], WordUse]:
    """
    (line, token) → each word's saved use label in lines start–end, chosen
    as get_word_use() chooses one: the model's, else the newest by any model.
    """
    rows = conn.execute(
        f"""SELECT {_USE_COLUMNS} FROM (
              SELECT *, row_number() OVER (
                PARTITION BY line_number, token_index
                ORDER BY model_used = ? DESC, made_at DESC) AS choice
              FROM word_uses
              WHERE text_id=? AND line_number BETWEEN ? AND ?)
            WHERE choice = 1""",
        (model, text_id, start, end),
    ).fetchall()
    return {(r["line_number"], r["token_index"]): _word_use(r) for r in rows}


def get_word_use(
    conn: sqlite3.Connection,
    text_id: int,
    line_number: int,
    token_index: int,
    model: str,
) -> Optional[WordUse]:
    """A word's saved use label: the model's, else the newest by any model."""
    row = conn.execute(
        f"""SELECT {_USE_COLUMNS} FROM word_uses
            WHERE text_id=? AND line_number=? AND token_index=?
            ORDER BY model_used = ? DESC, made_at DESC LIMIT 1""",
        (text_id, line_number, token_index, model),
    ).fetchone()
    return _word_use(row) if row else None
