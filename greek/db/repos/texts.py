import sqlite3
from typing import List, Optional

from greek.models.text import TextMeta


def insert_text(
    conn: sqlite3.Connection,
    title: str,
    author: Optional[str],
    filename: str,
    filepath: str,
    line_count: int,
) -> int:
    cur = conn.execute(
        "INSERT INTO texts(title, author, filename, filepath, line_count) VALUES (?,?,?,?,?)",
        (title, author, filename, filepath, line_count),
    )
    conn.commit()
    return cur.lastrowid


def get_text(conn: sqlite3.Connection, text_id: int) -> Optional[TextMeta]:
    row = conn.execute("SELECT * FROM texts WHERE id=?", (text_id,)).fetchone()
    if row is None:
        return None
    return TextMeta(**dict(row))


def list_texts(
    conn: sqlite3.Connection, sort: str = "id", author: Optional[str] = None
) -> List[TextMeta]:
    valid_sorts = {"id", "title", "author", "imported_at"}
    if sort not in valid_sorts:
        sort = "id"
    query = "SELECT * FROM texts"
    params = []
    if author:
        query += " WHERE author LIKE ?"
        params.append(f"%{author}%")
    query += f" ORDER BY {sort}"
    rows = conn.execute(query, params).fetchall()
    return [TextMeta(**dict(r)) for r in rows]


def update_line_count(conn: sqlite3.Connection, text_id: int, line_count: int) -> None:
    conn.execute("UPDATE texts SET line_count=? WHERE id=?", (line_count, text_id))
    conn.commit()
