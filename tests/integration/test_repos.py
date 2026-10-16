import shutil
import sqlite3
import unicodedata

import pytest

import greek.db.connection as connection
from greek.db.connection import run_migrations
from greek.db.repos.texts import insert_text, get_text, list_texts
from greek.db.repos.lines import (
    insert_lines,
    get_lines,
    list_books,
    resolve_line,
    search_lines,
)
from greek.db.repos.verbs import (
    get_verb_parts,
    list_verb_parts,
    save_verb_parts,
)


# --- Texts ---


def test_insert_and_get_text(db):
    text_id = insert_text(db, "Iliad", "Homer", "iliad.txt", "/data/iliad.txt", 100)
    text = get_text(db, text_id)
    assert text is not None
    assert text.title == "Iliad"
    assert text.author == "Homer"
    assert text.filename == "iliad.txt"
    assert text.line_count == 100


def test_get_text_not_found(db):
    assert get_text(db, 9999) is None


def test_list_texts_all(db):
    insert_text(db, "Iliad", "Homer", "iliad.txt", "/data/iliad.txt", 10)
    insert_text(
        db, "Histories", "Herodotus", "herodotus.txt", "/data/herodotus.txt", 20
    )
    texts = list_texts(db)
    assert len(texts) == 2


def test_list_texts_author_filter(db):
    insert_text(db, "Iliad", "Homer", "iliad.txt", "/data/iliad.txt", 10)
    insert_text(db, "Odyssey", "Homer", "odyssey.txt", "/data/odyssey.txt", 10)
    insert_text(
        db, "Histories", "Herodotus", "herodotus.txt", "/data/herodotus.txt", 10
    )
    texts = list_texts(db, author="Homer")
    assert len(texts) == 2
    assert all(t.author == "Homer" for t in texts)


def test_list_texts_sort_title(db):
    insert_text(db, "Odyssey", "Homer", "odyssey.txt", "/data/odyssey.txt", 10)
    insert_text(db, "Iliad", "Homer", "iliad.txt", "/data/iliad.txt", 10)
    texts = list_texts(db, sort="title")
    titles = [t.title for t in texts]
    assert titles == sorted(titles)


def test_duplicate_filename_raises(db):
    insert_text(db, "Iliad", "Homer", "iliad.txt", "/data/iliad.txt", 10)
    with pytest.raises(sqlite3.IntegrityError):
        insert_text(db, "Iliad v2", "Homer", "iliad.txt", "/data/iliad.txt", 10)


# --- Lines ---


def test_insert_and_get_lines_with_references(db_with_text):
    db, text_id = db_with_text
    lines = get_lines(db, text_id)
    assert len(lines) == 10
    assert lines[0].content == "Line 1"
    assert lines[0].reference == "1.1"
    assert lines[0].line_number == 1


def test_insert_lines_without_references(db):
    text_id = insert_text(db, "Test", None, "test.txt", "/data/test.txt", 3)
    insert_lines(db, text_id, ["a", "b", "c"])
    lines = get_lines(db, text_id)
    assert len(lines) == 3
    assert all(line.reference is None for line in lines)


def test_get_lines_range(db_with_text):
    db, text_id = db_with_text
    lines = get_lines(db, text_id, start=2, end=4)
    assert len(lines) == 3
    assert lines[0].line_number == 2
    assert lines[-1].line_number == 4


def test_get_lines_ordered(db_with_text):
    db, text_id = db_with_text
    lines = get_lines(db, text_id)
    nums = [ln.line_number for ln in lines]
    assert nums == sorted(nums)


# --- Cascade deletes ---


def test_delete_text_cascades_lines(db_with_text):
    db, text_id = db_with_text
    db.execute("DELETE FROM texts WHERE id=?", (text_id,))
    db.commit()
    lines = get_lines(db, text_id)
    assert lines == []


# --- Search (FTS over the accent-free copy of each line) ---


@pytest.fixture
def iliad(db):
    text_id = insert_text(db, "Iliad", "Homer", "il.txt", "/d/il.txt", 4)
    insert_lines(
        db,
        text_id,
        [
            "μῆνιν ἄειδε θεὰ Πηληϊάδεω Ἀχιλῆος",
            "οὐλομένην, ἣ μυρί᾽ Ἀχαιοῖς ἄλγε᾽ ἔθηκε,",
            "Μῆνιν Ἀπόλλωνος ἑκατηβελέταο ἄνακτος·",
            "μήνιος ἀλευάμενος",
        ],
        ["1.1", "1.2", "1.75", "5.444"],
    )
    return text_id


def _refs(found):
    lines, total = found
    return [ln.reference for ln in lines], total


def test_insert_lines_stores_plain_copy(db, iliad):
    row = db.execute("SELECT plain FROM lines WHERE reference='1.1'").fetchone()
    assert row["plain"] == "μηνιν αειδε θεα πηληιαδεω αχιληοσ"


def test_get_lines_unaffected_by_plain_column(db, iliad):
    [line] = get_lines(db, iliad, 1, 1)
    assert line.content.startswith("μῆνιν") and line.reference == "1.1"


def test_search_ignores_accents_and_case(db, iliad):
    for query in ("μῆνιν", "μηνιν", "ΜΗΝΙΝ", "Μῆνιν"):
        assert _refs(search_lines(db, iliad, query)) == (["1.1", "1.75"], 2)


def test_search_elided_word(db, iliad):
    assert _refs(search_lines(db, iliad, "ἄλγε")) == (["1.2"], 1)


def test_search_all_words_must_match(db, iliad):
    assert _refs(search_lines(db, iliad, "μῆνιν Ἀχιλῆος")) == (["1.1"], 1)


def test_search_prefix(db, iliad):
    assert _refs(search_lines(db, iliad, "μην*")) == (["1.1", "1.75", "5.444"], 3)


def test_search_phrase(db, iliad):
    assert _refs(search_lines(db, iliad, '"ἄειδε θεὰ"')) == (["1.1"], 1)
    assert _refs(search_lines(db, iliad, '"θεὰ ἄειδε"')) == ([], 0)


def test_search_limit_keeps_total(db, iliad):
    assert _refs(search_lines(db, iliad, "μην*", limit=2)) == (["1.1", "1.75"], 3)


def test_search_only_in_the_given_text(db, iliad):
    other = insert_text(db, "Other", None, "o.txt", "/d/o.txt", 1)
    insert_lines(db, other, ["μῆνιν"], ["9.9"])
    assert _refs(search_lines(db, other, "μῆνιν")) == (["9.9"], 1)


@pytest.mark.parametrize(
    "query", ["", "  ", "·", '"', "AND", "a OR", "(μῆνιν", "-μῆνιν", "x:y"]
)
def test_search_never_breaks_on_operators(db, iliad, query):
    search_lines(db, iliad, query)  # must not raise sqlite3.OperationalError


def test_search_no_words_finds_nothing(db, iliad):
    assert search_lines(db, iliad, "  ·  ") == ([], 0)


def test_search_reads_the_full_text_hits_first(db, iliad):
    # Walking the text's lines instead runs the MATCH again for each line:
    # about 6 s to count δέ in the Iliad
    statements = []
    db.set_trace_callback(statements.append)
    search_lines(db, iliad, "μῆνιν")
    db.set_trace_callback(None)
    # FTS5's own lookups are traced too, commented out with "--"
    searches = [sql for sql in statements if sql.startswith("SELECT")]
    assert len(searches) == 2
    for sql in searches:
        plan = db.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
        assert plan[0][3].startswith("SCAN lines_fts")


def test_deleted_lines_leave_the_index(db, iliad):
    db.execute("DELETE FROM lines WHERE reference='1.75'")
    db.commit()
    assert _refs(search_lines(db, iliad, "μῆνιν")) == (["1.1"], 1)


def test_migration_fills_plain_for_existing_lines(tmp_path, monkeypatch):
    """Lines imported before migration 009 get their plain copy and are indexed."""
    old_dir = tmp_path / "migrations"
    old_dir.mkdir()
    for f in sorted(connection._MIGRATIONS_DIR.glob("*.sql")):
        if f.name < "009":
            shutil.copy(f, old_dir / f.name)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(connection, "_MIGRATIONS_DIR", old_dir)
    run_migrations(conn)
    text_id = insert_text(conn, "Iliad", None, "il.txt", "/d/il.txt", 1)
    conn.execute(
        "INSERT INTO lines(text_id, line_number, content, reference) VALUES (?, 1, ?, '1.1')",
        (text_id, "μῆνιν ἄειδε θεὰ"),
    )
    conn.commit()

    monkeypatch.undo()
    run_migrations(conn)
    assert conn.execute("SELECT plain FROM lines").fetchone()[0] == "μηνιν αειδε θεα"
    assert _refs(search_lines(conn, text_id, "μηνιν")) == (["1.1"], 1)


# --- Books ---


def test_list_books_verse(db_with_text):
    db, text_id = db_with_text
    books = list_books(db, text_id)
    assert [b.number for b in books] == ["1", "2"]
    first, second = books
    assert (first.ref_start, first.ref_end) == ("1.1", "1.5")
    assert (first.line_start, first.line_end, first.count) == (1, 5, 5)
    assert (second.ref_start, second.ref_end) == ("2.1", "2.5")
    assert (second.line_start, second.line_end, second.count) == (6, 10, 5)


def test_list_books_prose_three_level_refs(db):
    text_id = insert_text(db, "Histories", "Herodotus", "hdt.txt", "/d/hdt.txt", 4)
    insert_lines(
        db, text_id, ["a", "b", "c", "d"], ["1.1.0", "1.2.1", "2.1.1", "2.1.2"]
    )
    books = list_books(db, text_id)
    assert [(b.number, b.ref_start, b.ref_end, b.count) for b in books] == [
        ("1", "1.1.0", "1.2.1", 2),
        ("2", "2.1.1", "2.1.2", 2),
    ]


def test_list_books_ordered_numerically_by_position(db):
    """Book 10 comes after book 9, not after book 1."""
    text_id = insert_text(db, "Long", None, "long.txt", "/d/long.txt", 3)
    insert_lines(db, text_id, ["a", "b", "c"], ["1.1", "9.1", "10.1"])
    assert [b.number for b in list_books(db, text_id)] == ["1", "9", "10"]


def test_list_books_without_references(db):
    text_id = insert_text(db, "Plain", None, "plain.txt", "/d/plain.txt", 2)
    insert_lines(db, text_id, ["a", "b"])
    assert list_books(db, text_id) == []


def test_list_books_ignores_other_texts(db_with_text):
    db, text_id = db_with_text
    other = insert_text(db, "Other", None, "other.txt", "/d/other.txt", 1)
    insert_lines(db, other, ["x"], ["7.1"])
    assert [b.number for b in list_books(db, text_id)] == ["1", "2"]


# --- Single-line resolution ---


def test_resolve_line_by_reference(db_with_text):
    db, text_id = db_with_text
    assert resolve_line(db, text_id, "2.1") == 6


def test_resolve_line_by_number(db_with_text):
    db, text_id = db_with_text
    assert resolve_line(db, text_id, "7") == 7


def test_resolve_line_strips_whitespace(db_with_text):
    db, text_id = db_with_text
    assert resolve_line(db, text_id, " 1.3 ") == 3


def test_resolve_line_not_found(db_with_text):
    db, text_id = db_with_text
    assert resolve_line(db, text_id, "9.9") is None
    assert resolve_line(db, text_id, "999") is None
    assert resolve_line(db, text_id, "abc") is None


# --- Verb parts ---

PARTS = [{"part": 1, "name": "Present Active", "form": "λύω", "notes": ""}]


def test_save_and_get_verb_parts(db):
    save_verb_parts(db, "λύω", "m1", PARTS)
    saved = get_verb_parts(db, "λύω", "m1")
    assert saved.verb == "λύω" and saved.model_used == "m1"
    assert saved.parts == PARTS
    assert get_verb_parts(db, "λύω", "m2") is None


def test_verb_parts_key_is_normalized(db):
    decomposed = unicodedata.normalize("NFD", "  Λύω ")
    save_verb_parts(db, decomposed, "m1", PARTS)
    assert get_verb_parts(db, "λύω", "m1").verb == "λύω"


def test_save_verb_parts_replaces_same_model(db):
    first = save_verb_parts(db, "λύω", "m1", PARTS)
    again = save_verb_parts(db, "λύω", "m1", [{"part": 1, "form": "λύω2"}])
    assert first == again
    [saved] = list_verb_parts(db, "λύω")
    assert saved.parts == [{"part": 1, "form": "λύω2"}]


def test_list_verb_parts_newest_first_across_models(db):
    save_verb_parts(db, "λύω", "old", PARTS)
    save_verb_parts(db, "λύω", "new", PARTS)
    db.execute(
        "UPDATE verb_parts SET looked_up_at='2026-01-01 00:00:00' WHERE model_used='old'"
    )
    db.commit()
    assert [v.model_used for v in list_verb_parts(db, "λύω")] == ["new", "old"]


def test_verb_parts_unreadable_json_gives_no_parts(db):
    db.execute(
        "INSERT INTO verb_parts(verb, model_used, parts_json) VALUES ('λύω','m','oops')"
    )
    db.commit()
    assert get_verb_parts(db, "λύω", "m").parts == []
