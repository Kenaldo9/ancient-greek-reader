"""`greek study coverage` and `greek study remarks` (reports on saved study data),
`study_line_glosses`, and `greek read --interlinear` reading the study glosses."""

import re

import pytest
from click.testing import CliRunner

from greek.api.study import MODEL, NOTE_VERSION
from greek.cli.read_cmd import read_cmd
from greek.db.repos.lines import get_lines
from greek.db.repos.parses import lemmas_with_pos, verb_lemmas
from greek.db.repos.study import (
    save_forms_result,
    save_parts_result,
    save_submitted_batch,
    save_uses_result,
    study_line_glosses,
)
from greek.models import LemmaForms, StudyBatch, WordUse
from tests.integration import test_notes_batch as notes_batch
from tests.integration import test_study_batch as study_batch
from tests.integration.test_uses_batch import MARKED
from tests.integration.test_word_details import _import_excerpt

# The treebank excerpt (Iliad 1.1–1.5 and 2.1–2.2, lines 1–7) with its nodes:
# units 1.1–1.5 (24 words, five to label once studied) and 2.1–2.2 (7 words).
no_api = study_batch.no_api
run_cli = notes_batch.run_cli
_save_study = notes_batch._save_study


@pytest.fixture
def iliad(db):
    return _import_excerpt(db)


@pytest.fixture
def complete(iliad):
    """The excerpt with everything a batch can save: study data, labels, forms, parts."""
    db, text_id = iliad
    _save_study(db, text_id)
    db.execute("UPDATE word_study SET note_version=?", (NOTE_VERSION,))
    save_uses_result(
        db,
        text_id,
        MODEL,
        1,
        5,
        [
            WordUse(text_id, line, token, MODEL, f"use of {form}", "high")
            for _, form, line, token in MARKED
        ],
    )
    save_forms_result(
        db,
        [
            LemmaForms(lemma, MODEL, pos, lemma)
            for lemma, pos in lemmas_with_pos(db, text_id, [(1, 7)])
        ],
    )
    save_parts_result(db, MODEL, {verb: [] for verb in verb_lemmas(db, text_id)})
    return db, text_id


def _coverage(run_cli, text_id):
    """The report's text on one line, the tables' borders left out."""
    result = run_cli("coverage", text_id)
    assert result.exit_code == 0, result.output
    return " ".join(re.sub(r"[\u2500-\u257f]", " ", result.output).split())


# --- greek study coverage -------------------------------------------------------


def test_coverage_of_a_text_without_study_data_lists_what_is_missing(
    iliad, run_cli, no_api
):
    _, text_id = iliad
    text = _coverage(run_cli, text_id)
    assert "1 0 / 24 0 / 5 0 / 1" in text  # book 1: words, lines, units
    assert "2 0 / 7 0 / 2 0 / 1" in text
    assert "Units without all their results (2): 1.1–1.5, 2.1–2.2" in text
    assert "Lemmas without a dictionary form or construction" in text
    assert "Verbs without principal parts" in text
    assert "Nothing missing" not in text


def test_coverage_of_a_complete_text(complete, run_cli, no_api):
    _, text_id = complete
    text = _coverage(run_cli, text_id)
    # book 1: 24 words glossed, 5 literal lines, 1 unit, 5 of 5 labelled, 1 remark
    assert "1 24 / 24 5 / 5 1 / 1 5 / 5 1" in text
    assert "All 31 / 31 7 / 7 2 / 2 5 / 5 1" in text
    assert "Nothing missing." in text
    assert "not yet collected" not in text


def test_coverage_counts_a_missing_label_and_a_waiting_batch(complete, run_cli, db):
    _, text_id = complete
    db.execute("DELETE FROM word_uses WHERE line_number=2 AND token_index=3")
    save_submitted_batch(
        db,
        StudyBatch("msgbatch_x", text_id, "Iliad 2.1–2.2", MODEL, "in_progress", 0),
        [],
    )
    text = _coverage(run_cli, text_id)
    assert "1 24 / 24 5 / 5 1 / 1 4 / 5 1" in text
    assert "Words to label without a label (1): 1.2 Ἀχαιοῖς" in text
    assert "1 batches for this text are not yet collected" in text


def test_coverage_reports_notes_older_than_the_current_version(complete, run_cli, db):
    _, text_id = complete
    db.execute("UPDATE word_study SET note_version=1 WHERE line_number=1")
    text = _coverage(run_cli, text_id)
    assert f"Words with notes older than version {NOTE_VERSION}: 5" in text
    assert "Nothing missing" not in text


def test_coverage_names_the_unit_without_a_literal_line(complete, run_cli, db):
    _, text_id = complete
    db.execute("DELETE FROM line_translations WHERE line_number=7")
    text = _coverage(run_cli, text_id)
    assert "2 7 / 7 1 / 2 0 / 1" in text
    assert "Units without all their results (1): 2.1–2.2" in text


# --- greek study remarks --------------------------------------------------------


def test_remarks_report_lists_each_remark_with_the_treebank_parse(
    complete, run_cli, no_api, tmp_path
):
    db, text_id = complete
    db.execute(
        "UPDATE word_study SET parse_check='genitive | not dative,\nsee 1.2' "
        "WHERE line_number=6 AND token_index=0"
    )
    out = tmp_path / "remarks.md"
    result = run_cli("remarks", text_id, "--out", out)
    assert result.exit_code == 0, result.output
    assert "2 parse_check remarks in Iliad 1.1–2.2" in result.text
    report = out.read_text()
    assert report.startswith("# parse_check remarks: Iliad 1.1–2.2\n")
    assert "| ἀείδω | 1 |" in report  # the lemmas remarked on most often
    assert "## Book 1" in report and "## Book 2" in report
    assert (
        "| 1.1 | ἄειδε | ἀείδω, verb 2nd singular present imperative active "
        "| imperative |"
    ) in report
    # a pipe in a remark is escaped and a line break joined
    assert "| genitive \\| not dative, see 1.2 |" in report


def test_remarks_report_of_some_books_goes_to_the_data_directory(
    complete, run_cli, tmp_path
):
    _, text_id = complete
    result = run_cli("remarks", text_id, "--books", "2")
    assert result.exit_code == 0, result.output
    report = (tmp_path / f"remarks-{text_id}.md").read_text()
    assert "0 remarks" in report and "## Book" not in report
    assert run_cli("remarks", text_id, "--books", "3").exit_code == 1


# --- Study glosses for the interlinear ---------------------------------------


def test_study_line_glosses_only_for_lines_with_every_word_glossed(complete):
    db, text_id = complete
    contents = {ln.line_number: ln.content for ln in get_lines(db, text_id)}
    glosses = study_line_glosses(db, text_id, contents, 1, 7, MODEL)
    assert glosses[1] == [f"gloss 1.1 {i}" for i in range(5)]
    assert sorted(glosses) == [1, 2, 3, 4, 5, 6, 7]
    db.execute("DELETE FROM word_study WHERE line_number=2 AND token_index=0")
    assert 2 not in study_line_glosses(db, text_id, contents, 1, 7, MODEL)
    assert study_line_glosses(db, text_id, contents, 1, 7, "another-model") == {}


@pytest.fixture
def read(monkeypatch, db):
    """Run `greek read` on the test database."""
    monkeypatch.setattr("greek.cli.read_cmd.get_connection", lambda: db)

    def run(*args):
        result = CliRunner().invoke(read_cmd, [str(a) for a in args])
        assert result.exit_code == 0, result.output
        return " ".join(result.output.split())

    return run


def test_read_interlinear_uses_study_glosses(complete, read):
    _, text_id = complete
    text = read(text_id, "--lines", "1.1-1.2", "--interlinear")
    assert "Glosses from the Claude Opus 5.5 study data." in text
    assert "gloss 1.1 0 gloss 1.1 1" in text


def test_read_interlinear_marks_lines_without_study_glosses(complete, read, db):
    _, text_id = complete
    db.execute("DELETE FROM word_study WHERE line_number IN (6, 7)")
    text = read(text_id, "--lines", "1.5-2.2", "--interlinear")
    assert "study data for 1 of 3 lines" in text
    assert "gloss 1.5 0" in text
    assert "Ἄλλοι" in text and "· ·" in text
