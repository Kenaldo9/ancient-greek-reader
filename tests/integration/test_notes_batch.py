"""`greek study notes` and notes batches in `greek study batch status | collect`,
with a mocked Message Batches client; migration 015 and its repository functions."""

import json
import sqlite3

import pytest
from click.testing import CliRunner

from greek.api.study import MODEL, NOTES_SYSTEM_PROMPT, study_words
from greek.cli.study_cmd import study_cmd
from greek.db.connection import _MIGRATIONS_DIR, run_migrations
from greek.db.repos.lines import get_lines
from greek.db.repos.parses import list_word_parses
from greek.db.repos.study import (
    get_study_batch,
    list_line_translations,
    list_study_requests,
    list_translation_units,
    list_word_study,
    measured_output_per_word,
    pending_ranges,
    save_study_result,
)
from greek.models import LineTranslation, TranslationUnit, WordStudy
from greek.search import plain
from tests.integration import test_study_batch as study_batch

# The study batch tests' text with treebank data and fake Message Batches client:
# units 1.1–1.5 (lines 1–5, 24 words) and 2.1–2.2 (lines 6–7, 7 words).
iliad = study_batch.iliad
batches = study_batch.batches
no_api = study_batch.no_api
_errored = study_batch._errored
_succeeded = study_batch._succeeded

UNITS = [(1, 5), (6, 7)]
HADES = "n-s---md-"  # the model's parse of the words the treebank lacks


@pytest.fixture
def run_cli(db, monkeypatch, tmp_path):
    monkeypatch.setattr("greek.cli.study_cmd.get_connection", lambda: db)
    monkeypatch.setenv("GREEK_DATA_DIR", str(tmp_path))

    def run(*args):
        result = CliRunner().invoke(study_cmd, [str(a) for a in args])
        # Rich wraps long lines at the terminal width
        result.text = " ".join(result.output.split())
        return result

    return run


def _save_study(db, text_id, units=UNITS):
    """Saved study data for the units, as a collected study batch leaves it."""
    for first, last in units:
        lines = get_lines(db, text_id, first, last)
        words = study_words(lines, list_word_parses(db, text_id, first, last))
        save_study_result(
            db,
            TranslationUnit(text_id, first, last, f"Fluent {first}-{last}.", MODEL),
            [
                LineTranslation(
                    text_id, line.line_number, f"literal {line.reference}", MODEL
                )
                for line in lines
            ],
            [
                WordStudy(
                    text_id,
                    w.line_number,
                    w.token_index,
                    f"gloss {w.reference} {w.token_index}",
                    f"old note on {w.form}",
                    "imperative" if w.form == "ἄειδε" else None,
                    MODEL,
                    1,
                )
                for w in words
            ],
            [
                (w.line_number, w.token_index, "Ἀΐδης", HADES)
                for w in words
                if w.needs_parse
            ],
        )


@pytest.fixture
def studied(iliad):
    db, text_id = iliad
    _save_study(db, text_id)
    return db, text_id


def _words(db, text_id, first, last):
    return study_words(
        get_lines(db, text_id, first, last), list_word_parses(db, text_id, first, last)
    )


def _notes(db, text_id, first, last, forms=None):
    """A notes result for lines first–last, as JSON (forms: the words it repeats)."""
    if forms is None:
        forms = [w.form for w in _words(db, text_id, first, last)]
    return json.dumps(
        {"notes": [{"word": f, "note": f"New note on {f}, longer."} for f in forms]},
        ensure_ascii=False,
    )


def _submit(run_cli, text_id, *args):
    result = run_cli("notes", "submit", text_id, *args, "--yes")
    assert result.exit_code == 0, result.output
    return result


def _end(batches, db, text_id, batch_id="msgbatch_01"):
    batches.end(
        batch_id,
        [
            _succeeded(f"{text_id}_{a}-{b}", _notes(db, text_id, a, b))
            for a, b in reversed(UNITS)  # results come in any order
        ],
    )


def _notes_now(db, text_id):
    return [(w.note, w.note_version) for w in list_word_study(db, text_id, 1, 7, MODEL)]


# --- Migration 015 and the repository -------------------------------------------


def test_migration_015_keeps_batches_requests_and_notes():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("greek_plain", 1, plain, deterministic=True)
    conn.execute("CREATE TABLE _migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if path.name >= "015":
            break
        conn.executescript(path.read_text())
        conn.execute("INSERT INTO _migrations(name) VALUES (?)", (path.name,))
    conn.executescript(
        """
        INSERT INTO texts(id, title, author, filename, filepath, line_count)
        VALUES (12, 'Iliad', 'Homer', 'iliad.txt', '/data/iliad.txt', 1);
        INSERT INTO word_study(text_id, line_number, token_index, gloss, note,
                               model_used)
        VALUES (12, 1, 0, 'wrath', 'First word of the poem.', 'claude-opus-5-5');
        INSERT INTO study_batches(id, text_id, scope, model, status, request_count,
                                  cost_usd, collected_at)
        VALUES ('msgbatch_study', 12, 'Iliad 1.1–1.611', 'claude-opus-5-5',
                'ended', 1, 3.08, '2026-10-04 09:30:00');
        INSERT INTO study_requests(batch_id, custom_id, first_line, last_line,
                                   word_count, status, output_tokens)
        VALUES ('msgbatch_study', '12_1-7', 1, 7, 37, 'succeeded', 2000);
        INSERT INTO study_batches(id, text_id, scope, model, status, request_count,
                                  kind)
        VALUES ('msgbatch_parts', 12, 'parts', 'claude-opus-5-5', 'ended', 1,
                'parts');
        INSERT INTO parts_requests(batch_id, custom_id, verbs, verb_count)
        VALUES ('msgbatch_parts', '12_parts_1', '["ἀείδω"]', 1);
        """
    )
    conn.commit()
    run_migrations(conn)

    rows = conn.execute(
        """SELECT id, kind, effort, pilot, cost_usd, collected_at FROM study_batches
           ORDER BY id"""
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("msgbatch_parts", "parts", "medium", 0, 0.0, None),
        ("msgbatch_study", "study", "medium", 0, 3.08, "2026-10-04 09:30:00"),
    ]
    assert conn.execute("SELECT count(*) FROM study_requests").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM parts_requests").fetchone()[0] == 1
    assert tuple(
        conn.execute("SELECT note, note_version FROM word_study").fetchone()
    ) == ("First word of the poem.", 1)

    # notes are a kind now, other kinds still aren't
    conn.execute("UPDATE study_batches SET kind='notes' WHERE id='msgbatch_study'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE study_batches SET kind='other'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE study_batches SET pilot=2")

    # the foreign keys are on again and point at the new table
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO study_requests(batch_id, custom_id, first_line, last_line,
                                          word_count)
               VALUES ('msgbatch_none', '12_1-7', 1, 7, 37)"""
        )
    conn.execute("DELETE FROM study_batches")
    assert conn.execute("SELECT count(*) FROM study_requests").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM parts_requests").fetchone()[0] == 0
    # the text's deletion still leaves a batch's record
    conn.execute(
        """INSERT INTO study_batches(id, text_id, scope, model, status, request_count)
           VALUES ('b', 12, 's', 'm', 'ended', 1)"""
    )
    conn.execute("DELETE FROM texts")
    assert conn.execute("SELECT text_id FROM study_batches").fetchone()[0] is None


def test_waiting_ranges_and_measured_output_are_per_kind(studied, batches, run_cli):
    db, text_id = studied
    _submit(run_cli, text_id)
    assert pending_ranges(db, text_id, MODEL, kind="notes") == UNITS
    assert pending_ranges(db, text_id, MODEL) == []  # no study batch is waiting
    _end(batches, db, text_id)
    assert run_cli("batch", "collect", "msgbatch_01").exit_code == 0
    assert measured_output_per_word(db, MODEL, "notes") == pytest.approx(5000 / 31)
    assert measured_output_per_word(db, MODEL, "notes", "medium") == pytest.approx(
        5000 / 31
    )
    assert measured_output_per_word(db, MODEL, "notes", "high") is None
    assert measured_output_per_word(db, MODEL) is None  # study requests only


# --- Estimate and submit ----------------------------------------------------------


def test_notes_need_saved_study_data(iliad, no_api, run_cli):
    db, text_id = iliad
    result = run_cli("notes", "estimate", text_id)
    assert result.exit_code == 0, result.output
    assert "Nothing to request" in result.text
    assert "Left out: 2 units without saved study data" in result.text

    _save_study(db, text_id, [(6, 7)])
    result = run_cli("notes", "estimate", text_id)
    assert "Requests (translation units) │ 1" in result.text
    assert "Left out: 1 units without saved study data" in result.text


def test_estimate_is_local(studied, no_api, run_cli):
    db, text_id = studied
    result = run_cli("notes", "estimate", text_id)
    assert result.exit_code == 0, result.output
    assert "Notes batch: Iliad 1.1–2.2: notes, effort medium (ID 1)" in result.text
    assert "Requests (translation units) │ 2" in result.text
    assert "Lines │ 7" in result.text
    assert "Words │ 31" in result.text
    assert "Output tokens (estimate, 75 per word) │ 2,325 │ $0.02" in result.text
    assert "Model claude-opus-5-5, effort medium" in result.text
    assert "is the plan's estimate" in result.text
    assert "Left out" not in result.text
    high = run_cli("notes", "estimate", text_id, "--effort", "high")
    assert "notes, effort high (ID 1)" in high.text
    assert "Model claude-opus-5-5, effort high" in high.text
    assert db.execute("SELECT count(*) FROM study_batches").fetchone()[0] == 0


def test_submit_without_yes_submits_nothing(studied, no_api, run_cli):
    db, text_id = studied
    result = run_cli("notes", "submit", text_id, "--pilot")
    assert result.exit_code == 0, result.output
    assert "Nothing submitted." in result.text
    assert "rerun with --yes" in result.text
    assert db.execute("SELECT count(*) FROM study_batches").fetchone()[0] == 0


def test_submit_sends_lines_translation_glosses_and_marks(studied, batches, run_cli):
    db, text_id = studied
    result = _submit(run_cli, text_id, "--effort", "high")
    assert (
        "Submitted batch msgbatch_01: 2 requests (Iliad 1.1–2.2: notes, effort high)"
        in result.text
    )
    [requests] = batches.created
    assert [r["custom_id"] for r in requests] == [f"{text_id}_1-5", f"{text_id}_6-7"]
    params = requests[0]["params"]
    assert params["output_config"]["effort"] == "high"
    assert params["system"][0]["text"] == NOTES_SYSTEM_PROMPT
    content = params["messages"][0]["content"]
    assert content.startswith(
        'Iliad 1.1–1.5: 5 lines, 24 words. Give "notes" 24 objects.'
    )
    assert "Translation:\nFluent 1-5.\n" in content
    words = dict(
        line.split(" ", 1)
        for line in content.split("Words:\n")[1].splitlines()
        if not line.startswith("[")
    )
    assert words["1"] == (
        "Μῆνιν: μῆνις, noun singular feminine accusative | gloss: gloss 1.1 0 "
        "| first in this book"
    )
    assert words["2"].endswith("| gloss: gloss 1.1 1 | first in this book")
    # the model's parse counts: Ἀΐδης is new at 1.3, not at 1.4
    assert words["16"] == (
        "Ἄϊδι: Ἀΐδης, noun singular masculine dative | gloss: gloss 1.3 4 "
        "| first in this book"
    )
    assert (
        words["18"]
        == "ἔρδον: Ἀΐδης, noun singular masculine dative | gloss: gloss 1.4 0"
    )
    # οὐδʼ brings in οὐ (δέ came at 1.3)
    assert words["22"].startswith("οὐδʼ: οὐ, ") and words["22"].endswith(
        "| gloss: gloss 1.5 0 | first in this book"
    )
    # a new book starts afresh
    assert all(
        line.endswith("| first in this book")
        for line in requests[1]["params"]["messages"][0]["content"]
        .split("Words:\n")[1]
        .splitlines()
        if not line.startswith("[")
    )

    batch = get_study_batch(db, "msgbatch_01")
    assert (batch.kind, batch.effort, batch.pilot, batch.request_count) == (
        "notes",
        "high",
        False,
        2,
    )
    assert [
        (r.first_line, r.last_line, r.word_count, r.status)
        for r in list_study_requests(db, "msgbatch_01")
    ] == [(1, 5, 24, "pending"), (6, 7, 7, "pending")]


def test_submit_takes_several_line_ranges(studied, batches, run_cli):
    db, text_id = studied
    result = _submit(
        run_cli, text_id, "--lines", "1.2-1.3", "--lines", "2.2-2.2", "--pilot"
    )
    assert (
        "2 requests (Iliad 1.1–1.5, 2.1–2.2: notes, effort medium, pilot)"
        in result.text
    )
    assert get_study_batch(db, "msgbatch_01").pilot is True


def test_units_waiting_in_a_notes_batch_are_left_out(studied, batches, run_cli):
    db, text_id = studied
    _submit(run_cli, text_id, "--lines", "1.1-1.1")
    result = run_cli("notes", "estimate", text_id)
    assert "Requests (translation units) │ 1" in result.text
    assert "Left out: 0 units whose notes are version 2, 1 waiting" in result.text
    assert (
        "Requests (translation units) │ 2"
        in run_cli("notes", "estimate", text_id, "--redo").text
    )


def test_a_waiting_pilot_holds_back_only_pilots_at_its_effort(
    studied, batches, run_cli
):
    db, text_id = studied
    _submit(run_cli, text_id, "--pilot", "--effort", "medium")
    assert pending_ranges(db, text_id, MODEL, "notes", True, "medium") == UNITS
    assert pending_ranges(db, text_id, MODEL, "notes", True, "high") == []
    assert pending_ranges(db, text_id, MODEL, "notes") == []
    again = run_cli("notes", "estimate", text_id, "--pilot")
    assert "Nothing to request" in again.text
    # the same units at another effort, and for real (a pilot stores nothing)
    high = run_cli("notes", "estimate", text_id, "--pilot", "--effort", "high")
    assert "Requests (translation units) │ 2" in high.text
    real = run_cli("notes", "estimate", text_id)
    assert "Requests (translation units) │ 2" in real.text


@pytest.mark.parametrize(
    "args, message",
    [
        (["--books", "1", "--lines", "1.1"], "Use only one of --books, --lines"),
        (["--books", "3"], "has no book 3"),
        (["--effort", "extreme"], "Invalid value for '--effort'"),
    ],
)
def test_bad_options(studied, no_api, run_cli, args, message):
    _, text_id = studied
    result = run_cli("notes", "estimate", text_id, *args)
    assert result.exit_code != 0
    assert message in result.text


# --- Collect ------------------------------------------------------------------------


def test_collect_replaces_only_the_notes(studied, batches, run_cli, tmp_path):
    db, text_id = studied
    before = {
        "units": list_translation_units(db, text_id, 1, 7, MODEL),
        "literal": list_line_translations(db, text_id, 1, 7, MODEL),
        "parses": list_word_parses(db, text_id),
        "words": list_word_study(db, text_id, 1, 7, MODEL),
    }
    _submit(run_cli, text_id)
    _end(batches, db, text_id)
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Requests stored │ 2" in result.text
    assert "Words │ 31" in result.text
    assert "Requests failed │ 0" in result.text
    assert "Note length, new" in result.text and "Note length, before" in result.text
    assert "$0.06 (effort medium)" in result.text

    words = list_word_study(db, text_id, 1, 7, MODEL)
    assert [(w.note, w.note_version) for w in words][:2] == [
        ("New note on Μῆνιν, longer.", 2),
        ("New note on ἄειδε, longer.", 2),
    ]
    assert {w.note_version for w in words} == {2}
    # nothing else changed
    assert [(w.gloss, w.parse_check) for w in words] == [
        (w.gloss, w.parse_check) for w in before["words"]
    ]
    assert list_translation_units(db, text_id, 1, 7, MODEL) == before["units"]
    assert list_line_translations(db, text_id, 1, 7, MODEL) == before["literal"]
    assert list_word_parses(db, text_id) == before["parses"]

    assert {r.status for r in list_study_requests(db, "msgbatch_01")} == {"succeeded"}
    batch = get_study_batch(db, "msgbatch_01")
    assert batch.output_tokens == 5000 and batch.collected_at is not None

    review = json.loads((tmp_path / "notes-msgbatch_01.json").read_text())
    assert (review["effort"], review["pilot"]) == ("medium", False)
    assert [u["custom_id"] for u in review["units"]] == [
        f"{text_id}_1-5",
        f"{text_id}_6-7",
    ]
    first = review["units"][0]["words"][0]
    assert first == {
        "ref": "1.1",
        "word": "Μῆνιν",
        "lemma": "μῆνις",
        "first_in_book": True,
        "gloss": "gloss 1.1 0",
        "old": "old note on Μῆνιν",
        "old_version": 1,
        "new": "New note on Μῆνιν, longer.",
    }
    assert not list(tmp_path.glob("study-failed-*"))

    # every unit is done now; later estimates use the measured output per word
    assert "Nothing to request" in run_cli("notes", "estimate", text_id).text
    redo = run_cli("notes", "estimate", text_id, "--redo")
    assert "Output tokens (estimate, 161 per word) │ 5,000" in redo.text
    assert "measured over collected batches" in redo.text
    high = run_cli("notes", "estimate", text_id, "--redo", "--effort", "high")
    assert "75 per word" in high.text  # nothing measured at high yet
    status = run_cli("batch", "status")
    assert "msgbatch_01" in status.text and "$0.06" in status.text


def test_a_word_that_does_not_line_up_fails_its_request(
    studied, batches, run_cli, tmp_path
):
    db, text_id = studied
    _submit(run_cli, text_id)
    forms = [w.form for w in _words(db, text_id, 1, 5)]
    skipped = [forms[0], *forms[2:], forms[-1]]  # ἄειδε skipped, the count still right
    batches.end(
        "msgbatch_01",
        [
            _succeeded(f"{text_id}_1-5", _notes(db, text_id, 1, 5, skipped)),
            _succeeded(f"{text_id}_6-7", _notes(db, text_id, 6, 7)),
        ],
    )
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Requests stored │ 1" in result.text
    assert "note 2 is for θεὰ, not ἄειδε (1.1) │ 1" in result.text

    notes = _notes_now(db, text_id)
    assert notes[:24] == [(f"old note on {f}", 1) for f in forms]  # untouched
    assert {version for _, version in notes[24:]} == {2}
    [failed] = [r for r in list_study_requests(db, "msgbatch_01") if r.failure]
    assert failed.custom_id == f"{text_id}_1-5"
    assert failed.output_tokens == 2500  # billed all the same

    path = tmp_path / "study-failed-msgbatch_01.txt"
    assert path.read_text() == f"{text_id}_1-5\n"
    assert (
        f"greek study notes submit {text_id} --retry {path} --effort medium'"
        in result.text
    )
    retry = _submit(run_cli, text_id, "--retry", path)
    assert "1 requests (Iliad 1.1–1.5 (retry of 1): notes, effort medium)" in retry.text
    assert [r["custom_id"] for r in batches.created[1]] == [f"{text_id}_1-5"]


def test_collect_fails_errors_missing_results_and_unsaved_words(
    studied, batches, run_cli, tmp_path
):
    db, text_id = studied
    _submit(run_cli, text_id)
    batches.end(
        "msgbatch_01",
        [
            _errored(f"{text_id}_1-5"),
            _succeeded(f"{text_id}_6-7", _notes(db, text_id, 6, 7)),
        ],
    )
    # the unit's study data is deleted before the batch is collected
    with db:
        db.execute("DELETE FROM word_study WHERE line_number = 7")
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Requests stored │ 0" in result.text
    assert "error: api_error: Internal server error │ 1" in result.text
    assert "2 of 7 words have no saved gloss │ 1" in result.text
    # line 6 was not changed either: a request is stored whole or not at all
    assert {v for _, v in _notes_now(db, text_id)} == {1}

    batches.end("msgbatch_01", [])  # collecting again, with no results
    again = run_cli("batch", "collect", "msgbatch_01")
    assert "no result returned │ 2" in again.text


def test_a_pilot_stores_nothing_and_writes_the_review(
    studied, batches, run_cli, tmp_path
):
    db, text_id = studied
    before = _notes_now(db, text_id)
    _submit(run_cli, text_id, "--pilot", "--effort", "high")
    _end(batches, db, text_id)
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Requests checked (pilot) │ 2" in result.text
    assert "(a pilot: no notes were stored)" in result.text
    assert _notes_now(db, text_id) == before
    assert {r.status for r in list_study_requests(db, "msgbatch_01")} == {"succeeded"}

    review = json.loads((tmp_path / "notes-msgbatch_01.json").read_text())
    assert (review["effort"], review["pilot"]) == ("high", True)
    word = review["units"][1]["words"][1]
    assert (word["word"], word["old"], word["new"]) == (
        "μέν",
        "old note on μέν",
        "New note on μέν, longer.",
    )

    # the units are still to do; a pilot measures its effort's output per word
    medium = run_cli("notes", "estimate", text_id)
    assert "Requests (translation units) │ 2" in medium.text
    assert "75 per word" in medium.text
    high = run_cli("notes", "estimate", text_id, "--effort", "high")
    assert "161 per word" in high.text
