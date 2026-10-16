"""`greek study forms` and forms batches in `greek study batch status | collect`,
with a mocked Message Batches client; the forms repository functions."""

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from greek.api.study import MODEL
from greek.cli.study_cmd import study_cmd
from greek.db.repos.lexicon import save_lexicon
from greek.db.repos.parses import lemma_forms_seen, lemmas_with_pos, nominal_lemmas
from greek.db.repos.study import (
    get_lemma_forms,
    get_study_batch,
    lemmas_with_forms,
    list_lemma_requests,
    measured_output_per_lemma,
    pending_forms_lemmas,
    save_forms_result,
)
from greek.models import LemmaForms, LexiconEntry
from tests.integration import test_study_batch as study_batch

# The study batch tests' text with treebank data and fake Message Batches client
iliad = study_batch.iliad
batches = study_batch.batches
no_api = study_batch.no_api
_errored = study_batch._errored

# Nouns and adjectives in the order they first appear: book 1 (lines 1–5),
# then book 2 (lines 6–7). Ἄϊδι (1.3) has no treebank parse.
BOOK_1 = [
    ("μῆνις", "noun"),
    ("θεά", "noun"),
    ("Πηληιάδης", "noun"),
    ("Ἀχιλλεύς", "noun"),
    ("οὐλόμενος", "adjective"),
    ("μυρίος", "adjective"),
    ("Ἀχαιός", "adjective"),
    ("ἄλγος", "noun"),
    ("πολύς", "adjective"),
    ("ἴφθιμος", "adjective"),
    ("ψυχή", "noun"),
]
BOOK_2 = [("ἄλλος", "adjective"), ("θεός", "noun"), ("παννύχιος", "adjective")]
# Verbs, asked for after the nouns and adjectives (ἔρδον, 1.4, has no parse)
VERBS_1 = [
    ("ἀείδω", "verb"),
    ("τίθημι", "verb"),
    ("προϊάπτω", "verb"),
    ("ἐθέλω", "verb"),
]
VERBS_2 = [("εὕδω", "verb")]

MENIS_HTML = (
    '<p><b lang="grc">μῆνις</b>, <span lang="grc">ἡ</span>, gen.</p>'
    '<p data-level="1"><b>A.</b> <span lang="grc">μήνιος</span>:—<em>wrath</em></p>'
)
PELEUS_HTML = '<p><b lang="grc">Πηλεύς</b>, <span lang="grc">ὁ</span>, gen. έως</p>'
PSYCHE_HTML = '<p><b lang="grc">ψυχή</b>, <span lang="grc">ἡ</span>, life</p>'
AEIDO_HTML = '<p><b lang="grc">ἀείδω</b>, Ep. and Ion., sing</p>'


@pytest.fixture
def lexicon(iliad):
    db, text_id = iliad
    save_lexicon(
        db,
        "LSJ",
        [
            LexiconEntry("LSJ", "mh=nis", "μῆνις", "wrath", MENIS_HTML),
            LexiconEntry("LSJ", "*phleu/s", "Πηλεύς", "Peleus", PELEUS_HTML),
            LexiconEntry("LSJ", "yuxh/", "ψυχή", "life", PSYCHE_HTML),
            LexiconEntry("LSJ", "a)ei/dw", "ἀείδω", "sing, chant", AEIDO_HTML),
        ],
        {
            "ἀείδω": ("a)ei/dw", "headword"),
            "μῆνις": ("mh=nis", "headword"),
            "Πηληιάδης": ("*phleu/s", "cross-reference"),
            "ψυχή": ("yuxh/", "headword"),
        },
    )
    return db, text_id


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


def _usage(output=2000):
    return SimpleNamespace(
        input_tokens=600,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens=output,
        cache_creation=None,
    )


def _headword(lemma, pos):
    if lemma == "μῆνις":
        return "μῆνις, -ιος, ὁ"  # the wrong article, for the LSJ check
    if lemma == "ψυχή":
        return "ψυχή, -ῆς, ἡ"
    if lemma == "Πηληιάδης":
        return "Πηληϊάδης, -εω, ὁ, ἡ"  # both genders agree with LSJ's ὁ
    return f"{lemma}, -ου, ὁ" if pos == "noun" else f"{lemma}, -η, -ον"


def _construction(lemma):
    return {"ἀείδω": "+ acc. of the song", "ἐθέλω": "+ inf."}.get(lemma, "+ acc.")


def _answer(lemmas, skip=(), no_construction=()):
    verb = {lemma for lemma, pos in lemmas if pos == "verb"}
    return json.dumps(
        {
            "lemmas": [
                {
                    "lemma": lemma,
                    "headword": "" if lemma in verb else _headword(lemma, pos),
                    "forms": "epic gen. -οιο" if lemma == "πολύς" else "",
                    "comparison": "πλείων, πλεῖστος" if lemma == "πολύς" else "",
                    "construction": _construction(lemma)
                    if lemma in verb and lemma not in no_construction
                    else "",
                }
                for lemma, pos in lemmas
                if lemma not in skip
            ]
        },
        ensure_ascii=False,
    )


def _succeeded(cid, text, output=2000):
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=_usage(output),
    )
    return SimpleNamespace(
        custom_id=cid, result=SimpleNamespace(type="succeeded", message=message)
    )


def _submit(run_cli, text_id, *args):
    result = run_cli("forms", "submit", text_id, *args, "--yes")
    assert result.exit_code == 0, result.output
    return result


def _asked(fake, n=0):
    """The lemma lines of each request of the nth batch created."""
    return [
        r["params"]["messages"][0]["content"].splitlines()[2:] for r in fake.created[n]
    ]


# --- Lemma selection --------------------------------------------------------------


def test_lemmas_with_pos_take_verbs_too(iliad):
    db, text_id = iliad
    assert lemmas_with_pos(db, text_id, [(1, 1)]) == [
        ("μῆνις", "noun"),
        ("ἀείδω", "verb"),
        ("θεά", "noun"),
        ("Πηληιάδης", "noun"),
        ("Ἀχιλλεύς", "noun"),
    ]
    assert lemmas_with_pos(db, text_id, [(6, 7)], "v") == VERBS_2
    # a lemma is counted under the part of speech it has most often
    db.execute(
        "UPDATE word_parses SET postag='v-sapamn-' WHERE text_id=? AND lemma='μυρίος'",
        (text_id,),
    )
    assert dict(lemmas_with_pos(db, text_id, [(1, 7)]))["μυρίος"] == "verb"
    assert "μυρίος" not in dict(nominal_lemmas(db, text_id, [(1, 7)]))


def test_nominal_lemmas_in_order_with_their_usual_part_of_speech(iliad):
    db, text_id = iliad
    assert nominal_lemmas(db, text_id, [(1, 7)]) == BOOK_1 + BOOK_2
    assert nominal_lemmas(db, text_id, [(6, 7), (1, 1)]) == BOOK_1[:4] + BOOK_2
    # a lemma tagged more often as a noun than an adjective reads as a noun
    db.execute(
        """UPDATE word_parses SET postag='n-p---md-' WHERE text_id=?
           AND lemma='Ἀχαιός'""",
        (text_id,),
    )
    assert dict(nominal_lemmas(db, text_id, [(1, 7)]))["Ἀχαιός"] == "noun"
    assert nominal_lemmas(db, text_id, [(4, 4)]) == []


def test_forms_seen_are_printed_forms_most_frequent_first(iliad):
    db, text_id = iliad
    seen = lemma_forms_seen(db, text_id, ["μῆνις", "μυρίος", "δέ", "Ἀχιλλεύς"])
    assert seen["μῆνις"] == ["μῆνιν"]  # lower case: not a name
    assert seen["μυρίος"] == ["μυρίʼ"]  # as printed, with the app's elision mark
    assert seen["Ἀχιλλεύς"] == ["Ἀχιλῆος"]
    assert seen["δέ"] == ["δʼ"]  # οὐδʼ (split) is left out


# --- Estimate and submit ----------------------------------------------------------


def test_estimate_is_local(lexicon, run_cli, no_api):
    _, text_id = lexicon
    result = run_cli("forms", "estimate", text_id, "--lines", "1.1-1.2,2.1-2.2")
    assert result.exit_code == 0, result.output
    assert "Iliad 1.1–1.2, 2.1–2.2: dictionary forms of 11 lemmas" in result.text
    assert "(6 nouns, 5 adjectives) and constructions of 3 verbs" in result.text
    assert "Requests (25 lemmas each) │ 1" in result.text
    assert "Lemmas │ 14" in result.text and "verbs │ 3" in result.text
    assert "without an LSJ entry │ 11" in result.text  # ἀείδω has a meaning
    assert "Output tokens (estimate, 250 per lemma) │ 3,500 │ $0.04" in result.text


def test_estimate_show_prints_the_requests(lexicon, run_cli, no_api):
    _, text_id = lexicon
    result = run_cli("forms", "estimate", text_id, "--books", "1", "--show")
    assert result.exit_code == 0, result.output
    assert "12_forms_1" not in result.output  # the fixture's text has ID 1
    assert "1_forms_1" in result.output
    assert "1 μῆνις (noun) | LSJ: μῆνις, ἡ, gen. μήνιος:— wrath | Iliad: μῆνιν" in (
        result.output
    )
    assert "LSJ, under Πηλεύς: Πηλεύς, ὁ, gen. έως" in result.output
    assert "2 θεά (noun) | not in LSJ | Iliad: θεὰ" not in result.output
    assert "2 θεά (noun) | not in LSJ | Iliad: θεά" in result.output  # acute
    # verbs come last, with LSJ's short definition rather than its entry
    assert "12 ἀείδω (verb) | LSJ meaning: sing, chant | Iliad: ἄειδε" in (
        result.output
    )
    assert "13 τίθημι (verb) | not in LSJ | Iliad: ἔθηκε" in result.output


def test_submit_needs_yes(lexicon, run_cli, no_api, db):
    _, text_id = lexicon
    result = run_cli("forms", "submit", text_id, "--books", "1")
    assert result.exit_code == 0, result.output
    assert "Nothing submitted" in result.text
    assert db.execute("SELECT count(*) FROM study_batches").fetchone()[0] == 0


def test_submit_records_the_batch_and_its_lemmas(lexicon, batches, run_cli, db):
    _, text_id = lexicon
    result = _submit(run_cli, text_id, "--books", "1")
    assert "Submitted batch msgbatch_01: 1 requests" in result.text
    batch = get_study_batch(db, "msgbatch_01")
    assert (batch.kind, batch.model, batch.request_count) == ("forms", MODEL, 1)
    assert "dictionary forms of 11 lemmas (6 nouns, 5 adjectives)" in batch.scope
    assert "and constructions of 4 verbs" in batch.scope
    [request] = list_lemma_requests(db, "msgbatch_01")
    assert request.lemmas == [lemma for lemma, _ in BOOK_1 + VERBS_1]
    assert (request.custom_id, request.status) == (f"{text_id}_forms_1", "pending")
    assert len(_asked(batches)[0]) == 15
    assert pending_forms_lemmas(db, "homeric", MODEL) == {
        lemma for lemma, _ in BOOK_1 + VERBS_1
    }
    system = batches.created[0][0]["params"]["system"][0]["text"]
    assert '"construction": for a verb' in system


def test_lemmas_saved_or_waiting_are_left_out(lexicon, batches, run_cli, db):
    _, text_id = lexicon
    save_forms_result(db, [LemmaForms("ἄλλος", MODEL, "adjective", "ἄλλος, -η, -ο")])
    _submit(run_cli, text_id, "--books", "1")
    result = run_cli("forms", "estimate", text_id)
    # θεός, παννύχιος; εὕδω
    assert "dictionary forms of 2 lemmas" in result.text
    assert "constructions of 1 verbs" in result.text
    assert "Left out: 1 lemmas with a saved Homeric form or construction" in result.text
    assert "15 waiting in a batch not yet collected" in result.text
    result = run_cli("forms", "estimate", text_id, "--redo")
    assert "dictionary forms of 14 lemmas" in result.text
    assert "constructions of 5 verbs" in result.text


def test_options_are_exclusive_and_checked(lexicon, run_cli, no_api):
    _, text_id = lexicon
    result = run_cli("forms", "estimate", text_id, "--books", "1", "--lines", "1.1")
    assert result.exit_code == 1 and "Use only one of" in result.text
    assert run_cli("forms", "estimate", 999).exit_code == 1
    result = run_cli("forms", "estimate", text_id, "--retry", "msgbatch_none")
    assert result.exit_code == 1 and "No dictionary-forms batch" in result.text


# --- Collect --------------------------------------------------------------------------


def test_collect_stores_forms_and_checks_articles_against_lsj(
    lexicon, batches, db, run_cli, tmp_path
):
    _, text_id = lexicon
    _submit(run_cli, text_id, "--books", "1")
    batches.end(
        "msgbatch_01", [_succeeded(f"{text_id}_forms_1", _answer(BOOK_1 + VERBS_1))]
    )
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Requests fully stored │ 1" in result.text
    assert "Lemmas stored │ 15" in result.text
    assert "with a construction │ 4" in result.text
    assert "Nouns whose article agrees with LSJ │ 2" in result.text  # ψυχή, Πηλεύς ὁ
    assert "Nouns whose article differs from LSJ │ 1" in result.text
    assert "μῆνις: μῆνις, -ιος, ὁ (LSJ ἡ)" in result.text
    assert "Per lemma asked for: 133 output tokens" in result.text

    forms = get_lemma_forms(db, ["πολύς", "μῆνις", "λόγος"], MODEL)
    assert set(forms) == {"πολύς", "μῆνις"}
    assert forms["πολύς"] == LemmaForms(
        "πολύς",
        MODEL,
        "adjective",
        "πολύς, -η, -ον",
        "epic gen. -οιο",
        "πλείων, πλεῖστος",
        None,
    )
    # a verb: its construction, with its lemma as the headword
    verbs = get_lemma_forms(db, ["ἀείδω", "ἐθέλω"], MODEL)
    assert verbs["ἀείδω"] == LemmaForms(
        "ἀείδω", MODEL, "verb", "ἀείδω", "", "", "+ acc. of the song"
    )
    assert verbs["ἐθέλω"].construction == "+ inf."
    # a noun's empty construction is not stored as asked for
    assert get_lemma_forms(db, ["ψυχή"], MODEL)["ψυχή"].construction is None
    batch = get_study_batch(db, "msgbatch_01")
    assert batch.collected_at and batch.output_tokens == 2000
    assert measured_output_per_lemma(db, MODEL) == pytest.approx(2000 / 15)

    review = json.loads((tmp_path / "forms-msgbatch_01.json").read_text())
    by_lemma = {item["lemma"]: item for item in review["lemmas"]}
    assert [item["lemma"] for item in review["lemmas"]] == [
        lemma for lemma, _ in BOOK_1 + VERBS_1
    ]
    assert by_lemma["ἀείδω"]["construction"] == "+ acc. of the song"
    assert by_lemma["ἀείδω"]["meaning"] == "sing, chant"
    assert by_lemma["ἀείδω"]["gender_check"] is None
    assert by_lemma["μῆνις"]["gender_check"] == "differs"
    assert by_lemma["μῆνις"]["lsj_article"] == "ἡ"
    assert by_lemma["μῆνις"]["seen"] == ["μῆνιν"]
    assert by_lemma["θεά"]["gender_check"] == "no article in LSJ"
    assert by_lemma["πολύς"]["gender_check"] is None  # an adjective


def test_a_missing_lemma_fails_alone_and_can_be_retried(lexicon, batches, db, run_cli):
    _, text_id = lexicon
    _submit(run_cli, text_id, "--books", "1")
    answer = _answer(BOOK_1 + VERBS_1, skip={"θεά"}, no_construction={"ἐθέλω"})
    batches.end("msgbatch_01", [_succeeded(f"{text_id}_forms_1", answer)])
    result = run_cli("batch", "collect", "msgbatch_01")
    assert "Lemmas stored │ 13" in result.text
    assert "Lemmas failed │ 2" in result.text
    assert (
        "2 of 15 lemmas not stored: θεά: not in the output; ἐθέλω: no construction"
        in result.text
    )
    [request] = list_lemma_requests(db, "msgbatch_01")
    assert (request.status, request.stored_count) == ("failed", 13)
    assert lemmas_with_forms(db, ["θεά", "μῆνις", "ἐθέλω"], MODEL, "homeric") == {
        "μῆνις"
    }

    result = _submit(run_cli, text_id, "--retry", "msgbatch_01")
    assert "Iliad, retry of msgbatch_01: dictionary forms of 1 lemmas" in result.text
    assert _asked(batches, 1) == [
        [
            "1 θεά (noun) | not in LSJ | Iliad: θεά",
            "2 ἐθέλω (verb) | not in LSJ | Iliad: ἔθελον",
        ]
    ]


def test_an_errored_request_stores_nothing(lexicon, batches, db, run_cli):
    _, text_id = lexicon
    _submit(run_cli, text_id, "--books", "1")
    batches.end("msgbatch_01", [_errored(f"{text_id}_forms_1")])
    result = run_cli("batch", "collect", "msgbatch_01")
    assert result.exit_code == 0, result.output
    assert "Lemmas failed │ 15" in result.text
    assert "error: api_error: Internal server error" in result.text
    [request] = list_lemma_requests(db, "msgbatch_01")
    assert request.status == "failed"
    assert get_lemma_forms(db, ["μῆνις"], MODEL) == {}
    assert pending_forms_lemmas(db, "homeric", MODEL) == set()


def test_collecting_again_replaces_and_keeps_a_construction(
    lexicon, batches, db, run_cli
):
    _, text_id = lexicon
    save_forms_result(
        db, [LemmaForms("ψυχή", MODEL, "noun", "old", construction="+ gen.")]
    )
    _submit(run_cli, text_id, "--books", "1", "--redo")
    batches.end("msgbatch_01", [_succeeded(f"{text_id}_forms_1", _answer(BOOK_1))])
    run_cli("batch", "collect", "msgbatch_01")
    run_cli("batch", "collect", "msgbatch_01")
    saved = get_lemma_forms(db, ["ψυχή"], MODEL)["ψυχή"]
    assert (saved.headword, saved.construction) == ("ψυχή, -ῆς, ἡ", "+ gen.")


def test_the_reader_prefers_the_study_models_form(iliad, db):
    save_forms_result(
        db,
        [
            LemmaForms("μῆνις", "other-model", "noun", "μῆνις, -ιδος, ἡ"),
            LemmaForms("μῆνις", MODEL, "noun", "μῆνις, -ιος, ἡ"),
            LemmaForms("ψυχή", "other-model", "noun", "ψυχή, -ῆς, ἡ"),
        ],
    )
    forms = get_lemma_forms(db, ["μῆνις", "ψυχή"], MODEL)
    assert forms["μῆνις"].headword == "μῆνις, -ιος, ἡ"
    assert forms["ψυχή"].model_used == "other-model"
