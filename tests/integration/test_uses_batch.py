"""`greek study uses` and uses batches in `greek study batch collect`, with a
mocked Message Batches client; migration 017 and its repository functions."""

import json
import sqlite3

import pytest

from greek.api.study import MODEL
from greek.db.connection import run_migrations
from greek.db.repos.study import (
    get_study_batch,
    get_word_use,
    labelled_words,
    list_study_requests,
    list_word_uses,
    measured_output_per_word,
    save_uses_result,
)
from greek.models import WordUse
from tests.integration import test_notes_batch as notes_batch
from tests.integration import test_study_batch as study_batch
from tests.integration.test_word_details import _import_excerpt

# The treebank excerpt with its nodes. Unit 1.1–1.5 (lines 1–5, 24 words) has
# five words to label once its study data is saved (Ἄϊδι then has the model's
# dative parse); unit 2.1–2.2 (lines 6–7) has none.
batches = study_batch.batches
no_api = study_batch.no_api
_errored = study_batch._errored
_succeeded = study_batch._succeeded
_save_study = notes_batch._save_study
run_cli = notes_batch.run_cli

MARKED = [
    (2, "ἄειδε", 1, 1),
    (4, "Πηληϊάδεω", 1, 3),
    (5, "Ἀχιλῆος", 1, 4),
    (9, "Ἀχαιοῖς", 2, 3),
    (16, "Ἄϊδι", 3, 4),
]  # (number in the unit, word, line_number, token_index)


@pytest.fixture
def iliad(db):
    return _import_excerpt(db)


@pytest.fixture
def studied(iliad):
    db, text_id = iliad
    _save_study(db, text_id)
    return db, text_id


def _uses(marked=MARKED, extra=(), skip=(), words=None):
    """A uses result for unit 1.1–1.5, as JSON."""
    items = [
        {"n": n, "word": (words or {}).get(n, form), "use": f"use of {form}"}
        for n, form, _, _ in marked
        if n not in skip
    ]
    items += [{"n": n, "word": form, "use": label} for n, form, label in extra]
    return json.dumps({"uses": items}, ensure_ascii=False)


def _submit(run_cli, text_id, *args):
    result = run_cli("uses", "submit", text_id, *args, "--yes")
    assert result.exit_code == 0, result.output
    return result


def _end(batches, text_id, text, batch_id="msgbatch_01"):
    batches.end(batch_id, [_succeeded(f"{text_id}_1-5", text)])


# --- Migration 017 and the repository -------------------------------------------


def test_migration_017_word_uses():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    run_migrations(conn)
    conn.execute(
        """INSERT INTO texts(id, title, author, filename, filepath, line_count)
           VALUES (12, 'Iliad', 'Homer', 'iliad.txt', '/data/iliad.txt', 1)"""
    )
    insert = """INSERT INTO word_uses(text_id, line_number, token_index, model_used,
                                      use_label, effort, extra)
                VALUES (12, 1, 0, ?, 'dative of disadvantage', 'high', ?)"""
    conn.execute(insert, (MODEL, 0))
    with pytest.raises(sqlite3.IntegrityError):  # one label per word and model
        conn.execute(insert, (MODEL, 0))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("other", 2))
    conn.execute(insert, ("other", 1))
    conn.execute("DELETE FROM texts")
    assert conn.execute("SELECT count(*) FROM word_uses").fetchone()[0] == 0


def test_saving_a_units_labels_replaces_the_models_earlier_ones(iliad):
    db, text_id = iliad
    old = [
        WordUse(text_id, 1, 1, MODEL, "old", "medium"),
        WordUse(text_id, 3, 4, MODEL, "old extra", "medium", extra=True),
        WordUse(text_id, 6, 0, MODEL, "another unit", "medium"),
        WordUse(text_id, 1, 3, "other-model", "other", "low"),
    ]
    save_uses_result(db, text_id, MODEL, 1, 7, old[:3])
    save_uses_result(db, text_id, "other-model", 1, 5, old[3:])
    save_uses_result(
        db, text_id, MODEL, 1, 5, [WordUse(text_id, 1, 1, MODEL, "new", "high")]
    )
    assert [
        (u.line_number, u.use_label, u.effort)
        for u in list_word_uses(db, text_id, 1, 7, MODEL)
    ] == [(1, "new", "high"), (6, "another unit", "medium")]
    assert labelled_words(db, text_id, MODEL) == {(1, 1), (6, 0)}
    # the reader takes the study model's label, else another model's
    assert get_word_use(db, text_id, 1, 1, MODEL).use_label == "new"
    assert get_word_use(db, text_id, 1, 3, MODEL).model_used == "other-model"
    assert get_word_use(db, text_id, 1, 0, MODEL) is None


# --- Estimate and submit ----------------------------------------------------------


def test_estimate_is_local(studied, no_api, run_cli):
    _, text_id = studied
    result = run_cli("uses", "estimate", text_id, "--show")
    assert result.exit_code == 0, result.output
    assert "Uses batch: Iliad 1.1–2.2: uses, effort medium" in result.text
    assert "Requests (translation units) │ 1" in result.text
    assert "Words │ 24" in result.text and "Words to label │ 5" in result.text
    assert "genitive │ 2" in result.text and "dative │ 2" in result.text
    assert "imperative │ 1" in result.text
    assert "Output tokens (estimate, 65 per label) │ 325" in result.text
    assert "Left out: 1 units with no word to label." in result.text
    # --show prints the user message
    assert 'Give "uses" 5 objects, one per marked word, in order.' in result.text
    assert "Translation: Fluent 1-5." in result.text
    assert (
        "16 Ἄϊδι: Ἀΐδης, noun singular masculine dative | gloss: gloss 1.3 4 | "
        "role: dative object of προΐαψεν → use"
    ) in result.text

    result = run_cli("uses", "estimate", text_id, "--effort", "high")
    assert "Output tokens (estimate, 100 per label) │ 500" in result.text


def test_units_need_saved_study_data(iliad, no_api, run_cli):
    _, text_id = iliad
    result = run_cli("uses", "estimate", text_id)
    assert result.exit_code == 0, result.output
    assert "Nothing to request" in result.text
    assert "Left out: 1 units without saved study data" in result.text


def test_submit_needs_yes(studied, no_api, run_cli, db):
    _, text_id = studied
    result = run_cli("uses", "submit", text_id, "--books", "1")
    assert "Nothing submitted" in result.text
    assert db.execute("SELECT count(*) FROM study_batches").fetchone()[0] == 0


def test_submit_records_the_words_to_label(studied, batches, run_cli, db):
    _, text_id = studied
    result = _submit(
        run_cli, text_id, "--lines", "1.2-1.3", "--effort", "high", "--pilot"
    )
    assert "Submitted batch msgbatch_01: 1 requests" in result.text
    batch = get_study_batch(db, "msgbatch_01")
    assert (batch.kind, batch.effort, batch.pilot) == ("uses", "high", True)
    assert batch.scope == "Iliad 1.1–1.5: uses, effort high, pilot"
    [request] = list_study_requests(db, "msgbatch_01")
    assert (request.custom_id, request.word_count) == (f"{text_id}_1-5", 5)
    [sent] = batches.created[0]
    assert sent["params"]["output_config"]["effort"] == "high"
    assert sent["params"]["messages"][0]["content"].count("→ use") == 5


def test_waiting_units_are_left_out(studied, batches, run_cli):
    _, text_id = studied
    _submit(run_cli, text_id, "--pilot")
    # a pilot holds back only pilots at its effort
    assert "waiting" in run_cli("uses", "estimate", text_id, "--pilot").text
    result = run_cli("uses", "estimate", text_id, "--pilot", "--effort", "high")
    assert "Words to label │ 5" in result.text
    assert "Words to label │ 5" in run_cli("uses", "estimate", text_id).text
    _submit(run_cli, text_id)
    result = run_cli("uses", "estimate", text_id)
    assert "Nothing to request" in result.text
    result = run_cli("uses", "estimate", text_id, "--redo")
    assert "Words to label │ 5" in result.text


# --- Collect ------------------------------------------------------------------------


def test_collect_stores_the_labels_with_their_effort(
    studied, batches, run_cli, db, tmp_path
):
    _, text_id = studied
    _submit(run_cli, text_id, "--effort", "high")
    _end(batches, text_id, _uses(extra=[(15, "ψυχὰς", "accusative of respect?")]))
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Requests stored │ 1" in result.text
    assert "Marked words labelled │ 5" in result.text
    assert "Extra labels (unmarked words) │ 1" in result.text
    assert "Per label: 416.7 output tokens" in result.text  # 2,500 / 6

    uses = list_word_uses(db, text_id, 1, 7, MODEL)
    assert [(u.line_number, u.token_index, u.extra) for u in uses] == [
        (line, token, False) for _, _, line, token in MARKED[:4]
    ] + [(3, 3, True), (3, 4, False)]
    assert {u.effort for u in uses} == {"high"}
    assert uses[0].use_label == "use of ἄειδε"
    [request] = list_study_requests(db, "msgbatch_01")
    assert request.status == "succeeded" and request.output_tokens == 2500

    review = json.loads((tmp_path / "uses-msgbatch_01.json").read_text())
    assert (review["effort"], review["pilot"], review["stored"]) == (
        "high",
        False,
        True,
    )
    [unit] = review["units"]
    assert unit["lines"] == ["1.1", "1.2", "1.3", "1.4", "1.5"]
    first = unit["uses"][0]
    assert first == {
        "n": 2,
        "ref": "1.1",
        "word": "ἄειδε",
        "lemma": "ἀείδω",
        "parse": "verb 2nd singular present imperative active",
        "gloss": "gloss 1.1 1",
        "role": "main verb",
        "kind": "imperative",
        "extra": False,
        "use": "use of ἄειδε",
    }
    assert unit["uses"][-2]["extra"] and unit["uses"][-2]["kind"] is None

    # the next estimate uses the measured output per label at this effort
    assert measured_output_per_word(db, MODEL, "uses", "high") == pytest.approx(500)
    result = run_cli("uses", "estimate", text_id, "--redo", "--effort", "high")
    assert "(estimate, 500 per label)" in result.text
    assert "Nothing to request" in run_cli("uses", "estimate", text_id).text


def test_a_pilot_stores_nothing_until_store(studied, batches, run_cli, db, tmp_path):
    _, text_id = studied
    _submit(run_cli, text_id, "--pilot")
    _end(batches, text_id, _uses())
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Requests checked (pilot) │ 1" in result.text
    assert "(a pilot: none were stored)" in result.text
    assert list_word_uses(db, text_id, 1, 7, MODEL) == []
    review = json.loads((tmp_path / "uses-msgbatch_01.json").read_text())
    assert review["stored"] is False and len(review["units"][0]["uses"]) == 5

    result = run_cli("batch", "collect", "msgbatch_01", "--store")
    assert result.exit_code == 0, result.output
    assert "Requests stored │ 1" in result.text
    uses = list_word_uses(db, text_id, 1, 7, MODEL)
    assert len(uses) == 5 and {u.effort for u in uses} == {"medium"}
    batch = get_study_batch(db, "msgbatch_01")
    assert batch.output_tokens == 2500  # collected twice, counted once


def test_store_is_only_for_a_uses_batch(studied, batches, run_cli):
    _, text_id = studied
    notes_batch._submit(run_cli, text_id)
    result = run_cli("batch", "collect", "msgbatch_01", "--store")
    assert result.exit_code == 1
    assert "--store is for a pilot uses batch; msgbatch_01 is a notes batch" in (
        result.text
    )


@pytest.mark.parametrize(
    "text, failure",
    [
        (_uses(skip={9}), "no use for marked words 9 Ἀχαιοῖς (1.2)"),
        (_uses(words={4: "Ἀχιλῆος"}), "use 4 is for Ἀχιλῆος, not Πηληϊάδεω (1.1)"),
        (_uses(extra=[(30, "x", "y")]), "a use is for word 30, which isn't numbered"),
    ],
)
def test_a_result_that_does_not_line_up_fails_and_can_be_retried(
    studied, batches, run_cli, db, tmp_path, text, failure
):
    _, text_id = studied
    _submit(run_cli, text_id, "--pilot", "--effort", "high")
    _end(batches, text_id, text)
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Requests failed │ 1" in result.text
    assert f"{failure} │ 1" in result.text
    assert list_word_uses(db, text_id, 1, 7, MODEL) == []
    [request] = list_study_requests(db, "msgbatch_01")
    assert request.status == "failed" and request.output_tokens == 2500
    path = tmp_path / "study-failed-msgbatch_01.txt"
    assert path.read_text() == f"{text_id}_1-5\n"
    assert (
        f"greek study uses submit {text_id} --retry {path} --effort high --pilot"
        in result.text
    )
    result = _submit(run_cli, text_id, "--retry", path, "--effort", "high", "--pilot")
    assert "Iliad 1.1–1.5 (retry of 1): uses, effort high, pilot" in result.text


def test_words_to_label_that_changed_since_submitting_fail(
    studied, batches, run_cli, db
):
    _, text_id = studied
    _submit(run_cli, text_id)
    db.execute(
        "UPDATE word_parses SET postag='a-p---mn-' WHERE text_id=? AND lemma='Ἀχαιός'",
        (text_id,),
    )
    _end(batches, text_id, _uses())
    result = run_cli("batch", "collect", "msgbatch_01")
    assert "the words to label changed since it was submitted │ 1" in result.text


def test_errors_and_missing_results_fail(studied, batches, run_cli, db):
    _, text_id = studied
    db.execute(
        "UPDATE word_parses SET postag='v3paoa---' WHERE text_id=? AND lemma='εὕδω'",
        (text_id,),
    )  # an optative: unit 2.1–2.2 now has a word to label
    result = _submit(run_cli, text_id)
    assert "2 requests" in result.text
    batches.end("msgbatch_01", [_errored(f"{text_id}_1-5")])
    result = run_cli("batch", "collect", "msgbatch_01")
    assert "error: api_error: Internal server error │ 1" in result.text
    assert "no result returned │ 1" in result.text
    statuses = {r.custom_id: r.status for r in list_study_requests(db, "msgbatch_01")}
    assert set(statuses.values()) == {"failed"}
