"""`greek study batch` with a mocked Message Batches client, and its repository (greek/db/repos/study.py)."""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from click.testing import CliRunner

import anthropic
from greek.api.study import MODEL, STUDY_SCHEMA, study_words
from greek.cli.study_cmd import study_cmd
from greek.db.repos.lines import get_lines, insert_lines
from greek.db.repos.parses import list_word_parses, save_treebank
from greek.db.repos.study import (
    get_study_batch,
    list_line_translations,
    list_study_requests,
    list_translation_units,
    list_word_study,
    save_study_result,
)
from greek.db.repos.texts import insert_text
from greek.fetch.treebank import align_text, parse_treebank
from greek.models import LineTranslation, TranslationUnit, WordStudy

FIXTURE = Path(__file__).parent.parent / "fixtures" / "iliad_treebank_excerpt.tb.xml"

# The treebank excerpt's sentences cover 1.1–1.3, 1.5 and 2.1–2.2; it omits
# 1.4 and has σὰ for σὺ in 1.5. Units: 1.1–1.5 (24 words) and 2.1–2.2 (7).
APP_LINES = [
    ("1.1", "Μῆνιν ἄειδε θεὰ Πηληϊάδεω Ἀχιλῆος"),
    ("1.2", "οὐλομένην, ἣ μυρίʼ Ἀχαιοῖς ἄλγεʼ ἔθηκε,"),
    ("1.3", "πολλὰς δʼ ἰφθίμους ψυχὰς Ἄϊδι προΐαψεν."),
    ("1.4", "ἔρδον ἀθανάτοισι τεληέσσας ἑκατόμβας"),
    ("1.5", "οὐδʼ ἔθελον σὺ"),
    ("2.1", "Ἄλλοι μέν ῥα θεοί τε"),
    ("2.2", "εὗδον παννύχιοι."),
]
UNPARSED = {(3, 4), (4, 0), (4, 1), (4, 2), (4, 3), (5, 2)}  # (line, token)


@pytest.fixture
def iliad(db):
    text_id = insert_text(db, "Iliad", "Homer", "iliad.txt", "/data/iliad.txt", 7)
    insert_lines(db, text_id, [c for _, c in APP_LINES], [r for r, _ in APP_LINES])
    result = align_text(text_id, get_lines(db, text_id), parse_treebank(FIXTURE))
    save_treebank(db, text_id, result.sentences, result.parses)
    return db, text_id


def _count(db, table, text_id=None):
    if text_id is None:
        return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return db.execute(
        f"SELECT count(*) FROM {table} WHERE text_id=?", (text_id,)
    ).fetchone()[0]


# --- A fake Message Batches client ---------------------------------------------


class FakeBatches:
    """client.messages.batches: create, retrieve and results, recorded."""

    def __init__(self):
        self.created = []  # the requests of each create()
        self.status = {}
        self.results_by_id = {}
        self.fail_create = None

    def create(self, requests):
        if self.fail_create:
            raise self.fail_create
        batch_id = f"msgbatch_{len(self.created) + 1:02d}"
        self.created.append(json.loads(json.dumps(requests)))
        self.status[batch_id] = "in_progress"
        return SimpleNamespace(id=batch_id, processing_status="in_progress")

    def retrieve(self, batch_id):
        status = self.status[batch_id]
        ended = status == "ended"
        n = len(self.created[int(batch_id.split("_")[1]) - 1])
        return SimpleNamespace(
            id=batch_id,
            processing_status=status,
            ended_at=datetime(2026, 10, 3, 12, 30, tzinfo=timezone.utc)
            if ended
            else None,
            request_counts=SimpleNamespace(
                processing=0 if ended else n,
                succeeded=n if ended else 0,
                errored=0,
                canceled=0,
                expired=0,
            ),
        )

    def results(self, batch_id):
        return iter(self.results_by_id[batch_id])

    def end(self, batch_id, entries):
        self.status[batch_id] = "ended"
        self.results_by_id[batch_id] = entries


@pytest.fixture
def batches(monkeypatch):
    fake = FakeBatches()
    client = SimpleNamespace(messages=SimpleNamespace(batches=fake))
    monkeypatch.setattr("greek.cli.study_cmd.batch_client", lambda: client)
    return fake


@pytest.fixture
def no_api(monkeypatch):
    """Fail the test if a command so much as creates an API client."""

    def refuse():
        raise AssertionError("no API client may be created here")

    monkeypatch.setattr("greek.cli.study_cmd.batch_client", refuse)


@pytest.fixture
def run_cli(db, monkeypatch, tmp_path):
    monkeypatch.setattr("greek.cli.study_cmd.get_connection", lambda: db)
    monkeypatch.setenv("GREEK_DATA_DIR", str(tmp_path))

    def run(*args):
        result = CliRunner().invoke(study_cmd, ["batch", *[str(a) for a in args]])
        # Rich wraps long lines at the terminal width
        result.text = " ".join(result.output.split())
        return result

    return run


def _usage(input_tokens=3000, output=2500, read=900):
    return SimpleNamespace(
        input_tokens=input_tokens,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=read,
        output_tokens=output,
        cache_creation=None,
    )


def _answer(db, text_id, first, last, **changes):
    """A valid model output for lines first–last, as JSON."""
    lines = get_lines(db, text_id, first, last)
    words = study_words(lines, list_word_parses(db, text_id, first, last))
    parse = {
        "lemma": "Ἀΐδης",
        "pos": "noun",
        "person": "-",
        "number": "singular",
        "tense": "-",
        "mood": "-",
        "voice": "-",
        "gender": "masculine",
        "case": "dative",
        "degree": "-",
    }
    data = {
        "fluent": f"Fluent {first}-{last}.",
        "literal": [f"literal {line.reference}" for line in lines],
        "words": [
            {
                "gloss": f"gloss {w.reference} {w.token_index}",
                "note": f"note on {w.form}",
                **({"parse": parse} if w.needs_parse else {}),
                **({"parse_check": "imperative, not indicative"} if i == 1 else {}),
            }
            for i, w in enumerate(words)
        ],
    }
    data.update(changes)
    return json.dumps(data, ensure_ascii=False)


def _succeeded(cid, text, stop_reason="end_turn"):
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=_usage(),
    )
    return SimpleNamespace(
        custom_id=cid, result=SimpleNamespace(type="succeeded", message=message)
    )


def _errored(cid):
    error = SimpleNamespace(type="api_error", message="Internal server error")
    return SimpleNamespace(
        custom_id=cid,
        result=SimpleNamespace(
            type="errored", error=SimpleNamespace(type="error", error=error)
        ),
    )


def _submit(run_cli, text_id, *args):
    result = run_cli("submit", text_id, *args, "--yes")
    assert result.exit_code == 0, result.output
    return result


# --- Estimate and submit ------------------------------------------------------


def test_estimate_is_local(iliad, run_cli, no_api, db):
    _, text_id = iliad
    result = run_cli("estimate", text_id)
    assert result.exit_code == 0, result.output
    assert "Requests (translation units) │ 2" in result.text
    assert "Lines │ 7" in result.text
    assert "Words │ 31" in result.text
    assert "Words without a treebank parse │ 6" in result.text
    assert "Output tokens (estimate, 83 per word) │ 2,573 │ $0.03" in result.text
    assert "output per word (thinking included) is the plan's estimate" in result.text
    assert _count(db, "study_batches") == 0


def test_submit_without_yes_submits_nothing(iliad, run_cli, no_api, db):
    _, text_id = iliad
    result = run_cli("submit", text_id)
    assert result.exit_code == 0, result.output
    assert "Total │ │ $" in result.text
    assert "Nothing submitted." in result.text
    assert "rerun with --yes" in result.text
    assert _count(db, "study_batches") == 0


def test_submit_sends_a_request_per_unit_and_records_the_batch(iliad, run_cli, batches):
    db, text_id = iliad
    result = _submit(run_cli, text_id)
    assert (
        "Submitted batch msgbatch_01: 2 requests (Iliad 1.1–2.2: effort medium)"
        in result.text
    )
    [requests] = batches.created
    assert [r["custom_id"] for r in requests] == [f"{text_id}_1-5", f"{text_id}_6-7"]
    params = requests[0]["params"]
    assert params["model"] == MODEL
    assert params["max_tokens"] == 32000
    assert params["output_config"]["effort"] == "medium"
    assert params["output_config"]["format"]["schema"] == STUDY_SCHEMA
    assert params["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    prompt = params["messages"][0]["content"]
    assert prompt.startswith("Iliad 1.1–1.5: 5 lines, 24 words.")
    assert "16 Ἄϊδι: no treebank parse" in prompt
    assert "22 οὐδʼ: οὐ, adverb + δέ, particle" in prompt

    batch = get_study_batch(db, "msgbatch_01")
    assert (batch.text_id, batch.status, batch.request_count, batch.model) == (
        text_id,
        "in_progress",
        2,
        MODEL,
    )
    assert (batch.scope, batch.effort) == ("Iliad 1.1–2.2: effort medium", "medium")
    assert [
        (r.custom_id, r.first_line, r.last_line, r.word_count, r.status)
        for r in list_study_requests(db, "msgbatch_01")
    ] == [
        (f"{text_id}_1-5", 1, 5, 24, "pending"),
        (f"{text_id}_6-7", 6, 7, 7, "pending"),
    ]


def test_submit_selects_books_and_lines(iliad, run_cli, batches):
    _, text_id = iliad
    _submit(run_cli, text_id, "--books", "2")
    _submit(run_cli, text_id, "--lines", "1.2-1.3")
    assert [[r["custom_id"] for r in b] for b in batches.created] == [
        [f"{text_id}_6-7"],
        [f"{text_id}_1-5"],
    ]
    assert run_cli("estimate", text_id, "--books", "3").exit_code == 1
    assert run_cli("estimate", text_id, "--books", "x").exit_code == 1
    both = run_cli("estimate", text_id, "--books", "1", "--lines", "1.1-1.2")
    assert both.exit_code == 1 and "only one of" in both.text


def test_submit_at_another_effort(iliad, run_cli, batches):
    db, text_id = iliad
    estimate = run_cli("estimate", text_id, "--effort", "high")
    assert estimate.exit_code == 0, estimate.output
    assert "Study batch: Iliad 1.1–2.2: effort high (ID 1)" in estimate.text
    assert "Model claude-opus-5-5, effort high" in estimate.text

    result = _submit(run_cli, text_id, "--effort", "high")
    assert (
        "Submitted batch msgbatch_01: 2 requests (Iliad 1.1–2.2: effort high)"
        in result.text
    )
    [requests] = batches.created
    assert {r["params"]["output_config"]["effort"] for r in requests} == {"high"}
    batch = get_study_batch(db, "msgbatch_01")
    assert (batch.kind, batch.effort) == ("study", "high")

    # a unit waiting at one effort is not requested again at another
    again = run_cli("submit", text_id, "--effort", "medium", "--yes")
    assert "Nothing to request" in again.text
    bad = run_cli("estimate", text_id, "--effort", "extreme")
    assert bad.exit_code == 2 and "Invalid value for '--effort'" in bad.output


def test_submit_skips_units_waiting_in_a_batch(iliad, run_cli, batches):
    _, text_id = iliad
    _submit(run_cli, text_id)
    again = run_cli("submit", text_id, "--yes")
    assert again.exit_code == 0
    assert "Nothing to request" in again.text
    assert len(batches.created) == 1
    redo = _submit(run_cli, text_id, "--redo")
    assert "Submitted batch msgbatch_02: 2 requests" in redo.text


def test_submit_reports_api_errors_and_saves_nothing(iliad, run_cli, batches, db):
    _, text_id = iliad
    batches.fail_create = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages/batches")
    )
    result = run_cli("submit", text_id, "--yes")
    assert result.exit_code == 1
    assert "the batch was not submitted" in result.text
    assert _count(db, "study_batches") == 0


def test_submit_without_an_api_key_stops(iliad, run_cli, db):
    # the real batch_client(); tests/conftest.py blanks ANTHROPIC_API_KEY
    _, text_id = iliad
    result = run_cli("submit", text_id, "--yes")
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY is not set" in result.text
    assert _count(db, "study_batches") == 0


def test_texts_without_treebank_data_are_refused(db, run_cli, no_api):
    text_id = insert_text(db, "Odyssey", "Homer", "od.txt", "/data/od.txt", 1)
    insert_lines(db, text_id, ["ἄνδρα μοι ἔννεπε"], ["1.1"])
    result = run_cli("estimate", text_id)
    assert result.exit_code == 1 and "no treebank data" in result.text
    assert run_cli("estimate", 999).exit_code == 1


# --- Status and collect -------------------------------------------------------


def test_status_lists_batches_and_refreshes_them(iliad, run_cli, batches, db):
    _, text_id = iliad
    assert "No batches submitted yet" in run_cli("status").text
    _submit(run_cli, text_id)
    listed = run_cli("status")
    assert listed.exit_code == 0, listed.output
    assert "msgbatch_01" in listed.text and "in_progress" in listed.text
    batches.end("msgbatch_01", [])
    one = run_cli("status", "msgbatch_01")
    assert "Batch msgbatch_01: ended" in one.text
    assert "2 succeeded" in one.text
    assert "greek study batch collect msgbatch_01" in one.text
    batch = get_study_batch(db, "msgbatch_01")
    assert (batch.status, batch.ended_at) == ("ended", "2026-10-03 12:30:00")
    assert run_cli("status", "msgbatch_99").exit_code == 1


def test_collect_waits_until_the_batch_has_ended(iliad, run_cli, batches, db):
    _, text_id = iliad
    _submit(run_cli, text_id)
    result = run_cli("collect", "msgbatch_01")
    assert result.exit_code == 1
    assert "has not ended yet (in_progress" in result.text
    assert _count(db, "word_study") == 0


def test_collect_stores_every_result_with_usage(iliad, run_cli, batches, tmp_path):
    db, text_id = iliad
    _submit(run_cli, text_id)
    batches.end(
        "msgbatch_01",
        [  # results come in any order
            _succeeded(f"{text_id}_6-7", _answer(db, text_id, 6, 7)),
            _succeeded(f"{text_id}_1-5", _answer(db, text_id, 1, 5)),
        ],
    )
    result = run_cli("collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Requests stored │ 2" in result.text
    assert "Words │ 31" in result.text
    assert "Words parsed by the model │ 6" in result.text
    assert "parse_check remarks │ 2" in result.text
    assert "Requests failed │ 0" in result.text
    assert not list(tmp_path.glob("study-failed-*"))

    assert [
        (u.first_line, u.last_line, u.fluent)
        for u in list_translation_units(db, text_id, 1, 7, MODEL)
    ] == [(1, 5, "Fluent 1-5."), (6, 7, "Fluent 6-7.")]
    literal = list_line_translations(db, text_id, 1, 7, MODEL)
    assert [t.literal for t in literal][:2] == ["literal 1.1", "literal 1.2"]
    assert len(literal) == 7
    words = list_word_study(db, text_id, 1, 7, MODEL)
    assert len(words) == 31
    assert (words[0].gloss, words[0].note, words[0].parse_check) == (
        "gloss 1.1 0",
        "note on Μῆνιν",
        None,
    )
    assert words[1].parse_check == "imperative, not indicative"
    assert {w.note_version for w in words} == {2}  # the revised note rule

    parses = list_word_parses(db, text_id)
    model_parsed = {
        (p.line_number, p.token_index) for p in parses if p.source == "model"
    }
    assert model_parsed == UNPARSED
    hades = next(p for p in parses if (p.line_number, p.token_index) == (3, 4))
    assert (hades.lemma, hades.postag, hades.model_used) == (
        "Ἀΐδης",
        "n-s---md-",
        MODEL,
    )
    assert parses[0].source == "treebank"  # parsed words keep the treebank's

    requests = list_study_requests(db, "msgbatch_01")
    assert {r.status for r in requests} == {"succeeded"}
    assert all(r.output_tokens == 2500 and r.cost_usd > 0 for r in requests)
    batch = get_study_batch(db, "msgbatch_01")
    assert (batch.input_tokens, batch.cache_read_tokens, batch.output_tokens) == (
        6000,
        1800,
        5000,
    )
    assert batch.cost_usd == pytest.approx(
        (6000 * 2.00 + 1800 * 0.10 + 5000 * 10.00) / 1e6
    )
    assert batch.collected_at is not None
    assert "$0.06" in result.text  # the batch's cost

    # everything is saved now, and later estimates use the measured rate
    done = run_cli("estimate", text_id)
    assert "Nothing to request" in done.text
    redo = run_cli("estimate", text_id, "--redo")
    assert "Output tokens (estimate, 161 per word) │ 5,000" in redo.text
    assert "measured over collected batches" in redo.text
    # a batch at effort medium measures nothing for effort high
    high = run_cli("estimate", text_id, "--redo", "--effort", "high")
    assert "Output tokens (estimate, 83 per word)" in high.text
    assert "is the plan's estimate" in high.text


def test_failed_requests_are_listed_and_resubmitted(iliad, run_cli, batches, tmp_path):
    db, text_id = iliad
    _submit(run_cli, text_id)
    short = _answer(db, text_id, 1, 5, literal=["only one line"])
    batches.end(
        "msgbatch_01",
        [
            _succeeded(f"{text_id}_1-5", short),
            _succeeded(f"{text_id}_6-7", _answer(db, text_id, 6, 7)),
        ],
    )
    result = run_cli("collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Requests stored │ 1" in result.text
    assert "Requests failed │ 1" in result.text
    assert "wrong count: 1 literal lines for 5 lines │ 1" in result.text
    failed_file = tmp_path / "study-failed-msgbatch_01.txt"
    assert failed_file.read_text() == f"{text_id}_1-5\n"
    assert f"submit {text_id} --retry {failed_file} --effort medium'" in result.text

    first, second = list_study_requests(db, "msgbatch_01")
    assert (first.status, first.failure) == (
        "failed",
        "wrong count: 1 literal lines for 5 lines",
    )
    assert first.cost_usd > 0  # a failed answer is still billed
    assert second.status == "succeeded"
    assert len(list_word_study(db, text_id, 1, 7, MODEL)) == 7  # only 2.1–2.2
    assert all(p.source == "treebank" for p in list_word_parses(db, text_id))

    # --retry asks for just the failed request
    estimate = run_cli("submit", text_id, "--retry", failed_file)
    assert "Requests (translation units) │ 1" in estimate.text
    assert "Nothing submitted." in estimate.text
    resubmit = _submit(run_cli, text_id, "--retry", failed_file)
    assert (
        "Submitted batch msgbatch_02: 1 requests "
        "(Iliad 1.1–1.5 (retry of 1): effort medium)"
    ) in resubmit.text
    assert [r["custom_id"] for r in batches.created[1]] == [f"{text_id}_1-5"]

    batches.end(
        "msgbatch_02", [_succeeded(f"{text_id}_1-5", _answer(db, text_id, 1, 5))]
    )
    assert run_cli("collect", "msgbatch_02").exit_code == 0
    assert len(list_word_study(db, text_id, 1, 7, MODEL)) == 31
    assert len(list_line_translations(db, text_id, 1, 7, MODEL)) == 7
    assert "Nothing to request" in run_cli("estimate", text_id).text


def test_collect_fails_errors_refusals_truncation_and_missing_results(
    iliad, run_cli, batches, tmp_path, db
):
    _, text_id = iliad
    _submit(run_cli, text_id)
    batches.end("msgbatch_01", [_errored(f"{text_id}_1-5")])
    result = run_cli("collect", "msgbatch_01", "--failed-file", tmp_path / "f.txt")
    assert result.exit_code == 0, result.output
    assert "error: api_error: Internal server error │ 1" in result.text
    assert "no result returned │ 1" in result.text
    assert (tmp_path / "f.txt").read_text() == f"{text_id}_1-5\n{text_id}_6-7\n"
    assert {r.status for r in list_study_requests(db, "msgbatch_01")} == {"failed"}

    _submit(run_cli, text_id, "--retry", tmp_path / "f.txt")
    batches.end(
        "msgbatch_02",
        [
            _succeeded(f"{text_id}_1-5", "", stop_reason="refusal"),
            _succeeded(f"{text_id}_6-7", '{"fluent": "Sle', stop_reason="max_tokens"),
        ],
    )
    result = run_cli("collect", "msgbatch_02")
    assert "refusal │ 1" in result.text
    assert "truncated at max_tokens │ 1" in result.text
    assert _count(db, "word_study") == 0


def test_collecting_again_replaces_results(iliad, run_cli, batches, db):
    _, text_id = iliad
    _submit(run_cli, text_id)
    batches.end(
        "msgbatch_01",
        [
            _succeeded(f"{text_id}_1-5", _answer(db, text_id, 1, 5)),
            _succeeded(f"{text_id}_6-7", _answer(db, text_id, 6, 7)),
        ],
    )
    run_cli("collect", "msgbatch_01")
    first_cost = get_study_batch(db, "msgbatch_01").cost_usd
    assert run_cli("collect", "msgbatch_01").exit_code == 0
    assert _count(db, "word_study", text_id) == 31
    assert _count(db, "translation_units", text_id) == 2
    assert get_study_batch(db, "msgbatch_01").cost_usd == pytest.approx(first_cost)


def test_collect_refuses_unknown_batches(iliad, run_cli, batches):
    assert run_cli("collect", "msgbatch_99").exit_code == 1


def test_retry_files_must_name_requests_of_the_text(iliad, run_cli, no_api, tmp_path):
    _, text_id = iliad
    bad = tmp_path / "bad.txt"
    bad.write_text("999_1-5\n")
    result = run_cli("estimate", text_id, "--retry", bad)
    assert result.exit_code == 1 and "not" in result.text
    bad.write_text("# comment only\n")
    assert run_cli("estimate", text_id, "--retry", bad).exit_code == 1
    bad.write_text(f"{text_id}:1-5\n")
    assert run_cli("estimate", text_id, "--retry", bad).exit_code == 1
    bad.write_text(f"{text_id}_1-50\n")
    assert run_cli("estimate", text_id, "--retry", bad).exit_code == 1


# --- Repository ---------------------------------------------------------------


def _save_unit(db, text_id, first, last, model=MODEL, fluent="F"):
    save_study_result(
        db,
        TranslationUnit(text_id, first, last, fluent, model),
        [LineTranslation(text_id, n, f"L{n}", model) for n in range(first, last + 1)],
        [WordStudy(text_id, first, 0, "g", "n", None, model)],
        [],
    )


def test_a_saved_unit_replaces_the_same_models_overlapping_units(iliad):
    db, text_id = iliad
    _save_unit(db, text_id, 1, 5)
    _save_unit(db, text_id, 6, 7)
    _save_unit(db, text_id, 1, 5, model="other-model")
    _save_unit(db, text_id, 4, 6, fluent="new")
    assert [
        (u.first_line, u.last_line, u.fluent)
        for u in list_translation_units(db, text_id, 1, 7, MODEL)
    ] == [(4, 6, "new")]
    assert len(list_translation_units(db, text_id, 1, 7, "other-model")) == 1
    # units overlapping a selection, not only those inside it
    assert len(list_translation_units(db, text_id, 5, 5, MODEL)) == 1
    assert list_translation_units(db, text_id, 7, 7, MODEL) == []


def test_model_identity_is_part_of_every_key(iliad):
    db, text_id = iliad
    _save_unit(db, text_id, 1, 2)
    _save_unit(db, text_id, 1, 2, model="other-model")
    assert _count(db, "line_translations", text_id) == 4
    assert _count(db, "word_study", text_id) == 2
    assert len(list_word_study(db, text_id, 1, 2, "other-model")) == 1


def test_deleting_a_text_deletes_its_study_data_but_keeps_batches(
    iliad, run_cli, batches
):
    db, text_id = iliad
    _submit(run_cli, text_id)
    _save_unit(db, text_id, 1, 5)
    db.execute("DELETE FROM lines WHERE text_id=?", (text_id,))
    db.execute("DELETE FROM texts WHERE id=?", (text_id,))
    db.commit()
    for table in ("translation_units", "line_translations", "word_study"):
        assert _count(db, table, text_id) == 0
    batch = get_study_batch(db, "msgbatch_01")
    assert batch.text_id is None and len(list_study_requests(db, batch.id)) == 2
    result = run_cli("collect", "msgbatch_01")
    assert result.exit_code == 1 and "has been deleted" in result.text
