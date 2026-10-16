"""Treebank postags read as part of speech and morphology (greek/postag.py)."""

from greek.postag import decode, encode
from greek.web.formatting import condensed_parse, parse_items


def test_noun():
    assert decode("n-s---fa-") == (
        "noun",
        {"number": "singular", "gender": "feminine", "case": "accusative"},
    )


def test_finite_verb():
    pos, morph = decode("v2spma---")
    assert pos == "verb"
    assert morph == {
        "person": "2",
        "number": "singular",
        "tense": "present",
        "mood": "imperative",
        "voice": "active",
    }
    assert condensed_parse(pos, morph) == "verb · 2nd sg. pres. imper. act."


def test_participle_and_infinitive_read_as_such():
    pos, morph = decode("v-sapmmn-")
    assert pos == "participle"
    assert condensed_parse(pos, morph) == "participle · aor. mid. nom. sg. masc."
    assert decode("v--ana---")[0] == "infinitive"


def test_middle_or_passive_voice():
    pos, morph = decode("v3siie---")
    assert morph["voice"] == "middle/passive"
    assert condensed_parse(pos, morph) == "verb · 3rd sg. impf. ind. mid./pass."


def test_degree_and_uninflected_words():
    assert decode("a-p---mnc")[1]["degree"] == "comparative"
    assert decode("d--------") == ("adverb", {})
    assert decode("g--------") == ("particle", {})


def test_parse_items_read_in_order():
    _, morph = decode("v3saia---")
    assert parse_items(morph) == [
        ("Person", "3rd"),
        ("Number", "singular"),
        ("Tense", "aorist"),
        ("Mood", "indicative"),
        ("Voice", "active"),
    ]


def test_missing_short_or_unknown_codes():
    assert decode(None) == ("", {})
    assert decode("") == ("", {})
    assert decode("n") == ("noun", {})
    assert decode("q-s---zz-") == ("", {"number": "singular"})


def test_glaux_coordinating_conjunction_reads_as_a_conjunction():
    # GLAUx tags καί, δέ, τε "b"; a model's parse still writes Perseus's "c"
    assert decode("b--------") == ("conjunction", {})
    assert encode("conjunction", {}) == "c--------"
