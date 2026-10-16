"""Dictionary-forms batch requests and results (greek/api/forms.py)."""

import json
from types import SimpleNamespace

import pytest

from greek.api.forms import (
    FORMS_SCHEMA,
    FORMS_SYSTEM_PROMPT,
    FormsLemma,
    build_forms_prompt,
    build_forms_request,
    check_forms_output,
    estimate_forms,
    forms_custom_id,
    group_lemmas,
    headword_articles,
    lsj_article,
    lsj_start,
    read_forms_result,
)
from greek.api.study import MODEL
from greek.api.works import ILIAD

MENIS_HTML = (
    '<p><b lang="grc">μῆνις</b>, <i>Dor.</i> and <i>Aeol.</i> '
    '<b lang="grc">μᾶν-</b>, <span lang="grc">ἡ</span>, gen.</p>\n'
    '<p data-level="1"><b>A.</b> <span lang="grc">μήνιος</span> '
    '<cite>Pl. R. 390e</cite> , later <span lang="grc">μήνιδος</span> '
    "<cite>Ael. Fr. 80</cite> (<cite>Pall.</cite>):—<em>wrath</em>; from "
    "<cite>Hom.</cite> downwds. freq. of the <em>wrath</em> of the gods, "
    "<cite>Il. 5.34</cite>, al., <cite>A. Ag. 701</cite> (lyr.), <cite>Pl. Lg. "
    "880e</cite></p>"
)
ALGOS_HTML = (
    '<p><b lang="grc">ἄλγος</b>, <span lang="grc">εος</span>, '
    '<span lang="grc">τό</span>, (<cite>Cypr.</cite>,</p>'
)
KYON_HTML = (
    '<p><b lang="grc">κύων</b> [<span lang="grc">ῠ</span>], <span lang="grc">ὁ</span>'
    ' and <span lang="grc">ἡ</span>, both in <cite>Hom.</cite></p>'
)


def _lemma(name, **extra):
    return FormsLemma(lemma=name, pos=extra.pop("pos", "noun"), **extra)


# --- LSJ ----------------------------------------------------------------------------


def test_lsj_start_is_plain_text_cut_at_a_word():
    text = lsj_start(MENIS_HTML)
    # the sense label "A." between "gen." and μήνιος is dropped; "(Pall.)" is tidied
    assert text.startswith("μῆνις, Dor. and Aeol. μᾶν-, ἡ, gen. μήνιος Pl. R. 390e,")
    assert "(Pall.)" in text
    assert text.endswith("Pl. Lg. 880e")  # under 200 characters: whole
    short = lsj_start(MENIS_HTML, chars=60)
    assert short == "μῆνις, Dor. and Aeol. μᾶν-, ἡ, gen. μήνιος Pl. R. 390e …"
    assert lsj_start(ALGOS_HTML) == "ἄλγος, εος, τό, (Cypr.,"


def test_lsj_article():
    assert lsj_article(MENIS_HTML) == "ἡ"
    assert lsj_article(ALGOS_HTML) == "τό"
    assert lsj_article(KYON_HTML) is None  # ὁ and ἡ
    assert lsj_article('<p><b lang="grc">Ἀχιλλεύς</b>, gen.</p>') is None


def test_headword_articles():
    assert headword_articles("μῆνις, -ιος, ἡ") == ("ἡ",)
    assert headword_articles("κῆρ, κῆρος, τό") == ("τό",)
    assert headword_articles("Ἀχαιοί, -ῶν, οἱ") == ("ὁ",)
    assert headword_articles("κύων, κυνός, ὁ, ἡ") == ("ὁ", "ἡ")
    assert headword_articles("θίς, θινός, ὁ/ἡ") == ("ὁ", "ἡ")
    assert headword_articles("Τρώς, Τρωός, ὁ (pl. Τρῶες, -ων, οἱ)") == ("ὁ",)
    assert headword_articles("οὐλόμενος, -η, -ον") == ()
    assert headword_articles("μῆνις") == ()


# --- Requests -------------------------------------------------------------------------


def test_lemmas_are_grouped_evenly():
    lemmas = [_lemma(str(n)) for n in range(127)]
    groups = group_lemmas(lemmas)
    assert [len(g) for g in groups] == [22, 21, 21, 21, 21, 21]
    assert [x for g in groups for x in g] == lemmas
    assert [len(g) for g in group_lemmas(lemmas[:25])] == [25]
    assert [len(g) for g in group_lemmas(lemmas[:26])] == [13, 13]
    assert group_lemmas([]) == []


def test_prompt_lists_each_lemma_with_its_lsj_start_and_forms():
    prompt = build_forms_prompt(
        [
            _lemma("μῆνις", lsj="μῆνις, ἡ, gen. μήνιος", forms=["μῆνιν", "μῆνις"]),
            _lemma(
                "Πηληιάδης",
                lsj="Πηλεύς, ὁ, gen. έως",
                lsj_headword="Πηλεύς",
                forms=["Πηληϊάδεω"],
            ),
            _lemma("Σμινθεύς", identification="epithet of Apollo"),
            _lemma("ἄγνωστος", pos="adjective"),
            _lemma("ἀκούω", pos="verb", meaning="hear", forms=["ἄκουσε"]),
        ]
    )
    assert prompt.splitlines() == [
        'Dictionary forms of 5 lemmas. Give "lemmas" 5 objects, in this order.',
        "",
        "1 μῆνις (noun) | LSJ: μῆνις, ἡ, gen. μήνιος | Iliad: μῆνιν, μῆνις",
        "2 Πηληιάδης (noun) | LSJ, under Πηλεύς: Πηλεύς, ὁ, gen. έως | Iliad: Πηληϊάδεω",
        "3 Σμινθεύς (noun) | not in LSJ; identified as: epithet of Apollo",
        "4 ἄγνωστος (adjective) | not in LSJ",
        "5 ἀκούω (verb) | LSJ meaning: hear | Iliad: ἄκουσε",
    ]


def test_request_shape():
    request = build_forms_request(ILIAD, forms_custom_id(12, 3), [_lemma("μῆνις")])
    assert request["custom_id"] == "12_forms_3"
    params = request["params"]
    assert params["model"] == MODEL
    assert params["max_tokens"] == 32000
    assert params["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": FORMS_SCHEMA},
    }
    assert params["system"] == [
        {
            "type": "text",
            "text": FORMS_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]
    assert params["messages"][0]["content"].startswith("Dictionary forms of 1 lemmas")


def test_estimate_prices_input_and_output():
    requests = [build_forms_request(ILIAD, "12_forms_1", [_lemma("μῆνις")])]
    est = estimate_forms(requests, 1, output_per_lemma=250)
    assert est.output_tokens == 250
    assert est.output_cost == pytest.approx(250 * 10 / 1_000_000)
    assert est.input_tokens > 400  # the system prompt and schema
    assert est.cost == pytest.approx(est.input_cost + est.output_cost)


# --- Results ----------------------------------------------------------------------------


def _item(lemma, headword="h", forms="", comparison="", construction=""):
    return {
        "lemma": lemma,
        "headword": headword,
        "forms": forms,
        "comparison": comparison,
        "construction": construction,
    }


def test_output_is_matched_by_the_repeated_lemma():
    found, failed = check_forms_output(
        {
            "lemmas": [
                _item("οὐλόμενος", "οὐλόμενος, -η, -ον", "epic for ὀλόμενος"),
                _item("μῆνις", " μῆνις, -ιος, ἡ "),
            ]
        },
        ["μῆνις", "οὐλόμενος", "ψυχή"],
    )
    assert found["μῆνις"].headword == "μῆνις, -ιος, ἡ"
    assert found["οὐλόμενος"].forms == "epic for ὀλόμενος"
    assert failed == {"ψυχή": "not in the output"}


def test_a_lemma_repeated_with_other_accents_is_found():
    found, failed = check_forms_output(
        {"lemmas": [_item("Ἀΐδης", "Ἀΐδης, -αο, ὁ")]}, ["Ἀίδης"]
    )
    assert found["Ἀίδης"].headword == "Ἀΐδης, -αο, ὁ" and not failed


def test_a_lemma_without_a_headword_fails_alone():
    found, failed = check_forms_output(
        {"lemmas": [_item("μῆνις", ""), _item("ψυχή", "ψυχή, -ῆς, ἡ")]},
        ["μῆνις", "ψυχή"],
    )
    assert list(found) == ["ψυχή"] and failed == {"μῆνις": "no headword"}


def test_a_verb_needs_a_construction_and_no_headword():
    found, failed = check_forms_output(
        {
            "lemmas": [
                _item("ἀκούω", "", construction="+ gen. of the person heard"),
                _item("φημί", "φημί", construction=""),
                _item("μῆνις", "μῆνις, -ιος, ἡ"),
            ]
        },
        ["ἀκούω", "φημί", "μῆνις"],
        verbs={"ἀκούω", "φημί"},
    )
    assert found["ἀκούω"].construction == "+ gen. of the person heard"
    assert found["ἀκούω"].headword == ""
    assert found["μῆνις"].construction == ""
    assert failed == {"φημί": "no construction"}


def test_the_schema_and_prompt_ask_for_a_verbs_construction():
    item = FORMS_SCHEMA["properties"]["lemmas"]["items"]
    assert "construction" in item["required"]
    assert '"construction": for a verb' in FORMS_SYSTEM_PROMPT
    # tightened after Pilot 2: one format, at most three, no forms; and
    # after Pilot 1: remarks out of the headword, comparison only if irregular
    assert "at most three" in FORMS_SYSTEM_PROMPT
    assert "a comma, and the sense" in FORMS_SYSTEM_PROMPT
    assert "no forms, tenses or quotations" in FORMS_SYSTEM_PROMPT
    assert "not in brackets after it" in FORMS_SYSTEM_PROMPT
    assert "whose comparison is irregular" in FORMS_SYSTEM_PROMPT


def test_output_without_a_list_raises():
    with pytest.raises(ValueError):
        check_forms_output({"verbs": []}, ["μῆνις"])


def _entry(text, stop_reason="end_turn"):
    usage = SimpleNamespace(
        input_tokens=500,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens=1000,
        cache_creation=None,
    )
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=usage,
    )
    return SimpleNamespace(
        custom_id="12_forms_1",
        result=SimpleNamespace(type="succeeded", message=message),
    )


def test_read_result_success_and_usage():
    outcome = read_forms_result(
        _entry(json.dumps({"lemmas": [_item("μῆνις", "μῆνις, -ιος, ἡ")]})),
        ["μῆνις", "ψυχή"],
    )
    assert outcome.failure is None
    assert list(outcome.lemmas) == ["μῆνις"]
    assert outcome.summary == "1 of 2 lemmas not stored: ψυχή: not in the output"
    assert outcome.usage.cost_usd == pytest.approx((500 * 2 + 1000 * 10) / 1e6)


@pytest.mark.parametrize(
    "entry, failure",
    [
        (_entry("not json"), "the output is not valid JSON"),
        (_entry("{}", "max_tokens"), "truncated at max_tokens"),
        (_entry("{}", "refusal"), "refusal"),
        (_entry("{}"), "the output has no list of lemmas"),
    ],
)
def test_read_result_failures(entry, failure):
    outcome = read_forms_result(entry, ["μῆνις"])
    assert outcome.failure == failure
    assert outcome.usage.output_tokens == 1000  # still billed


def test_an_errored_or_expired_request_fails():
    error = SimpleNamespace(type="api_error", message="Internal server error")
    errored = SimpleNamespace(
        custom_id="12_forms_1",
        result=SimpleNamespace(
            type="errored", error=SimpleNamespace(type="error", error=error)
        ),
    )
    assert read_forms_result(errored, ["μῆνις"]).failure == (
        "error: api_error: Internal server error"
    )
    expired = SimpleNamespace(
        custom_id="12_forms_1", result=SimpleNamespace(type="expired")
    )
    assert read_forms_result(expired, ["μῆνις"]).failure == "expired"
