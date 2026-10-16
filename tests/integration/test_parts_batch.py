"""`greek study parts` and parts batches in `greek study batch status | collect`,
with a mocked Message Batches client; migration 014 and its repository functions."""

import json
import sqlite3
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from greek.api.parts import PART_NAMES, group_verbs
from greek.api.study import MODEL
from greek.cli.study_cmd import study_cmd
from greek.db.connection import _MIGRATIONS_DIR, run_migrations
from greek.db.repos.parses import verb_lemmas
from greek.db.repos.study import (
    get_study_batch,
    list_parts_requests,
    measured_output_per_verb,
    pending_parts_verbs,
)
from greek.db.repos.verbs import get_verb_parts, save_verb_parts, verbs_with_parts
from greek.search import plain
from tests.integration import test_study_batch as study_batch

# The study batch tests' text with treebank data and fake Message Batches client
iliad = study_batch.iliad
batches = study_batch.batches
no_api = study_batch.no_api
_errored = study_batch._errored

BOOK_1 = ["ἀείδω", "τίθημι", "προϊάπτω", "ἐθέλω"]  # in the order they appear


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


def _usage(output=3000):
    return SimpleNamespace(
        input_tokens=400,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens=output,
        cache_creation=None,
    )


def _answer(verbs, skip=()):
    return json.dumps(
        {
            "verbs": [
                {
                    "verb": v,
                    "parts": [
                        {"form": f"{v}-{n}", "note": "Attic" if n == 2 else ""}
                        for n in range(1, 7)
                    ],
                }
                for v in verbs
                if v not in skip
            ]
        },
        ensure_ascii=False,
    )


def _succeeded(cid, text, output=3000):
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=_usage(output),
    )
    return SimpleNamespace(
        custom_id=cid, result=SimpleNamespace(type="succeeded", message=message)
    )


def _submit(run_cli, text_id, *args):
    result = run_cli("parts", "submit", text_id, *args, "--yes")
    assert result.exit_code == 0, result.output
    return result


def _batch_verbs(fake, n=0):
    return [
        r["params"]["messages"][0]["content"].splitlines()[2:] for r in fake.created[n]
    ]


# --- Migration 014 and the repository -----------------------------------------------


def test_migration_014_keeps_existing_batches_as_study_batches():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.create_function("greek_plain", 1, plain, deterministic=True)
    conn.execute("CREATE TABLE _migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if path.name >= "014":
            break
        conn.executescript(path.read_text())
        conn.execute("INSERT INTO _migrations(name) VALUES (?)", (path.name,))
    conn.execute(
        """INSERT INTO study_batches(id, scope, model, status, request_count)
           VALUES ('msgbatch_old', 'Iliad 1.1–1.611', 'claude-opus-5-5', 'ended', 71)"""
    )
    conn.commit()
    run_migrations(conn)
    assert conn.execute("SELECT kind FROM study_batches").fetchone()[0] == "study"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE study_batches SET kind='other'")
    assert "parts_requests" in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def test_parts_requests_go_with_their_batch(iliad, batches, run_cli):
    db, text_id = iliad
    _submit(run_cli, text_id, "--books", "1")
    db.execute("DELETE FROM study_batches")
    assert db.execute("SELECT count(*) FROM parts_requests").fetchone()[0] == 0


def test_verb_lemmas_in_order_of_first_appearance(iliad):
    db, text_id = iliad
    assert verb_lemmas(db, text_id) == [*BOOK_1, "εὕδω"]
    assert verb_lemmas(db, text_id, 1, 5) == BOOK_1
    assert verb_lemmas(db, text_id, 6, 7) == ["εὕδω"]
    assert verb_lemmas(db, text_id, 4, 4) == []  # 1.4 has no treebank parse


def test_verbs_with_parts_by_model(db):
    save_verb_parts(db, "ἀείδω", MODEL, [])
    save_verb_parts(db, "τίθημι", "claude-sonnet-5-5", [])
    assert verbs_with_parts(db, ["ἀείδω", "τίθημι", "λύω"], MODEL) == {"ἀείδω"}


# --- estimate and submit ----------------------------------------------------------


def test_estimate_is_local(iliad, no_api, run_cli):
    db, text_id = iliad
    result = run_cli("parts", "estimate", text_id, "--books", "1")
    assert result.exit_code == 0, result.output
    assert "Iliad 1.1–1.5: principal parts of 4 verbs" in result.text
    assert "Requests (20 verbs each) │ 1" in result.text
    assert "Verbs │ 4" in result.text
    assert "Output tokens (estimate, 350 per verb) │ 1,400" in result.text
    assert "an estimate until a batch is collected" in result.text
    whole = run_cli("parts", "estimate", text_id)
    assert "Iliad 1.1–2.2: principal parts of 5 verbs" in whole.text


def test_submit_needs_yes(iliad, no_api, run_cli):
    db, text_id = iliad
    result = run_cli("parts", "submit", text_id, "--books", "2")
    assert result.exit_code == 0, result.output
    assert "Nothing submitted." in result.text
    assert "rerun with --yes" in result.text
    assert db.execute("SELECT count(*) FROM study_batches").fetchone()[0] == 0


def test_submit_records_a_parts_batch(iliad, batches, run_cli, monkeypatch):
    db, text_id = iliad
    monkeypatch.setattr(
        "greek.cli.study_cmd.group_verbs", lambda verbs: group_verbs(verbs, 3)
    )
    result = _submit(run_cli, text_id, "--books", "1")
    assert "Submitted batch msgbatch_01: 2 requests" in result.text
    assert [r["custom_id"] for r in batches.created[0]] == [
        f"{text_id}_parts_1",
        f"{text_id}_parts_2",
    ]
    assert _batch_verbs(batches) == [
        ["1 ἀείδω", "2 τίθημι", "3 προϊάπτω"],
        ["1 ἐθέλω"],
    ]
    batch = get_study_batch(db, "msgbatch_01")
    assert batch.kind == "parts" and batch.text_id == text_id
    assert batch.scope == "Iliad 1.1–1.5: principal parts of 4 verbs (Homeric)"
    assert batch.dialect == "homeric"
    assert batch.request_count == 2
    requests = list_parts_requests(db, "msgbatch_01")
    assert [(r.custom_id, r.verbs, r.status) for r in requests] == [
        (f"{text_id}_parts_1", BOOK_1[:3], "pending"),
        (f"{text_id}_parts_2", BOOK_1[3:], "pending"),
    ]
    assert pending_parts_verbs(db, "homeric", MODEL) == set(BOOK_1)
    # Waiting verbs are not requested again
    again = run_cli("parts", "estimate", text_id)
    assert "principal parts of 1 verbs" in again.text
    assert "4 waiting in a batch not yet collected" in again.text
    assert (
        "principal parts of 5 verbs"
        in run_cli("parts", "estimate", text_id, "--redo").text
    )


def test_verbs_with_saved_parts_are_left_out(iliad, no_api, run_cli):
    db, text_id = iliad
    for verb in BOOK_1:
        save_verb_parts(db, verb, MODEL, [], "homeric")
    save_verb_parts(db, "εὕδω", "claude-sonnet-5-5", [], "homeric")  # another model's
    save_verb_parts(db, "εὕδω", MODEL, [])  # a Verbs-page lookup: no dialect
    save_verb_parts(db, "ἐθέλω", MODEL, [], "ionic")  # Herodotus' don't count
    result = run_cli("parts", "estimate", text_id)
    assert "principal parts of 1 verbs (Homeric)" in result.text
    assert "Left out: 4 verbs with saved Homeric parts" in result.text
    result = run_cli("parts", "estimate", text_id, "--books", "1")
    assert "Nothing to request" in result.text


def test_submit_reports_api_errors(iliad, batches, run_cli):
    import anthropic
    import httpx

    db, text_id = iliad
    batches.fail_create = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages/batches")
    )
    result = run_cli("parts", "submit", text_id, "--yes")
    assert result.exit_code == 1
    assert "the batch was not submitted" in result.text
    assert db.execute("SELECT count(*) FROM study_batches").fetchone()[0] == 0


@pytest.mark.parametrize(
    "args, message",
    [
        (["--books", "1", "--retry", "msgbatch_01"], "Use only one of --books"),
        (["--books", "9"], "has no book 9"),
        (["--retry", "msgbatch_99"], "No principal-parts batch msgbatch_99"),
    ],
)
def test_bad_scopes(iliad, no_api, run_cli, args, message):
    db, text_id = iliad
    result = run_cli("parts", "estimate", text_id, *args)
    assert result.exit_code == 1
    assert message in result.text


def test_a_text_without_treebank_data(db, no_api, run_cli):
    from greek.db.repos.lines import insert_lines
    from greek.db.repos.texts import insert_text

    text_id = insert_text(db, "Odyssey", "Homer", "o.txt", "/d/o.txt", 1)
    insert_lines(db, text_id, ["ἄνδρα μοι ἔννεπε"], ["1.1"])
    result = run_cli("parts", "estimate", text_id)
    assert result.exit_code == 1
    assert "has no treebank data" in result.text


# --- collect -------------------------------------------------------------------------


def test_collect_stores_parts_and_lists_failed_verbs(iliad, batches, run_cli):
    db, text_id = iliad
    _submit(run_cli, text_id, "--books", "1")
    cid = f"{text_id}_parts_1"
    batches.end("msgbatch_01", [_succeeded(cid, _answer(BOOK_1, skip=["ἐθέλω"]))])
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Requests fully stored │ 0" in result.text
    assert "Verbs stored │ 3" in result.text
    assert "Verbs failed │ 1" in result.text
    assert "1 of 4 verbs not stored: ἐθέλω: not in the output" in result.text
    assert f"greek study parts submit {text_id} --retry msgbatch_01" in result.text
    saved = get_verb_parts(db, "ἀείδω", MODEL)
    assert [p["form"] for p in saved.parts] == [f"ἀείδω-{n}" for n in range(1, 7)]
    assert saved.parts[1] == {
        "part": 2,
        "name": PART_NAMES[1],
        "form": "ἀείδω-2",
        "notes": "Attic",
    }
    assert get_verb_parts(db, "ἐθέλω", MODEL) is None
    (request,) = list_parts_requests(db, "msgbatch_01")
    assert request.status == "failed" and request.stored_count == 3
    assert request.output_tokens == 3000 and request.cost_usd > 0
    batch = get_study_batch(db, "msgbatch_01")
    assert batch.collected_at and batch.output_tokens == 3000
    assert batch.cost_usd == pytest.approx(request.cost_usd)
    assert measured_output_per_verb(db, MODEL) == 750  # 3,000 tokens for 4 verbs
    assert pending_parts_verbs(db, "homeric", MODEL) == set()

    # The retry asks only for the verb still missing, at the measured rate
    retry = run_cli("parts", "estimate", text_id, "--retry", "msgbatch_01")
    assert "Iliad, retry of msgbatch_01: principal parts of 1 verbs" in retry.text
    assert "Left out: 3 verbs with saved Homeric parts" in retry.text
    assert "750 per verb" in retry.text
    assert "measured over collected batches" in retry.text
    _submit(run_cli, text_id, "--retry", "msgbatch_01")
    assert _batch_verbs(batches, 1) == [["1 ἐθέλω"]]


def test_collect_all_stored(iliad, batches, run_cli):
    db, text_id = iliad
    _submit(run_cli, text_id)
    batches.end(
        "msgbatch_01",
        [_succeeded(f"{text_id}_parts_1", _answer([*BOOK_1, "εὕδω"]), output=2000)],
    )
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Requests fully stored │ 1" in result.text
    assert "Verbs stored │ 5" in result.text
    assert "Per verb asked for: 400 output tokens" in result.text
    assert "--retry" not in result.text
    (request,) = list_parts_requests(db, "msgbatch_01")
    assert request.status == "succeeded" and request.failure is None
    assert verbs_with_parts(db, [*BOOK_1, "εὕδω"], MODEL) == {*BOOK_1, "εὕδω"}
    # The Verbs page shows a batch result like any saved lookup
    assert "Nothing to request" in run_cli("parts", "estimate", text_id).text


def test_collect_failed_and_missing_requests(iliad, batches, run_cli, monkeypatch):
    db, text_id = iliad
    monkeypatch.setattr(
        "greek.cli.study_cmd.group_verbs", lambda verbs: group_verbs(verbs, 2)
    )
    _submit(run_cli, text_id)  # 3 requests: 2, 2 and 1 verbs
    batches.end(
        "msgbatch_01",
        [
            _errored(f"{text_id}_parts_1"),
            _succeeded(f"{text_id}_parts_2", "not json"),
        ],
    )
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Verbs stored │ 0" in result.text
    assert "Verbs failed │ 5" in result.text
    assert "error: api_error: Internal server error" in result.text
    assert "the output is not valid JSON" in result.text
    assert "no result returned" in result.text
    statuses = [
        (r.status, r.stored_count) for r in list_parts_requests(db, "msgbatch_01")
    ]
    assert statuses == [("failed", 0)] * 3
    assert db.execute("SELECT count(*) FROM verb_parts").fetchone()[0] == 0


def test_status_lists_parts_batches(iliad, batches, run_cli):
    db, text_id = iliad
    _submit(run_cli, text_id, "--books", "1")
    result = run_cli("batch", "status")
    assert result.exit_code == 0, result.output
    assert "msgbatch_01" in result.text and "principal" in result.text
    assert "in_progress" in result.text


def test_collect_waits_for_the_batch_to_end(iliad, batches, run_cli):
    db, text_id = iliad
    _submit(run_cli, text_id, "--books", "1")
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 1
    assert "has not ended yet" in result.text
    assert list_parts_requests(db, "msgbatch_01")[0].status == "pending"
