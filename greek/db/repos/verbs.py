import json
import sqlite3
import unicodedata
from typing import Iterable, List, Optional, Set

from greek.models.verb import VerbParts


def normalize_verb(verb: str) -> str:
    """The form a verb is saved under: NFC, trimmed, lowercase."""
    return unicodedata.normalize("NFC", verb.strip()).lower()


def _row_to_parts(row) -> VerbParts:
    try:
        parts = json.loads(row["parts_json"])
    except (json.JSONDecodeError, TypeError):
        parts = []
    return VerbParts(
        id=row["id"],
        verb=row["verb"],
        model_used=row["model_used"],
        looked_up_at=row["looked_up_at"],
        parts=[p for p in parts if isinstance(p, dict)]
        if isinstance(parts, list)
        else [],
        dialect=row["dialect"],
    )


def save_verb_parts(
    conn: sqlite3.Connection,
    verb: str,
    model_used: str,
    parts: list,
    dialect: str = "",
) -> int:
    """
    Save a verb's principal parts by a model in a dialect; newer parts of the
    same verb by the same model in the same dialect replace them.
    """
    conn.execute(
        """INSERT INTO verb_parts(verb, dialect, model_used, parts_json)
           VALUES (?,?,?,?)
           ON CONFLICT(verb, dialect, model_used) DO UPDATE
           SET parts_json = excluded.parts_json, looked_up_at = datetime('now')""",
        (
            normalize_verb(verb),
            dialect,
            model_used,
            json.dumps(parts, ensure_ascii=False),
        ),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM verb_parts WHERE verb=? AND dialect=? AND model_used=?",
        (normalize_verb(verb), dialect, model_used),
    ).fetchone()[0]


def get_verb_parts(
    conn: sqlite3.Connection,
    verb: str,
    model_used: str,
    dialect: Optional[str] = None,
) -> Optional[VerbParts]:
    """
    A verb's saved parts by a model: in the dialect, if one is given;
    otherwise the newest in any dialect.
    """
    if dialect is not None:
        row = conn.execute(
            "SELECT * FROM verb_parts WHERE verb=? AND model_used=? AND dialect=?",
            (normalize_verb(verb), model_used, dialect),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT * FROM verb_parts WHERE verb=? AND model_used=?
               ORDER BY looked_up_at DESC, id DESC LIMIT 1""",
            (normalize_verb(verb), model_used),
        ).fetchone()
    return _row_to_parts(row) if row else None


def verbs_with_parts(
    conn: sqlite3.Connection,
    verbs: Iterable[str],
    model_used: str,
    dialect: Optional[str] = None,
) -> Set[str]:
    """
    The given verbs (as given) that have saved parts by the model: in the
    dialect, if one is given, else in any.
    """
    query = "SELECT verb FROM verb_parts WHERE model_used=?"
    params: list = [model_used]
    if dialect is not None:
        query += " AND dialect=?"
        params.append(dialect)
    saved = {r[0] for r in conn.execute(query, params)}
    return {v for v in verbs if normalize_verb(v) in saved}


def list_verb_parts(conn: sqlite3.Connection, verb: str) -> List[VerbParts]:
    """Every saved set of a verb's parts, by any model and in any dialect, newest first."""
    rows = conn.execute(
        """SELECT * FROM verb_parts WHERE verb=?
           ORDER BY looked_up_at DESC, id DESC""",
        (normalize_verb(verb),),
    ).fetchall()
    return [_row_to_parts(r) for r in rows]
