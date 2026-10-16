"""
One pipeline for every text: prose units,
the words a request numbers (editorial marks and speakers' labels left out),
automatic-parse marks and repeated words.
"""

import pytest

from greek.api.study import (
    build_study_prompt,
    check_output,
    group_units,
    is_word,
    numbered_words,
    study_words,
)
from greek.api.uses import build_uses_prompt
from greek.api.works import HERODOTUS, ILIAD, THUCYDIDES
from greek.models import Line, TreebankSentence, WordParse


def _sections(*specs):
    """Lines from (reference, word count) pairs, numbered from 1."""
    return [
        Line(
            id=n,
            text_id=1,
            line_number=n,
            content=" ".join(f"w{n}_{i}" for i in range(words)),
            reference=ref,
        )
        for n, (ref, words) in enumerate(specs, 1)
    ]


def _sentences(*spans):
    return [TreebankSentence(1, i, a, b) for i, (a, b) in enumerate(spans)]


def _spans(units):
    return [(u.first_line, u.last_line) for u in units]


def _one_sentence_each(lines):
    return _sentences(*[(n, n) for n in range(1, len(lines) + 1)])


# --- Prose units ----------------------------------------------------------------


def test_prose_sections_are_grouped_up_to_80_words():
    lines = _sections(("1.1.0", 39), ("1.1.1", 30), ("1.1.2", 20), ("1.1.3", 30))
    units = group_units(1, lines, _one_sentence_each(lines), **HERODOTUS.unit_size)
    # 39 + 30 = 69; + 20 = 89 > 80, so 1.1.2 starts a unit: 20 + 30 = 50
    assert _spans(units) == [(1, 2), (3, 4)]


def test_prose_units_stay_within_a_chapter():
    lines = _sections(("1.1.1", 10), ("1.1.2", 10), ("1.2.1", 10), ("1.2.2", 10))
    units = group_units(1, lines, _one_sentence_each(lines), max_words=80)
    assert _spans(units) == [(1, 2), (3, 4)]


def test_a_long_section_is_a_unit_alone():
    lines = _sections(("1.1.1", 20), ("1.1.2", 120), ("1.1.3", 20))
    units = group_units(1, lines, _one_sentence_each(lines), max_words=80)
    assert _spans(units) == [(1, 1), (2, 2), (3, 3)]


def test_sections_that_share_a_sentence_merge_first_even_past_the_limit():
    lines = _sections(("1.1.1", 50), ("1.1.2", 50), ("1.1.3", 10))
    units = group_units(1, lines, _sentences((1, 2), (3, 3)), max_words=80)
    assert _spans(units) == [(1, 2), (3, 3)]
    # a sentence across chapters keeps them together too
    lines = _sections(("1.1.1", 10), ("1.2.1", 10), ("1.2.2", 10))
    units = group_units(1, lines, _sentences((1, 2), (3, 3)), max_words=80)
    assert _spans(units) == [(1, 3)]


def test_prose_units_do_not_cross_books_and_cover_every_section():
    lines = _sections(
        *[(f"1.{c}.{s}", 15 + 7 * s) for c in range(1, 5) for s in range(1, 4)],
        *[(f"2.1.{s}", 30) for s in range(1, 6)],
    )
    sentences = _sentences((1, 2), (3, 3), (5, 7), (8, 8), (13, 14))
    units = group_units(1, lines, sentences, max_words=80)
    covered = [n for u in units for n in range(u.first_line, u.last_line + 1)]
    assert covered == list(range(1, len(lines) + 1))
    by_number = {line.line_number: line for line in lines}
    for u in units:
        books = {
            by_number[n].reference.split(".")[0] for n in (u.first_line, u.last_line)
        }
        assert len(books) == 1


def test_a_prose_book_without_sentences_is_cut_by_words():
    lines = _sections(("1.1.1", 30), ("1.1.2", 30), ("1.1.3", 30))
    assert _spans(group_units(1, lines, [], max_words=80)) == [(1, 2), (3, 3)]


def test_verse_units_are_unchanged_without_max_words():
    lines = _sections(*[(f"1.{n}", 5) for n in range(1, 15)])
    sentences = _sentences((1, 4), (5, 8), (9, 12), (13, 14))
    assert _spans(group_units(1, lines, sentences, **ILIAD.unit_size)) == [
        (1, 8),
        (9, 14),
    ]


# --- The words a request numbers ----------------------------------------------


def _parse(n, i, form, lemma, postag, sentence=1, part=0):
    return WordParse(1, n, i, part, form, lemma, postag, sentence if lemma else None)


@pytest.mark.parametrize(
    "form, parts, expected",
    [
        ("†", [], False),
        ("]", [], False),
        ("〉", [], False),
        ("...", [], False),
        ("’", [], False),
        ("ΑΘ", [], False),  # a speaker's label the treebank lacks
        ("ΜΗΛ", [_parse(1, 0, "ΜΗΛ", None, None)], False),
        ("ΑΘ", [_parse(1, 0, "ΑΘ", "Ἀθηναῖος", "n-p---mn-")], True),
        ("Ὦ", [], True),  # a single capital is a word
        ("Ἄϊδι", [], True),  # a word the treebank lacks is still a word
        ("μῆνιν", [_parse(1, 0, "μῆνιν", "μῆνις", "n-s---fa-")], True),
    ],
)
def test_is_word(form, parts, expected):
    assert is_word(form, parts) is expected


def test_editorial_marks_and_speakers_are_left_out_keeping_token_positions():
    line = Line(
        id=1,
        text_id=1,
        line_number=1,
        content="ΑΘ. εἰ † μὲν ], λέγοιμεν",
        reference="5.87.1",
    )
    parses = [
        _parse(1, 1, "εἰ", "εἰ", "b--------"),
        _parse(1, 3, "μὲν", "μέν", "g--------"),
        _parse(1, 5, "λέγοιμεν", "λέγω", "v1ppoa---"),
    ]
    words = study_words([line], parses)
    assert [(w.token_index, w.form) for w in words] == [
        (1, "εἰ"),
        (3, "μὲν"),
        (5, "λέγοιμεν"),
    ]


# --- Automatic-parse marks ---------------------------------------------------------


LINES = [
    Line(
        id=1,
        text_id=1,
        line_number=1,
        content="οὐδʼ ὀλίγον. κίνησις γὰρ",
        reference="1.1.2",
    ),
    Line(
        id=2, text_id=1, line_number=2, content="Ἄρχεται δὲ πόλεμος", reference="2.1.1"
    ),
]
PARSES = [
    _parse(1, 0, "οὐ", "οὐ", "d--------", sentence=1),
    _parse(1, 0, "δ’", "δέ", "b--------", sentence=1, part=1),
    _parse(1, 1, "ὀλίγον", "ὀλίγος", "a-s---ma-", sentence=1),
    _parse(1, 2, "κίνησις", "κίνησις", "n-s---fn-", sentence=2),
    _parse(1, 3, "γὰρ", "γάρ", "g--------", sentence=2),
    _parse(2, 0, "Ἄρχεται", "ἄρχω", "v3spie---", sentence=3),
    _parse(2, 1, "δὲ", "δέ", "b--------", sentence=3),
    _parse(2, 2, "πόλεμος", None, None),  # no treebank word
]


def test_a_section_parsed_automatically_throughout_is_marked_in_its_heading():
    words = study_words(LINES, PARSES, automatic={2, 3})
    assert [w.automatic for w in words] == [False, False, True, True, True, True, False]
    assert numbered_words(words) == [
        "[1.1.2]",
        "1 οὐδʼ: οὐ, adverb + δέ, conjunction",
        "2 ὀλίγον: ὀλίγος, adjective singular masculine accusative",
        "3 κίνησις: κίνησις, noun singular feminine nominative | automatic parse",
        "4 γὰρ: γάρ, particle | automatic parse",
        # every word of 2.1.1 in a sentence was parsed automatically; πόλεμος
        # has no treebank word, so it doesn't count either way
        "[2.1.1] (automatic parse)",
        "5 Ἄρχεται: ἄρχω, verb 3rd singular present indicative middle/passive",
        "6 δὲ: δέ, conjunction",
        "7 πόλεμος: no treebank parse",
    ]


def test_no_marks_without_automatic_sentences():
    words = study_words(LINES, PARSES)
    lines = numbered_words(words)
    assert "automatic" not in "\n".join(lines)
    assert lines[0] == "[1.1.2]" and lines[5] == "[2.1.1]"


def test_prose_user_messages_count_sections_and_head_them():
    words = study_words(LINES, PARSES, automatic={3})
    prompt = build_study_prompt(THUCYDIDES, LINES, words)
    assert prompt.splitlines()[:3] == [
        'Thucydides 1.1.2–2.1.1: 2 sections, 7 words. Give "literal" 2 strings '
        'and "words" 7 objects.',
        "",
        "Sections:",
    ]
    assert "[2.1.1] (automatic parse)" in prompt
    for w in words:
        w.gloss = "g"
    uses = build_uses_prompt(HERODOTUS, LINES[:1], "Fluent.", words[:4])
    assert uses.startswith("Herodotus 1.1.2: 1 section, 4 words, 0 marked.")
    assert "\nSections:\n" in uses


# --- Repeated words -------------------------------------------------------------------


def _output(words, given):
    return {
        "fluent": "Not even a little.",
        "literal": ["not even little"],
        "words": [
            {"word": g, "gloss": "x", "note": ""}
            if g is not None
            else {"gloss": "x", "note": ""}
            for g in given
        ],
    }


def test_a_repeated_word_must_be_the_numbered_one():
    words = study_words(LINES[:1], PARSES)
    out = check_output(_output(words, ["οὐδʼ", "ὀλίγον", "κίνησις", "γαρ"]), words, 1)
    assert len(out.words) == 4
    # the lemma of a part, or the form without accents, will do
    check_output(_output(words, ["δέ", "ὀλίγος", "κινησις", "γάρ"]), words, 1)
    with pytest.raises(ValueError, match="word 3 is for γὰρ, not κίνησις"):
        check_output(_output(words, ["οὐδʼ", "ὀλίγον", "γὰρ", "κίνησις"]), words, 1)
    # without the field (no --repeat-words) nothing is compared
    check_output(_output(words, [None] * 4), words, 1)
