"""Batch requests for study data (greek/api/study.py): units, prompts, results, costs."""

import json
from types import SimpleNamespace

import pytest

from greek.api.study import (
    BATCH_PRICES,
    EFFORT,
    MAX_TOKENS,
    MODEL,
    NOTE_RULE,
    NOTES_SCHEMA,
    NOTES_SYSTEM_PROMPT,
    STUDY_SCHEMA,
    STUDY_SYSTEM_PROMPT,
    StudyWord,
    build_notes_prompt,
    build_notes_request,
    build_request,
    build_study_prompt,
    check_notes,
    check_output,
    custom_id,
    estimate,
    estimate_tokens,
    first_in_book,
    group_units,
    parse_custom_id,
    parse_label,
    read_notes_result,
    read_result,
    same_word,
    study_words,
    usage_of,
)
from greek.api.works import ILIAD
from greek.models import Line, TranslationUnit, TreebankSentence, WordParse
from greek.postag import VALUES, decode, encode


def _lines(refs):
    return [
        Line(id=n, text_id=1, line_number=n, content=f"w{n}", reference=ref)
        for n, ref in enumerate(refs, 1)
    ]


def _book(book, count, start=1):
    return [f"{book}.{i}" for i in range(start, start + count)]


def _sentences(*spans):
    return [TreebankSentence(1, i, a, b) for i, (a, b) in enumerate(spans)]


def _spans(units):
    return [(u.first_line, u.last_line) for u in units]


# --- Translation units --------------------------------------------------------


def test_sentences_sharing_a_line_are_merged():
    lines = _lines(_book(1, 6))
    sentences = _sentences((1, 2), (2, 3), (4, 6))
    # 1–2 and 2–3 share line 2, so they stay together even in 2-line units
    assert _spans(group_units(1, lines, sentences, max_lines=2)) == [(1, 3), (4, 6)]
    assert _spans(group_units(1, lines, sentences)) == [(1, 6)]


def test_consecutive_sentences_are_grouped_up_to_ten_lines():
    sentences = _sentences((1, 4), (5, 8), (9, 12), (13, 14))
    assert _spans(group_units(1, _lines(_book(1, 14)), sentences)) == [
        (1, 8),
        (9, 14),
    ]


def test_a_sentence_longer_than_ten_lines_is_a_unit_of_its_own():
    sentences = _sentences((1, 3), (4, 15), (16, 18))
    assert _spans(group_units(1, _lines(_book(1, 18)), sentences)) == [
        (1, 3),
        (4, 15),
        (16, 18),
    ]


def test_lines_without_a_sentence_join_the_preceding_one():
    sentences = _sentences((1, 3), (6, 7))
    units = group_units(1, _lines(_book(1, 7)), sentences, max_lines=5)
    assert _spans(units) == [(1, 5), (6, 7)]


def test_lines_without_a_sentence_at_a_book_start_join_the_following_one():
    lines = _lines(_book(1, 3) + _book(2, 4))
    units = group_units(1, lines, _sentences((1, 3), (6, 7)), max_lines=3)
    assert _spans(units) == [(1, 3), (4, 7)]


def test_units_do_not_cross_books():
    lines = _lines(_book(1, 3) + _book(2, 3))
    assert _spans(group_units(1, lines, _sentences((1, 3), (4, 6)))) == [
        (1, 3),
        (4, 6),
    ]


def test_a_book_without_sentences_is_cut_into_units():
    lines = _lines(_book(1, 2) + _book(2, 12))
    units = group_units(1, lines, _sentences((1, 2)))
    assert _spans(units) == [(1, 2), (3, 12), (13, 14)]


def test_every_line_belongs_to_exactly_one_unit():
    lines = _lines(_book(1, 40) + _book(2, 25))
    sentences = _sentences(
        (2, 4), (4, 5), (7, 19), (20, 20), (22, 30), (31, 33), (41, 44), (50, 52)
    )
    units = group_units(1, lines, sentences)
    covered = [n for u in units for n in range(u.first_line, u.last_line + 1)]
    assert covered == list(range(1, 66))
    assert all(u.text_id == 1 and u.fluent is None for u in units)


def test_custom_ids_name_the_text_and_lines():
    unit = TranslationUnit(12, 1, 7)
    assert custom_id(unit) == "12_1-7"
    assert parse_custom_id("12_1-7") == (12, 1, 7)
    assert parse_custom_id(" 12_1-7\n") == (12, 1, 7)
    for bad in ("12:1-7", "12_7-1", "x_1-2", ""):
        assert parse_custom_id(bad) is None


# --- Postags --------------------------------------------------------------------


@pytest.mark.parametrize(
    "postag",
    ["v3saia---", "n-s---fa-", "v-sapamn-", "v--pna---", "a-p---mnc", "g--------"],
)
def test_encode_is_the_inverse_of_decode(postag):
    assert encode(*decode(postag)) == postag


def test_encode_participles_infinitives_and_unknown_values():
    assert encode("participle", {"tense": "aorist", "case": "nominative"}) == (
        "v--ap--n-"
    )
    assert encode("infinitive", {"tense": "present", "voice": "active"}) == "v--pna---"
    assert encode("noun", {"case": "ablative", "number": "singular"}) == "n-s------"
    assert encode("gerund", {}) is None


def test_parse_labels_read_like_grammar():
    assert parse_label("v3saia---") == "verb 3rd singular aorist indicative active"
    assert parse_label("n-s---fa-") == "noun singular feminine accusative"
    assert parse_label("v-pppmmn-").startswith("participle plural present")
    assert "participle" not in parse_label("v-pppmmn-").split(" ", 1)[1]


# --- Words, prompts and requests ------------------------------------------------


def _parse(n, i, form, lemma, postag, part=0):
    return WordParse(1, n, i, part, form, lemma, postag, 1 if lemma else None)


LINES = [
    Line(id=1, text_id=1, line_number=1, content="μῆνιν ἄειδε, θεὰ", reference="1.1"),
    Line(id=2, text_id=1, line_number=2, content="οὐδʼ Ἄϊδι", reference="1.2"),
]
PARSES = [
    _parse(1, 0, "μῆνιν", "μῆνις", "n-s---fa-"),
    _parse(1, 1, "ἄειδε", "ἀείδω", "v2spma---"),
    _parse(1, 2, "θεὰ", "θεά", "n-s---fv-"),
    _parse(2, 0, "οὐ", "οὐ", "d--------"),
    _parse(2, 0, "δ̓", "δέ", "g--------", part=1),
    _parse(2, 1, "Ἄϊδι", None, None),
]


def test_study_words_follow_the_tokens_of_each_line():
    words = study_words(LINES, PARSES)
    assert [(w.reference, w.token_index, w.form) for w in words] == [
        ("1.1", 0, "μῆνιν"),
        ("1.1", 1, "ἄειδε"),
        ("1.1", 2, "θεὰ"),
        ("1.2", 0, "οὐδʼ"),
        ("1.2", 1, "Ἄϊδι"),
    ]
    assert [len(w.parts) for w in words] == [1, 1, 1, 2, 1]
    assert [w.needs_parse for w in words] == [False, False, False, False, True]
    # a word with no treebank row at all needs a parse too
    assert StudyWord(1, 0, "1.1", "σὺ").needs_parse


def test_prompt_lists_lines_then_numbered_words_with_parses():
    prompt = build_study_prompt(ILIAD, LINES, study_words(LINES, PARSES))
    assert prompt.splitlines()[0] == (
        'Iliad 1.1–1.2: 2 lines, 5 words. Give "literal" 2 strings and "words" '
        "5 objects."
    )
    assert "1.1 μῆνιν ἄειδε, θεὰ\n1.2 οὐδʼ Ἄϊδι" in prompt
    assert prompt.endswith(
        "[1.1]\n"
        "1 μῆνιν: μῆνις, noun singular feminine accusative\n"
        "2 ἄειδε: ἀείδω, verb 2nd singular present imperative active\n"
        "3 θεὰ: θεά, noun singular feminine vocative\n"
        "[1.2]\n"
        "4 οὐδʼ: οὐ, adverb + δέ, particle\n"
        "5 Ἄϊδι: no treebank parse"
    )


def test_request_settings():
    unit = TranslationUnit(12, 1, 2)
    request = build_request(ILIAD, unit, LINES, study_words(LINES, PARSES))
    assert request["custom_id"] == "12_1-2"
    params = request["params"]
    assert params["model"] == MODEL == "claude-opus-5-5"
    assert params["max_tokens"] == MAX_TOKENS == 32000
    assert params["output_config"]["effort"] == EFFORT == "medium"
    assert params["output_config"]["format"] == {
        "type": "json_schema",
        "schema": STUDY_SCHEMA,
    }
    [system] = params["system"]
    assert system["text"] == STUDY_SYSTEM_PROMPT
    assert system["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert params["messages"] == [
        {
            "role": "user",
            "content": build_study_prompt(ILIAD, LINES, study_words(LINES, PARSES)),
        }
    ]
    # Opus 5.5 rejects disabled thinking, and Batches reject fallbacks
    assert "thinking" not in params and "fallbacks" not in params
    high = build_request(ILIAD, unit, LINES, study_words(LINES, PARSES), "high")
    assert high["params"]["output_config"]["effort"] == "high"
    assert high["params"]["messages"] == params["messages"]


def _objects(schema):
    if schema.get("type") == "object":
        yield schema
        for sub in schema["properties"].values():
            yield from _objects(sub)
    elif schema.get("type") == "array":
        yield from _objects(schema["items"])


def test_schema_suits_structured_outputs():
    for obj in _objects(STUDY_SCHEMA):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) <= set(obj["properties"])
    word = STUDY_SCHEMA["properties"]["words"]["items"]
    assert word["required"] == ["gloss", "note"]
    parse = word["properties"]["parse"]
    assert parse["properties"]["pos"]["enum"] == VALUES["pos"]
    assert parse["properties"]["case"]["enum"][0] == "-"
    json.dumps(STUDY_SCHEMA)  # serializable, so the request is


# --- Estimates and costs -------------------------------------------------------


def test_token_estimate_counts_greek_and_other_characters():
    assert estimate_tokens("") == 0
    assert estimate_tokens("αβγδεζηθικ") == 13  # 1.3 per Greek character
    assert estimate_tokens("a" * 27) == 10  # 2.7 characters per token


def test_estimate_prices_input_and_output_at_batch_rates():
    request = build_request(
        ILIAD, TranslationUnit(1, 1, 2), LINES, study_words(LINES, PARSES)
    )
    est = estimate([request, request], line_count=4, word_count=10, output_per_word=80)
    assert (est.requests, est.lines, est.words, est.output_tokens) == (2, 4, 10, 800)
    assert est.input_tokens > 2 * estimate_tokens(STUDY_SYSTEM_PROMPT)
    assert est.output_cost == pytest.approx(800 * 10 / 1e6)
    assert est.input_cost == pytest.approx(est.input_tokens * 2 / 1e6)
    assert est.cost == pytest.approx(est.input_cost + est.output_cost)


def _usage(input_tokens=1000, write=0, read=0, output=2000, breakdown=None):
    return SimpleNamespace(
        input_tokens=input_tokens,
        cache_creation_input_tokens=write,
        cache_read_input_tokens=read,
        output_tokens=output,
        cache_creation=breakdown,
    )


def test_usage_cost_at_batch_prices():
    prices = BATCH_PRICES[MODEL]
    usage = usage_of(_usage(1000, 0, 500, 2000))
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (
        1000,
        500,
        2000,
    )
    assert usage.cost_usd == pytest.approx(
        (1000 * prices["input"] + 500 * prices["cache_read"] + 2000 * prices["output"])
        / 1e6
    )
    one_hour = usage_of(_usage(0, 800, 0, 0))
    assert one_hour.cost_usd == pytest.approx(800 * prices["cache_write_1h"] / 1e6)
    split = usage_of(
        _usage(
            0,
            800,
            0,
            0,
            SimpleNamespace(
                ephemeral_5m_input_tokens=300, ephemeral_1h_input_tokens=500
            ),
        )
    )
    assert split.cache_write_tokens == 800
    assert split.cost_usd == pytest.approx(
        (300 * prices["cache_write_5m"] + 500 * prices["cache_write_1h"]) / 1e6
    )
    assert (usage + one_hour).cost_usd == pytest.approx(
        usage.cost_usd + one_hour.cost_usd
    )


# --- Reading results -----------------------------------------------------------

WORDS = study_words(LINES, PARSES)


def _output(**changes):
    data = {
        "fluent": "Sing the wrath, goddess. Not to Hades.",
        "literal": ["wrath sing, goddess", "not-and to Hades"],
        "words": [
            {"gloss": "the wrath", "note": "First word of the poem."},
            {"gloss": "sing", "note": "", "parse_check": ""},
            {"gloss": "goddess", "note": "The Muse."},
            {"gloss": "and not", "note": "οὐ + δέ."},
            {
                "gloss": "to Hades",
                "note": "Epic dative of Ἀΐδης.",
                "parse": {
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
                },
            },
        ],
    }
    data.update(changes)
    return data


def test_check_output_reads_words_and_model_parses():
    out = check_output(_output(), WORDS, 2)
    assert out.literal == ["wrath sing, goddess", "not-and to Hades"]
    assert [w.gloss for w in out.words] == [
        "the wrath",
        "sing",
        "goddess",
        "and not",
        "to Hades",
    ]
    assert out.words[1].parse_check is None  # an empty remark is no remark
    assert (out.words[4].lemma, out.words[4].postag) == ("Ἀΐδης", "n-s---md-")
    # parses are only read for words the treebank lacks
    assert out.words[0].postag is None


@pytest.mark.parametrize(
    "changes, reason",
    [
        ({"literal": ["one line"]}, "wrong count: 1 literal lines for 2 lines"),
        ({"words": [{"gloss": "x", "note": ""}] * 4}, "wrong count: 4 word objects"),
        ({"fluent": " "}, "fluent is missing or empty"),
        ({"literal": ["a", ""]}, "literal line 2 is missing or empty"),
    ],
)
def test_check_output_rejects_wrong_counts_and_empty_fields(changes, reason):
    with pytest.raises(ValueError, match=reason):
        check_output(_output(**changes), WORDS, 2)


def test_check_output_requires_a_gloss_and_the_missing_parses():
    data = _output()
    data["words"][2]["gloss"] = ""
    with pytest.raises(ValueError, match="word 3's gloss"):
        check_output(data, WORDS, 2)
    data = _output()
    del data["words"][4]["parse"]
    with pytest.raises(ValueError, match=r"word 5 has no parse \(1\.2 Ἄϊδι\)"):
        check_output(data, WORDS, 2)
    data = _output()
    data["words"][4]["parse"]["case"] = "ablative"
    with pytest.raises(ValueError, match="case 'ablative'"):
        check_output(data, WORDS, 2)


def _message(text, stop_reason="end_turn", **extra):
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop_reason,
        usage=_usage(),
        **extra,
    )


def _entry(result, cid="1_1-2"):
    return SimpleNamespace(custom_id=cid, result=result)


def _succeeded(message):
    return _entry(SimpleNamespace(type="succeeded", message=message))


def test_read_result_succeeds_with_usage():
    outcome = read_result(_succeeded(_message(json.dumps(_output()))), WORDS, 2)
    assert outcome.failure is None
    assert outcome.custom_id == "1_1-2"
    assert outcome.output.fluent.startswith("Sing the wrath")
    assert outcome.usage.output_tokens == 2000 and outcome.usage.cost_usd > 0


@pytest.mark.parametrize(
    "result, failure, billed",
    [
        (
            SimpleNamespace(
                type="errored",
                error=SimpleNamespace(
                    type="error",
                    error=SimpleNamespace(
                        type="overloaded_error", message="Overloaded"
                    ),
                ),
            ),
            "error: overloaded_error: Overloaded",
            False,
        ),
        (SimpleNamespace(type="expired"), "expired", False),
        (SimpleNamespace(type="canceled"), "canceled", False),
        (
            SimpleNamespace(type="succeeded", message=_message("", "refusal")),
            "refusal",
            True,
        ),
        (
            SimpleNamespace(
                type="succeeded",
                message=_message(
                    "", "refusal", stop_details=SimpleNamespace(category="cyber")
                ),
            ),
            "refusal (cyber)",
            True,
        ),
        (
            SimpleNamespace(
                type="succeeded", message=_message('{"fluent": "Sing', "max_tokens")
            ),
            "truncated at max_tokens",
            True,
        ),
        (
            SimpleNamespace(type="succeeded", message=_message("not json")),
            "the output is not valid JSON",
            True,
        ),
        (
            SimpleNamespace(
                type="succeeded",
                message=_message(json.dumps(_output(literal=["x"]))),
            ),
            "wrong count: 1 literal lines for 2 lines",
            True,
        ),
    ],
)
def test_read_result_failures(result, failure, billed):
    outcome = read_result(_entry(result), WORDS, 2)
    assert outcome.output is None
    assert outcome.failure == failure
    assert (outcome.usage.cost_usd > 0) == billed


# --- First in the book ------------------------------------------------------------


def _word(ref, i, form, *lemmas):
    n = int(ref.split(".")[1]) + 100 * int(ref.split(".")[0])
    parts = [
        WordParse(1, n, i, part, form, lemma, "n-s---fa-" if lemma else None, 1)
        for part, lemma in enumerate(lemmas)
    ]
    return StudyWord(n, i, ref, form, parts)


def test_first_in_book_marks_each_lemmas_first_word_in_each_book():
    words = [
        _word("1.1", 0, "θεὰ", "θεά"),
        _word("1.1", 1, "θεᾶς", "θεά"),  # the same lemma again
        _word("1.2", 0, "οὐδʼ", "οὐ", "δέ"),  # δέ is new: the word is marked
        _word("1.2", 1, "δʼ", "δέ"),
        _word("1.2", 2, "οὐδʼ", "οὐ", "δέ"),  # neither part is new
        _word("1.3", 0, "ἄειδε", "ἀείδω1"),  # homograph digits don't count
        _word("1.3", 1, "ἀείδειν", "ἀείδω"),
        _word("1.3", 2, "Ἄϊδι", None),  # no lemma, never marked
        _word("2.1", 0, "θεοί", "θεά"),  # a new book starts afresh
    ]
    marked = first_in_book(words)
    assert [w.form for w in words if (w.line_number, w.token_index) in marked] == [
        "θεὰ",
        "οὐδʼ",
        "ἄειδε",
        "θεοί",
    ]


def test_study_words_take_the_marks():
    words = study_words(LINES, PARSES, {(1, 2), (2, 1)})
    assert [w.first_in_book for w in words] == [False, False, True, False, True]
    assert not any(w.first_in_book for w in study_words(LINES, PARSES))


def test_the_study_prompt_shows_the_mark_and_shares_the_note_rule():
    words = study_words(LINES, PARSES, {(1, 2)})
    assert "3 θεὰ: θεά, noun singular feminine vocative | first in this book" in (
        build_study_prompt(ILIAD, LINES, words)
    )
    assert f'- "note": {NOTE_RULE}' in STUDY_SYSTEM_PROMPT
    assert f'- "note": {NOTE_RULE}' in NOTES_SYSTEM_PROMPT
    for prompt in (STUDY_SYSTEM_PROMPT, NOTES_SYSTEM_PROMPT):
        assert 'A word marked "first in this book" is the first appearance' in prompt


# --- Notes-only requests ------------------------------------------------------------


def _noted_words():
    words = study_words(LINES, PARSES, {(1, 0), (2, 0)})
    for w, gloss in zip(words, ["wrath", "sing", "goddess", "and not", "to Hades"]):
        w.gloss = gloss
    return words


def test_notes_prompt_gives_lines_translation_and_words_with_glosses():
    prompt = build_notes_prompt(
        ILIAD, LINES, "Sing the wrath, goddess. Not to Hades.", _noted_words()
    )
    assert prompt == (
        'Iliad 1.1–1.2: 2 lines, 5 words. Give "notes" 5 objects.\n'
        "\n"
        "Lines:\n"
        "1.1 μῆνιν ἄειδε, θεὰ\n"
        "1.2 οὐδʼ Ἄϊδι\n"
        "\n"
        "Translation:\n"
        "Sing the wrath, goddess. Not to Hades.\n"
        "\n"
        "Words:\n"
        "[1.1]\n"
        "1 μῆνιν: μῆνις, noun singular feminine accusative | gloss: wrath "
        "| first in this book\n"
        "2 ἄειδε: ἀείδω, verb 2nd singular present imperative active | gloss: sing\n"
        "3 θεὰ: θεά, noun singular feminine vocative | gloss: goddess\n"
        "[1.2]\n"
        "4 οὐδʼ: οὐ, adverb + δέ, particle | gloss: and not | first in this book\n"
        "5 Ἄϊδι: no treebank parse | gloss: to Hades"
    )


def test_notes_request_settings():
    words = _noted_words()
    request = build_notes_request(
        ILIAD, TranslationUnit(12, 1, 2), LINES, "Fluent.", words, "high"
    )
    assert request["custom_id"] == "12_1-2"
    params = request["params"]
    assert params["model"] == MODEL
    assert params["max_tokens"] == MAX_TOKENS
    assert params["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": NOTES_SCHEMA},
    }
    [system] = params["system"]
    assert system["text"] == NOTES_SYSTEM_PROMPT
    assert system["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert params["messages"] == [
        {
            "role": "user",
            "content": build_notes_prompt(ILIAD, LINES, "Fluent.", words),
        }
    ]
    assert "thinking" not in params and "fallbacks" not in params
    default = build_notes_request(
        ILIAD, TranslationUnit(12, 1, 2), LINES, "Fluent.", words
    )
    assert default["params"]["output_config"]["effort"] == EFFORT


def test_notes_schema_suits_structured_outputs():
    for obj in _objects(NOTES_SCHEMA):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) <= set(obj["properties"])
    assert NOTES_SCHEMA["properties"]["notes"]["items"]["required"] == ["word", "note"]


def _notes(*forms):
    return {"notes": [{"word": f, "note": f"On {f}."} for f in forms]}


FORMS = ["μῆνιν", "ἄειδε", "θεὰ", "οὐδʼ", "Ἄϊδι"]


def test_check_notes_reads_a_note_per_word():
    assert check_notes(_notes(*FORMS), WORDS) == [f"On {f}." for f in FORMS]


@pytest.mark.parametrize(
    "given, form",
    [
        ("θεὰ", "θεὰ"),
        ("θεά", "θεὰ"),  # another accent
        ("οὐδ'", "οὐδʼ"),  # another elision mark
        ("οὐδ", "οὐδʼ"),
        ("θεὰ,", "θεὰ"),  # with punctuation
        ("Μῆνιν", "μῆνιν"),
    ],
)
def test_same_word_ignores_accents_elision_and_punctuation(given, form):
    assert same_word(given, form)


def test_check_notes_takes_a_words_lemma_in_its_place():
    # the model sometimes repeats the dictionary form: μῆνις for μῆνιν
    data = _notes("μῆνις", "ἀείδω", "θεὰ", "δέ", "Ἄϊδι")
    assert len(check_notes(data, WORDS)) == 5
    # but not another word's lemma
    with pytest.raises(ValueError, match="note 1 is for θεά, not μῆνιν"):
        check_notes(_notes("θεά", *FORMS[1:]), WORDS)


def test_same_word_tells_words_apart():
    assert not same_word("θεοί", "θεὰ")
    assert not same_word("ἄειδε", "μῆνιν")


@pytest.mark.parametrize(
    "data, reason",
    [
        ([], "not a JSON object"),
        ({"words": []}, "notes is missing"),
        (_notes(*FORMS[:4]), "wrong count: 4 notes for 5 words"),
        (  # a skipped word with one added later fails where it is skipped
            _notes("μῆνιν", "θεὰ", "οὐδʼ", "Ἄϊδι", "Ἄϊδι"),
            r"note 2 is for θεὰ, not ἄειδε \(1\.1\)",
        ),
        ({"notes": [*_notes(*FORMS[:4])["notes"], "x"]}, "note 5 is not an object"),
        (
            {"notes": [*_notes(*FORMS[:4])["notes"], {"word": " ", "note": "x"}]},
            "note 5's word is missing",
        ),
    ],
)
def test_check_notes_rejects_what_does_not_line_up(data, reason):
    with pytest.raises(ValueError, match=reason):
        check_notes(data, WORDS)


def test_check_notes_allows_an_empty_note():
    data = _notes(*FORMS)
    data["notes"][1]["note"] = ""
    assert check_notes(data, WORDS)[1] == ""


def test_read_notes_result():
    entry = _succeeded(_message(json.dumps(_notes(*FORMS))))
    outcome = read_notes_result(entry, WORDS)
    assert outcome.failure is None and outcome.notes[0] == "On μῆνιν."
    assert outcome.usage.output_tokens == 2000 and outcome.usage.cost_usd > 0

    skipped = read_notes_result(
        _succeeded(_message(json.dumps(_notes(*FORMS[:4])))), WORDS
    )
    assert skipped.notes is None
    assert skipped.failure == "wrong count: 4 notes for 5 words"
    assert skipped.usage.cost_usd > 0  # billed all the same

    refused = read_notes_result(
        _succeeded(
            _message("", "refusal", stop_details=SimpleNamespace(category="cyber"))
        ),
        WORDS,
    )
    assert refused.failure == "refusal (cyber)"
