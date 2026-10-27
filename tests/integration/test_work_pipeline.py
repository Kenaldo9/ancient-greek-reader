"""
One pipeline for every text, with a mocked
Message Batches client: a GLAUx Thucydides text through `greek study batch`,
`uses`, `forms` and `parts` (its prompts, section units, automatic-parse
marks, the words left out, its dialect); the Odyssey reusing the Iliad's
Homeric forms and parts; migration 019; and the word pane's dialect mark.
"""

import json
import sqlite3
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from greek.api.forms import forms_system_prompt
from greek.api.parts import parts_system_prompt
from greek.api.study import (
    MODEL,
    STUDY_SCHEMA_WORDS,
    study_system_prompt,
    study_words,
)
from greek.api.uses import uses_system_prompt
from greek.api.works import ODYSSEY, THUCYDIDES
from greek.cli.study_cmd import study_cmd
from greek.db.connection import _MIGRATIONS_DIR
from greek.db.repos.lines import get_lines, insert_lines
from greek.db.repos.parses import list_word_parses, save_tb_source, save_treebank
from greek.db.repos.study import (
    get_lemma_forms,
    get_study_batch,
    list_study_requests,
    list_word_study,
    save_forms_result,
    save_parts_result,
    save_study_result,
)
from greek.db.repos.texts import insert_text
from greek.db.repos.verbs import get_verb_parts, save_verb_parts
from greek.fetch.treebank import WORKS, align_text, parse_treebank
from greek.models import (
    LemmaForms,
    LineTranslation,
    TranslationUnit,
    TreebankSource,
    WordStudy,
)
from greek.search import plain
from tests.integration import test_glaux_import as glaux
from tests.integration.test_web_study import tag
from tests.integration import test_study_batch as study_batch

batches = study_batch.batches
no_api = study_batch.no_api
iliad = study_batch.iliad

# The GLAUx excerpt's Thucydides with a speaker's label (ΑΘ.) and an editorial
# mark (†) in 2.1.1, which its treebank lacks. Units: 1.1.1–1.1.2 (sentence 1
# runs into 1.1.2; 20 words) and 2.1.1 (another book; 6 words).
APP_LINES = [
    *glaux.APP_LINES[:2],
    ("2.1.1", "ΑΘ. Ἄρχεται δὲ ὁ πόλεμος † ἐνθένδε ἤδη."),
]


@pytest.fixture
def thucydides(db, tmp_path):
    """
    The Thucydides text with its GLAUx treebank: sentence 1 by hand, sentence
    2 (in 1.1.2) by GLAUx's parser, and sentence 3 (2.1.1) by the parser too.
    """
    xml = glaux.GLAUX.read_text().replace(
        'id="3" document_id="0003-001" analysis="manual"',
        'id="3" document_id="0003-001" analysis="auto"',
    )
    path = tmp_path / "0003-001.xml"
    path.write_text(xml)
    text_id = glaux._add_text(db, APP_LINES)
    glaux._import(db, text_id, path)
    return db, text_id


@pytest.fixture
def run_cli(db, monkeypatch, tmp_path):
    monkeypatch.setattr("greek.cli.study_cmd.get_connection", lambda: db)
    monkeypatch.setenv("GREEK_DATA_DIR", str(tmp_path))

    def run(*args):
        result = CliRunner().invoke(study_cmd, [str(a) for a in args])
        result.text = " ".join(result.output.split())  # Rich wraps at the width
        return result

    return run


def _usage(output=2000):
    return SimpleNamespace(
        input_tokens=3000,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens=output,
        cache_creation=None,
    )


def _succeeded(cid, data):
    message = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text=json.dumps(data, ensure_ascii=False))
        ],
        stop_reason="end_turn",
        usage=_usage(),
    )
    return SimpleNamespace(
        custom_id=cid, result=SimpleNamespace(type="succeeded", message=message)
    )


def _words(db, text_id, first, last):
    lines = get_lines(db, text_id, first, last)
    return lines, study_words(lines, list_word_parses(db, text_id, first, last))


def _study_answer(db, text_id, first, last, repeat=None):
    lines, words = _words(db, text_id, first, last)
    return {
        "fluent": f"Fluent {first}-{last}.",
        "literal": [f"literal {line.reference}" for line in lines],
        "words": [
            {
                **({"word": repeat(i, w)} if repeat else {}),
                "gloss": f"gloss {w.form}",
                "note": "",
            }
            for i, w in enumerate(words)
        ],
    }


def _request(fake, cid, n=0):
    return next(r for r in fake.created[n] if r["custom_id"] == cid)


# --- Study requests for a prose text ------------------------------------------------


def test_a_prose_text_gets_its_works_prompts_and_section_units(thucydides, run_cli):
    _, text_id = thucydides
    result = run_cli("batch", "estimate", text_id)
    assert result.exit_code == 0, result.output
    assert "Requests (translation units) │ 2" in result.text
    assert "Sections │ 3" in result.text
    assert "Words │ 26" in result.text  # 28 tokens less ΑΘ. and †
    assert "Words without a treebank parse │ 0" in result.text
    assert "Words parsed automatically │ 14" in result.text
    assert "Largest request: 2 sections, 20 words" in result.text


def test_prose_study_requests_mark_automatic_parses_and_leave_out_non_words(
    thucydides, batches, run_cli
):
    _, text_id = thucydides
    result = run_cli("batch", "submit", text_id, "--effort", "high", "--yes")
    assert result.exit_code == 0, result.output
    first = _request(batches, f"{text_id}_1-2")["params"]
    assert first["system"][0]["text"] == study_system_prompt(THUCYDIDES)
    content = first["messages"][0]["content"].splitlines()
    assert content[:4] == [
        'Thucydides 1.1.1–1.1.2: 2 sections, 20 words. Give "literal" 2 strings '
        'and "words" 20 objects.',
        "",
        "Sections:",
        "1.1.1 " + APP_LINES[0][1],
    ]
    words = content[content.index("Words:") + 1 :]
    assert words[0] == "[1.1.1]"
    # 1.1.2 mixes sentence 1 (by hand) and sentence 2 (by GLAUx's parser)
    assert words[11] == "[1.1.2]"
    assert words[12].startswith("11 οὐδʼ: οὐ, adverb + δέ, conjunction")
    assert words[14] == (
        "13 κίνησις: κίνησις, noun singular feminine nominative | automatic parse "
        "| first in this book"
    )
    second = _request(batches, f"{text_id}_3-3")["params"]["messages"][0]["content"]
    assert second.startswith(
        'Thucydides 2.1.1: 1 section, 6 words. Give "literal" 1 string and "words" '
        "6 objects."
    )
    assert "[2.1.1] (automatic parse)\n1 Ἄρχεται: ἄρχω," in second
    assert "automatic parse |" not in second.split("Words:")[1]
    listed = second.split("Words:")[1]
    assert "ΑΘ" not in listed and "†" not in listed
    assert "2.1.1 ΑΘ. Ἄρχεται δὲ ὁ πόλεμος † ἐνθένδε ἤδη." in second


def test_collect_stores_a_prose_unit_and_counts_it_covered(
    thucydides, batches, run_cli
):
    db, text_id = thucydides
    run_cli("batch", "submit", text_id, "--yes")
    batches.end(
        "msgbatch_01",
        [
            _succeeded(f"{text_id}_1-2", _study_answer(db, text_id, 1, 2)),
            _succeeded(f"{text_id}_3-3", _study_answer(db, text_id, 3, 3)),
        ],
    )
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Words │ 26" in result.text
    saved = {
        (w.line_number, w.token_index)
        for w in list_word_study(db, text_id, 3, 3, MODEL)
    }
    assert saved == {(3, 1), (3, 2), (3, 3), (3, 4), (3, 6), (3, 7)}  # not ΑΘ., †
    # every word of every unit is saved, though 2.1.1 has 8 tokens
    assert "Nothing to request" in run_cli("batch", "estimate", text_id).text


def test_repeat_words_asks_for_each_word_and_checks_it(thucydides, batches, run_cli):
    db, text_id = thucydides
    result = run_cli("batch", "submit", text_id, "--repeat-words", "--yes")
    assert result.exit_code == 0, result.output
    params = _request(batches, f"{text_id}_3-3")["params"]
    assert params["output_config"]["format"]["schema"] == STUDY_SCHEMA_WORDS
    assert '- "word": the numbered word' in params["system"][0]["text"]
    assert get_study_batch(db, "msgbatch_01").scope.endswith(
        "effort medium, words repeated"
    )
    _, words = _words(db, text_id, 3, 3)
    swapped = {0: words[1].form, 1: words[0].form}
    batches.end(
        "msgbatch_01",
        [
            _succeeded(
                f"{text_id}_1-2",
                _study_answer(db, text_id, 1, 2, repeat=lambda i, w: w.form),
            ),
            _succeeded(
                f"{text_id}_3-3",
                _study_answer(
                    db, text_id, 3, 3, repeat=lambda i, w: swapped.get(i, w.form)
                ),
            ),
        ],
    )
    result = run_cli("batch", "collect", "msgbatch_01")
    assert "Requests stored │ 1" in result.text
    failed = {r.custom_id: r.failure for r in list_study_requests(db, "msgbatch_01")}
    assert failed == {
        f"{text_id}_1-2": None,
        f"{text_id}_3-3": "word 1 is for δὲ, not Ἄρχεται (2.1.1)",
    }
    assert list_word_study(db, text_id, 3, 3, MODEL) == []


def test_prose_uses_requests_have_the_works_prompt_and_marks(
    thucydides, batches, run_cli
):
    db, text_id = thucydides
    for first, last in ((1, 2), (3, 3)):
        lines, words = _words(db, text_id, first, last)
        save_study_result(
            db,
            TranslationUnit(text_id, first, last, "Fluent.", MODEL),
            [LineTranslation(text_id, n.line_number, "lit", MODEL) for n in lines],
            [
                WordStudy(
                    text_id, w.line_number, w.token_index, "g", "", None, MODEL, 2
                )
                for w in words
            ],
            [],
        )
    result = run_cli("uses", "submit", text_id, "--yes")
    assert result.exit_code == 0, result.output
    params = _request(batches, f"{text_id}_1-2")["params"]
    assert params["system"][0]["text"] == uses_system_prompt(THUCYDIDES)
    content = params["messages"][0]["content"]
    assert content.startswith("Thucydides 1.1.1–1.1.2: 2 sections, 20 words,")
    assert "| automatic parse | gloss: g" in content
    assert "ἐλπίσας: ἐλπίζω, participle" in content and "→ use" in content


# --- Forms and parts in the text's dialect --------------------------------------


def _forms_answer(lemmas, verbs):
    return {
        "lemmas": [
            {
                "lemma": lemma,
                "headword": "" if lemma in verbs else f"{lemma}, -ου, ὁ",
                "forms": "",
                "comparison": "",
                "construction": "+ acc., do" if lemma in verbs else "",
            }
            for lemma in lemmas
        ]
    }


def test_forms_are_made_and_stored_in_the_texts_dialect(thucydides, batches, run_cli):
    db, text_id = thucydides
    # the Iliad's Homeric form doesn't count for Thucydides
    save_forms_result(db, [LemmaForms("πόλεμος", MODEL, "noun", "πόλεμος, -οιο")])
    result = run_cli("forms", "submit", text_id, "--yes")
    assert result.exit_code == 0, result.output
    assert "Left out" not in result.text
    batch = get_study_batch(db, "msgbatch_01")
    assert batch.dialect == "attic" and batch.scope.endswith("verbs (Attic)")
    params = batches.created[0][0]["params"]
    assert params["system"][0]["text"] == forms_system_prompt(THUCYDIDES)
    content = params["messages"][0]["content"]
    assert "πόλεμος (noun) | not in LSJ | Thucydides: πόλεμο" in content
    asked = [line.split(" ")[1] for line in content.splitlines()[2:]]
    verbs = {"συγγράφω", "ἐλπίζω", "εἰμί", "γίγνομαι", "ἄρχω"} & set(asked)
    batches.end(
        "msgbatch_01", [_succeeded(f"{text_id}_forms_1", _forms_answer(asked, verbs))]
    )
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    rows = db.execute(
        "SELECT dialect, headword FROM lemma_forms WHERE lemma='πόλεμος' ORDER BY dialect"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("attic", "πόλεμος, -ου, ὁ"),
        ("homeric", "πόλεμος, -οιο"),
    ]
    assert (
        get_lemma_forms(db, ["πόλεμος"], MODEL, "attic")["πόλεμος"].dialect == "attic"
    )
    assert (
        get_lemma_forms(db, ["πόλεμος"], MODEL, "homeric")["πόλεμος"].dialect
        == "homeric"
    )
    result = run_cli("forms", "estimate", text_id)
    assert "Nothing to request" in result.text


def test_parts_are_made_and_stored_in_the_texts_dialect(thucydides, batches, run_cli):
    db, text_id = thucydides
    save_verb_parts(db, "γίγνομαι", MODEL, [{"part": 1, "form": "γίγνομαι"}], "homeric")
    save_verb_parts(db, "ἄρχω", MODEL, [{"part": 1, "form": "ἄρχω"}])  # a lookup
    result = run_cli("parts", "submit", text_id, "--yes")
    assert result.exit_code == 0, result.output
    assert "principal parts of 5 verbs (Attic)" in result.text
    params = batches.created[0][0]["params"]
    assert params["system"][0]["text"] == parts_system_prompt(THUCYDIDES)
    verbs = [
        line.split(" ", 1)[1]
        for line in params["messages"][0]["content"].splitlines()[2:]
    ]
    answer = {
        "verbs": [
            {
                "verb": v,
                "parts": [{"form": f"{v}-{n}", "note": ""} for n in range(1, 7)],
            }
            for v in verbs
        ]
    }
    batches.end("msgbatch_01", [_succeeded(f"{text_id}_parts_1", answer)])
    assert run_cli("batch", "collect", "msgbatch_01").exit_code == 0
    assert (
        get_verb_parts(db, "γίγνομαι", MODEL, "attic").parts[0]["form"] == "γίγνομαι-1"
    )
    assert (
        get_verb_parts(db, "γίγνομαι", MODEL, "homeric").parts[0]["form"] == "γίγνομαι"
    )
    # without a dialect, the newest parts
    assert get_verb_parts(db, "ἄρχω", MODEL).parts[0]["form"] == "ἄρχω-1"
    assert get_verb_parts(db, "ἄρχω", MODEL, "attic").dialect == "attic"
    assert "Nothing to request" in run_cli("parts", "estimate", text_id).text


# --- The Odyssey reuses the Iliad's Homeric forms and parts ------------------------


def _as_work(db, text_id, work):
    w = WORKS[work]
    save_tb_source(db, TreebankSource(text_id, work, w.corpus, w.filename, w.licence))


@pytest.fixture
def two_homeric_texts(iliad):
    """The study tests' Iliad text, and a copy of it recorded as the Odyssey."""
    db, iliad_id = iliad
    _as_work(db, iliad_id, "iliad")
    lines = study_batch.APP_LINES
    odyssey_id = insert_text(db, "Odyssey", "Homer", "od.txt", "/d/od.txt", len(lines))
    insert_lines(db, odyssey_id, [c for _, c in lines], [r for r, _ in lines])
    result = align_text(
        odyssey_id, get_lines(db, odyssey_id), parse_treebank(study_batch.FIXTURE)
    )
    save_treebank(db, odyssey_id, result.sentences, result.parses)
    _as_work(db, odyssey_id, "odyssey")
    return db, iliad_id, odyssey_id


def test_the_odyssey_reuses_homeric_forms_and_parts(
    two_homeric_texts, batches, run_cli
):
    db, iliad_id, odyssey_id = two_homeric_texts
    save_forms_result(db, [LemmaForms("μῆνις", MODEL, "noun", "μῆνις, -ιος, ἡ")])
    save_parts_result(db, MODEL, {"ἀείδω": []})
    # an Iliad batch still waiting holds the verbs it asked for
    assert run_cli("parts", "submit", iliad_id, "--books", "2", "--yes").exit_code == 0
    result = run_cli("forms", "estimate", odyssey_id)
    assert "Left out: 1 lemmas with a saved Homeric form" in result.text
    result = run_cli("parts", "submit", odyssey_id, "--yes")
    assert (
        "Left out: 1 verbs with saved Homeric parts by claude-opus-5-5, 1 waiting"
        in (result.text)
    )
    params = batches.created[1][0]["params"]
    assert params["system"][0]["text"] == parts_system_prompt(ODYSSEY)
    assert get_study_batch(db, "msgbatch_02").dialect == "homeric"


def test_the_odysseys_study_requests_name_it(two_homeric_texts, batches, run_cli):
    _, _, odyssey_id = two_homeric_texts
    assert run_cli("batch", "submit", odyssey_id, "--yes").exit_code == 0
    params = batches.created[0][0]["params"]
    assert params["system"][0]["text"] == study_system_prompt(ODYSSEY)
    assert params["messages"][0]["content"].startswith("Odyssey 1.1–1.5: 5 lines,")


# --- Migration 019 -------------------------------------------------------------------


def _migrated_to(before: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.create_function("greek_plain", 1, plain, deterministic=True)
    conn.execute("CREATE TABLE _migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if path.name >= before:
            break
        conn.executescript(path.read_text())
        conn.execute("INSERT INTO _migrations(name) VALUES (?)", (path.name,))
    conn.commit()
    return conn


def test_migration_019_keys_forms_and_parts_by_dialect():
    conn = _migrated_to("019")
    conn.executescript(
        """
        INSERT INTO study_batches(id, scope, model, status, request_count, kind)
        VALUES ('b_parts', 'Iliad 1', 'claude-opus-5-5', 'ended', 1, 'parts'),
               ('b_wait', 'Iliad 2', 'claude-opus-5-5', 'in_progress', 1, 'parts'),
               ('b_forms', 'Iliad 1', 'claude-opus-5-5', 'ended', 1, 'forms'),
               ('b_study', 'Iliad 1', 'claude-opus-5-5', 'ended', 1, 'study');
        INSERT INTO parts_requests(batch_id, custom_id, verbs, verb_count, status,
                                   stored_count)
        VALUES ('b_parts', '12_parts_1', '["ἀείδω", "τίθημι"]', 2, 'succeeded', 2),
               ('b_wait', '12_parts_1', '["εὕδω"]', 1, 'pending', 0);
        INSERT INTO verb_parts(verb, model_used, parts_json)
        VALUES ('ἀείδω', 'claude-opus-5-5', '[1]'),
               ('τίθημι', 'gpt-6.1', '[2]'),
               ('λύω', 'claude-opus-5-5', '[3]'),
               ('εὕδω', 'claude-opus-5-5', '[4]');
        INSERT INTO lemma_forms(lemma, model_used, pos, headword)
        VALUES ('μῆνις', 'claude-opus-5-5', 'noun', 'μῆνις, -ιος, ἡ');
        """
    )
    conn.commit()
    ids = dict(conn.execute("SELECT verb, id FROM verb_parts").fetchall())
    # 019 alone: 020 drops the lookups (dialect '') it keeps
    conn.executescript((_MIGRATIONS_DIR / "019_dialects.sql").read_text())
    rows = conn.execute(
        "SELECT verb, dialect, model_used, id FROM verb_parts ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("ἀείδω", "homeric", "claude-opus-5-5", ids["ἀείδω"]),  # stored by b_parts
        ("τίθημι", "", "gpt-6.1", ids["τίθημι"]),  # another model's: a lookup
        ("λύω", "", "claude-opus-5-5", ids["λύω"]),  # never asked: a lookup
        ("εὕδω", "", "claude-opus-5-5", ids["εὕδω"]),  # still pending: a lookup
    ]
    row = conn.execute("SELECT lemma, dialect, headword FROM lemma_forms").fetchone()
    assert tuple(row) == ("μῆνις", "homeric", "μῆνις, -ιος, ἡ")
    dialects = dict(conn.execute("SELECT id, dialect FROM study_batches").fetchall())
    assert dialects == {
        "b_parts": "homeric",
        "b_wait": "homeric",
        "b_forms": "homeric",
        "b_study": None,
    }
    assert conn.execute("SELECT count(*) FROM parts_requests").fetchone()[0] == 2
    # a verb can now have a lookup and a batch's parts by the same model
    conn.execute(
        "INSERT INTO verb_parts(verb, dialect, model_used, parts_json) "
        "VALUES ('ἀείδω', '', 'claude-opus-5-5', '[]')"
    )
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        conn.execute(
            "INSERT INTO verb_parts(verb, dialect, model_used, parts_json) "
            "VALUES ('ἀείδω', 'homeric', 'claude-opus-5-5', '[]')"
        )
    conn.execute(
        "INSERT INTO lemma_forms(lemma, dialect, model_used, pos, headword) "
        "VALUES ('μῆνις', 'ionic', 'claude-opus-5-5', 'noun', 'μῆνις, -ιος, ἡ')"
    )
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# --- The word pane prefers the text's dialect and names another -----------------------


def _strip(client, text_id, line, i):
    resp = client.get(f"/texts/{text_id}/word", params={"line": line, "i": i})
    assert resp.status_code == 200
    return resp.text


def test_the_word_pane_names_the_dialect_of_forms_made_for_another_work(client, web_db):
    text_id = glaux._add_text(web_db)
    glaux._import(web_db, text_id)
    save_forms_result(
        web_db, [LemmaForms("πόλεμος", MODEL, "noun", "πόλεμος, -οιο, ὁ")]
    )
    save_forms_result(
        web_db,
        [
            LemmaForms(
                "συγγράφω", MODEL, "verb", "συγγράφω", construction="+ acc., write"
            )
        ],
    )
    save_parts_result(web_db, MODEL, {"συγγράφω": [{"part": 1, "form": "συγγράφω"}]})
    html = _strip(client, text_id, 1, 4)  # πόλεμον: only the Iliad's form
    assert "πόλεμος, -οιο, ὁ" in html
    assert f"{tag('AI')} dictionary form · Homeric</p>" in html
    html = _strip(client, text_id, 1, 2)  # ξυνέγραψε: Homeric parts and construction
    assert f"Principal parts&nbsp;{tag('AI')}</h3>" in html
    assert '<span class="ws-parts-source">Homeric</span>' in html
    # iPad's line
    assert f'pp-summary-label">Principal parts&nbsp;{tag("AI")} · Homeric<' in html
    assert f"Construction&nbsp;{tag('AI')} · Homeric</h3>" in html
    # once Thucydides' own are made, they are shown, unmarked
    save_forms_result(
        web_db,
        [LemmaForms("πόλεμος", MODEL, "noun", "πόλεμος, -ου, ὁ", dialect="attic")],
    )
    save_parts_result(
        web_db, MODEL, {"συγγράφω": [{"part": 1, "form": "ξυγγράφω"}]}, dialect="attic"
    )
    html = _strip(client, text_id, 1, 4)
    assert "πόλεμος, -ου, ὁ" in html and "· Homeric" not in html
    html = _strip(client, text_id, 1, 2)
    assert "ξυγγράφω" in html and "ws-parts-source" not in html
    assert f'pp-summary-label">Principal parts&nbsp;{tag("AI")}</span>' in html


def test_the_iliads_word_pane_shows_no_dialect_mark(client, web_db):
    text_id = glaux._add_text(web_db, glaux.ILIAD_LINES, "Iliad")
    glaux._import(web_db, text_id, glaux.PERSEUS, "iliad")
    save_forms_result(web_db, [LemmaForms("μῆνις", MODEL, "noun", "μῆνις, -ιος, ἡ")])
    html = _strip(client, text_id, 1, 0)
    assert "μῆνις, -ιος, ἡ" in html and "Homeric" not in html


def test_saved_forms_prefer_the_dialect_then_the_model(db):
    save_forms_result(
        db,
        [
            LemmaForms("λόγος", MODEL, "noun", "λόγος, -οιο, ὁ"),
            LemmaForms("λόγος", "gpt-6.1", "noun", "λόγος, -ου, ὁ", dialect="attic"),
        ],
    )
    # the text's dialect by another model beats the study model's Homeric form
    assert get_lemma_forms(db, ["λόγος"], MODEL, "attic")["λόγος"].headword == (
        "λόγος, -ου, ὁ"
    )
    # Ionic has none: the study model's, in another dialect
    assert get_lemma_forms(db, ["λόγος"], MODEL, "ionic")["λόγος"].dialect == "homeric"
    # no dialect: the model's, as before migration 019
    assert get_lemma_forms(db, ["λόγος"], MODEL)["λόγος"].dialect == "homeric"
