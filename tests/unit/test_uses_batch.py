"""Use-in-context batch requests and results (greek/api/uses.py): which words
get a label, the request, and the checks on a result."""

import json
from types import SimpleNamespace

import pytest

from greek.api.study import MODEL
from greek.api.uses import (
    USES_SCHEMA,
    USES_SYSTEM_PROMPT,
    build_uses_prompt,
    build_uses_request,
    check_uses,
    default_output_per_label,
    read_uses_result,
    role_text,
    use_target,
    use_words,
)
from greek.api.works import ILIAD
from greek.models import Line, TranslationUnit, TreebankNode, WordParse
from greek.syntax import text_syntax

# ἐκ Διὸς καὶ Ἥρης ἦλθε θεῶν πρῶτα ἰδὼν κῆρ ἐπὶ νῆας: one sentence, a node
# per word in token order (node 3, καὶ, coordinates Διὸς and Ἥρης under ἐκ).
CONTENT = "ἐκ Διὸς καὶ Ἥρης ἦλθε θεῶν πρῶτα ἰδὼν κῆρ ἐπὶ νῆας."
NODES = [
    (1, 5, "AuxP", "ἐκ", "ἐκ", "r--------"),
    (2, 3, "ADV_CO", "Διὸς", "Ζεύς", "n-s---mg-"),
    (3, 1, "COORD", "καὶ", "καί", "c--------"),
    (4, 3, "ADV_CO", "Ἥρης", "Ἥρη", "n-s---fg-"),
    (5, 0, "PRED", "ἦλθε", "ἔρχομαι", "v3saia---"),
    (6, 5, "OBJ", "θεῶν", "θεός", "n-p---mg-"),
    (7, 5, "ADV", "πρῶτα", "πρῶτος", "a-p---na-"),
    (8, 5, "ADV", "ἰδὼν", "ὁράω", "v-sapamn-"),
    (9, 8, "OBJ", "κῆρ", "κῆρ", "n-s---na-"),
    (10, 5, "AuxP", "ἐπὶ", "ἐπί", "r--------"),
    (11, 10, "ADV", "νῆας", "ναῦς", "n-p---fa-"),
    (12, 5, "AuxK", ".", "punc1", "u--------"),
]


def _parse(i, form, lemma, postag, node_id=None, part=0, sentence_id=1):
    return WordParse(1, 1, i, part, form, lemma, postag, sentence_id, node_id=node_id)


def _sentence():
    nodes = [
        TreebankNode(1, 1, node_id, head, rel, form, lemma, tag)
        for node_id, head, rel, form, lemma, tag in NODES
    ]
    parses = [
        _parse(i, form, lemma, tag, node_id)
        for i, (node_id, _, _, form, lemma, tag) in enumerate(NODES[:-1])
    ]
    line = Line(1, 1, 1, CONTENT, "1.1")
    return line, parses, text_syntax(nodes, parses, {1: line})


def test_which_words_get_a_label():
    line, parses, trees = _sentence()
    targets = {p.form: use_target([p], trees) for p in parses}
    assert targets == {
        "ἐκ": None,
        "Διὸς": None,  # after a preposition, through the coordination
        "καὶ": None,
        "Ἥρης": None,
        "ἦλθε": None,  # indicative
        "θεῶν": "genitive",
        "πρῶτα": "adverbial accusative",
        "ἰδὼν": "participle",
        "κῆρ": None,  # an object: the model may add it
        "ἐπὶ": None,
        "νῆας": None,  # adverbial, but after a preposition
    }


# ἔδωκε θεοῖσιν ἀθανάτοισι Ἀτρεΐδῃ ἰόντι δῶρα Ἀχιλῆος Πηληϊάδεω: words
# that agree with a noun or stand in apposition to it share its use.
AGREEING = [
    (1, 0, "PRED", "ἔδωκε", "δίδωμι", "v3saia---"),
    (2, 1, "OBJ", "θεοῖσιν", "θεός", "n-p---md-"),
    (3, 2, "ATR", "ἀθανάτοισι", "ἀθάνατος", "a-p---md-"),
    (4, 2, "ATR", "Ἀτρεΐδῃ", "Ἀτρεΐδης", "n-s---md-"),
    (5, 2, "ATR", "ἰόντι", "εἶμι", "v-sapamd-"),
    (6, 1, "OBJ", "δῶρα", "δῶρον", "n-p---na-"),
    (7, 6, "ATR", "Ἀχιλῆος", "Ἀχιλλεύς", "n-s---mg-"),
    (8, 7, "ATR", "Πηληϊάδεω", "Πηληϊάδης", "n-s---mg-"),
]


def test_a_word_that_agrees_with_a_noun_is_not_marked():
    content = " ".join(form for _, _, _, form, _, _ in AGREEING)
    nodes = [TreebankNode(1, 1, *row) for row in AGREEING]
    parses = [
        _parse(i, form, lemma, tag, node_id)
        for i, (node_id, _, _, form, lemma, tag) in enumerate(AGREEING)
    ]
    trees = text_syntax(nodes, parses, {1: Line(1, 1, 1, content, "1.1")})
    assert {p.form: use_target([p], trees) for p in parses} == {
        "ἔδωκε": None,
        "θεοῖσιν": "dative",
        "ἀθανάτοισι": None,  # agrees with θεοῖσιν
        "Ἀτρεΐδῃ": None,  # in apposition to θεοῖσιν
        "ἰόντι": "participle",  # agrees too, but a participle is always marked
        "δῶρα": None,
        "Ἀχιλῆος": "genitive",
        "Πηληϊάδεω": "genitive",  # a genitive on a genitive: "genitive with"
    }
    assert trees[1].agrees(3) and trees[1].agrees(4) and trees[1].agrees(5)
    assert not trees[1].agrees(8) and not trees[1].agrees(2)
    assert not trees[1].agrees(99)


@pytest.mark.parametrize(
    "postag, kind",
    [
        ("v--pna---", "infinitive"),
        ("v1ppsa---", "subjunctive"),
        ("v3saoa---", "optative"),
        ("v2spma---", "imperative"),
        ("v-sapamn-", "participle"),
        ("v3saia---", None),
        ("n-s---md-", None),  # a dative without a node: its preposition is unknown
        (None, None),
    ],
)
def test_moods_need_no_node(postag, kind):
    assert use_target([_parse(0, "x", "x", postag)], {}) == kind


def test_a_split_word_is_one_if_any_part_is():
    parts = [
        _parse(0, "οὐ", "οὐ", "d--------", part=0),
        _parse(0, "ἰδών", "ὁράω", "v-sapamn-", part=1),
    ]
    assert use_target(parts, {}) == "participle"


def test_the_preposition_of_a_phrase():
    _, _, trees = _sentence()
    tree = trees[1]
    assert tree.preposition(2).form == "ἐκ"  # Διὸς, through καὶ
    assert tree.preposition(11).form == "ἐπὶ"
    assert tree.preposition(6) is None
    assert tree.preposition(99) is None


def test_use_words_carry_their_role_and_kind():
    line, parses, trees = _sentence()
    words = use_words([line], parses, trees)
    assert [w.form for w in words][-1] == "νῆας"  # the full stop is stripped
    by_form = {w.form: w for w in words}
    assert by_form["θεῶν"].role == "genitive object of ἦλθε"
    assert by_form["θεῶν"].target == "genitive"
    assert by_form["κῆρ"].role == "object of ἰδὼν" and by_form["κῆρ"].target is None
    assert role_text([], trees) == ""


# --- The request ----------------------------------------------------------------------


def _unit_words():
    line, parses, trees = _sentence()
    words = use_words([line], parses, trees)
    for w in words:
        w.gloss = f"g{w.token_index}"
    return line, words


def test_prompt_marks_the_words_to_label():
    line, words = _unit_words()
    prompt = build_uses_prompt(ILIAD, [line], "From Zeus and Hera he came.", words)
    lines = prompt.splitlines()
    assert lines[0] == (
        "Iliad 1.1: 1 lines, 11 words, 3 marked. "
        'Give "uses" 3 objects, one per marked word, in order.'
    )
    assert lines[2:4] == ["Lines:", f"1.1 {CONTENT}"]
    assert lines[5:7] == ["Translation:", "From Zeus and Hera he came."]
    assert lines[8:10] == ["Words:", "[1.1]"]
    assert (
        "6 θεῶν: θεός, noun plural masculine genitive | gloss: g5 | "
        "role: genitive object of ἦλθε → use"
    ) in lines
    assert (
        "9 κῆρ: κῆρ, noun singular neuter accusative | gloss: g8 | role: object of ἰδὼν"
        in lines
    )
    assert "2 Διὸς: Ζεύς, noun singular masculine genitive | gloss: g1 | role: " in (
        prompt
    )
    assert sum(line.endswith("→ use") for line in lines) == 3


def test_request_shape():
    line, words = _unit_words()
    request = build_uses_request(
        ILIAD, TranslationUnit(12, 1, 1), [line], "Fluent.", words, "high"
    )
    assert request["custom_id"] == "12_1-1"
    params = request["params"]
    assert params["model"] == MODEL
    assert params["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": USES_SCHEMA},
    }
    assert params["system"][0]["text"] == USES_SYSTEM_PROMPT
    assert params["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert (
        "Participle: attributive participle, substantive participle, "
        "circumstantial participle with its kind after a comma"
    ) in USES_SYSTEM_PROMPT
    assert "accusative of respect that the treebank calls an object" in (
        USES_SYSTEM_PROMPT
    )
    # tightened after Pilot 2: no remarks on the form
    assert "say nothing of how the word is formed" in USES_SYSTEM_PROMPT
    # after Book 1: the names written as listed, the same in every request
    assert "one of the names below, written exactly as listed" in USES_SYSTEM_PROMPT
    assert "imperative of command, imperative of prayer" in USES_SYSTEM_PROMPT


def test_default_output_per_label():
    assert default_output_per_label("medium") == 65
    assert default_output_per_label("high") == 100
    assert default_output_per_label("max") > 100


# --- Results ----------------------------------------------------------------------------


def _use(n, word, use="a use"):
    return {"n": n, "word": word, "use": use}


def test_labels_are_checked_by_number_and_word():
    _, words = _unit_words()
    uses = check_uses(
        {
            "uses": [
                _use(8, "ἰδών", "circumstantial participle: temporal"),
                _use(6, "θεῶν", " genitive with a verb of coming? "),
                _use(9, "κῆρ", "accusative of respect"),  # an extra
                _use(7, "πρῶτα", "adverbial: first"),
            ]
        },
        words,
    )
    assert [(u.number, u.extra) for u in uses] == [
        (6, False),
        (7, False),
        (8, False),
        (9, True),
    ]
    assert uses[0].label == "genitive with a verb of coming?"


def test_a_word_given_by_its_lemma_is_accepted():
    _, words = _unit_words()
    uses = check_uses(
        {"uses": [_use(6, "θεός"), _use(7, "πρῶτα"), _use(8, "ὁράω")]}, words
    )
    assert len(uses) == 3


@pytest.mark.parametrize(
    "items, message",
    [
        ([_use(6, "θεῶν"), _use(7, "πρῶτα")], "no use for marked words 8 ἰδὼν (1.1)"),
        (
            [_use(6, "θεῶν"), _use(7, "πρῶτα"), _use(8, "κῆρ")],
            "use 8 is for κῆρ, not ἰδὼν (1.1)",
        ),
        (
            [_use(6, "θεῶν"), _use(6, "θεῶν"), _use(7, "πρῶτα"), _use(8, "ἰδὼν")],
            "word 6 (θεῶν) is labelled twice",
        ),
        ([_use(12, "x")], "a use is for word 12, which isn't numbered"),
        ([_use(True, "ἐκ")], "a use is for word True, which isn't numbered"),
        ([_use(6, "θεῶν", " ")], "use 6 is missing or empty"),
        (["θεῶν"], "a use is not an object"),
    ],
)
def test_a_result_that_does_not_line_up_fails(items, message):
    _, words = _unit_words()
    with pytest.raises(
        ValueError, match=message.replace("(", r"\(").replace(")", r"\)")
    ):
        check_uses({"uses": items}, words)


def test_output_without_a_list_raises():
    _, words = _unit_words()
    with pytest.raises(ValueError, match="uses is missing"):
        check_uses({"labels": []}, words)


def _entry(text, stop_reason="end_turn"):
    usage = SimpleNamespace(
        input_tokens=800,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens=300,
        cache_creation=None,
    )
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=usage,
    )
    return SimpleNamespace(
        custom_id="12_1-1", result=SimpleNamespace(type="succeeded", message=message)
    )


def test_read_result():
    _, words = _unit_words()
    good = json.dumps(
        {"uses": [_use(6, "θεῶν"), _use(7, "πρῶτα"), _use(8, "ἰδὼν")]},
        ensure_ascii=False,
    )
    outcome = read_uses_result(_entry(good), words)
    assert outcome.failure is None and len(outcome.uses) == 3
    assert outcome.usage.cost_usd == pytest.approx((800 * 2 + 300 * 10) / 1e6)

    outcome = read_uses_result(_entry(good, "max_tokens"), words)
    assert outcome.failure == "truncated at max_tokens" and outcome.uses is None
    outcome = read_uses_result(_entry("{}"), words)
    assert outcome.failure == "uses is missing"
    assert outcome.usage.output_tokens == 300  # a failed message is still billed
