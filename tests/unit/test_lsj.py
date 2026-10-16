"""LSJ index, lemma resolution, short definitions and HTML (greek/fetch/lsj.py)."""

import copy
from pathlib import Path

import pytest

from greek.fetch.lsj import (
    LsjIndex,
    _entries,
    download_lsj,
    entry_html,
    lemma_key,
    read_entries,
    read_heads,
    resolve_lemma,
    short_definition,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "lsj_excerpt.xml"


@pytest.fixture(scope="module")
def heads():
    return read_heads([FIXTURE])


@pytest.fixture(scope="module")
def index(heads):
    return LsjIndex(heads)


def _element(key):
    for el in _entries([FIXTURE]):
        if el.get("key") == key:
            return copy.deepcopy(el)  # _entries clears each element after it
    raise KeyError(key)


def _match(index, lemma):
    m = resolve_lemma(lemma, index)
    return (m.head.key if m.head else None), m.matched_by


# --- index ---------------------------------------------------------------------


def test_heads_have_unicode_headwords_without_homograph_digits(heads):
    by_key = {h.key: h for h in heads}
    assert by_key["ei)mi/1"].headword == "εἰμί"
    assert by_key["ei)mi/1"].homograph == 1
    assert by_key["*(/aidhs"].headword == "Ἅιδης"
    assert by_key["*(/aidhs"].variants == ["ᾅδης", "Ἀΐδης"]


def test_forms_further_on_and_their_pointers(heads):
    by_key = {h.key: h for h in heads}
    assert by_key["mei/gnumi"].variants == ["μίγνυμι"]
    assert by_key["mei/gnumi"].forms == [("μείγνυμαι", None)]
    assert by_key["h)e/"].forms == [("ἠέλιος", "ἥλιος")]


def test_cross_reference_only_near_the_start(heads):
    by_key = {h.key: h for h in heads}
    assert by_key["a)rno/s"].xref == "ἀρήν"
    assert by_key["du/w1"].xref == "δύο"  # "v. X" counts too
    assert by_key["e)kei=qi"].xref is None  # "= X" after a long head
    assert by_key["i)/fqimos"].xref is None


def test_lemma_key_ignores_accents_case_and_final_sigma():
    assert lemma_key("Ἀΐδης") == lemma_key("ἀίδης") == "αιδησ"


# --- resolution ----------------------------------------------------------------


@pytest.mark.parametrize(
    "lemma, key, how",
    [
        ("ἴφθιμος", "i)/fqimos", "headword"),
        ("εἰμί1", "ei)mi/1", "headword"),  # a homograph digit on the lemma
        ("Ἅϊδης", "*(/aidhs", "headword"),  # by its letters, accents aside
        ("ᾍδης", "*(/aidhs", "variant"),
        ("μίγνυμι", "mei/gnumi", "variant"),
        ("μείγνυμαι", "mei/gnumi", "form"),
        ("ἠέλιος", "h(/lios", "form"),  # "ἠέλιος, ὁ, v. ἥλιος" inside ἠέ
        ("ἀρνός", "a)rh/n", "cross-reference"),
        ("μάλιστα", "ma/la", "alias"),
        ("ἀμβροσίη", "a)mbrosi/a", "spelling"),
    ],
)
def test_resolve(index, lemma, key, how):
    assert _match(index, lemma) == (key, how)


def test_late_cross_reference_is_not_followed(index):
    assert _match(index, "ἐκεῖθι") == ("e)kei=qi", "headword")


def test_homograph_with_its_own_meaning_wins_over_a_pointer(index):
    # δύω "sink", not δύω "v. δύο"
    assert _match(index, "δύω") == ("du/w2", "headword")


def test_same_accents_win_over_bare_letters(index):
    # ἄνεω is ἄνεῳ "in silence", not ἀνέω "= πτίσσω"
    assert _match(index, "ἄνεω") == ("a)/new|", "headword")


def test_a_name_only_matches_a_capitalized_headword(index):
    assert _match(index, "Ἑλένη") == (None, None)  # not ἑλένη, a torch
    assert _match(index, "ἑλένη") == ("e(le/nh", "headword")


def test_spelling_rules_are_not_used_for_names(index):
    assert _match(index, "Ἀμβροσίη") == (None, None)


def test_unknown_lemma(index):
    assert _match(index, "ἐρωή") == (None, None)


# --- short definitions -----------------------------------------------------------


def test_short_definition_takes_two_meanings_of_the_first_sense():
    assert short_definition(_element("i)/fqimos")) == "stout, strong; comely, stately"


def test_short_definition_skips_cognates_and_the_preamble():
    # nar- and vir are cognates; man after ":—" starts the meanings
    assert short_definition(_element("a)nh/r")) == "man; woman; husband"


def test_short_definition_skips_a_latin_gloss_in_parentheses():
    assert short_definition(_element("ei)mi/1")) == "be; exist"


def test_short_definition_skips_repeats():
    assert short_definition(_element("mh=nis")) == "wrath; revengeful temper"


def test_short_definition_without_meanings_uses_the_first_sense():
    text = short_definition(_element("a)/ndixa"))
    assert text.startswith("in two, asunder, ἄ. κεάσθη; of opinions")
    assert text.endswith(" …") and len(text) <= 92


def test_short_definition_of_a_cross_reference():
    assert short_definition(_element("a)rno/s")) == "= ἀρήν"


# --- HTML ----------------------------------------------------------------------


def test_entry_html_paragraphs_greek_meanings_and_citations():
    html = entry_html(_element("i)/fqimos"))
    head, sense = html.split("\n")
    assert head == (
        '<p><b lang="grc">ἴφθιμος</b>, <span lang="grc">η</span>, '
        '<span lang="grc">ον</span>,</p>'
    )
    assert sense.startswith('<p data-level="1"><b>A.</b> <em>stout, strong</em>')
    assert '<span lang="grc">ὤμοις ἰ</span>' in sense
    assert "<cite>Il. 18.204</cite>" in sense


def test_entry_html_escapes_text():
    assert "&amp; more &lt;sic&gt;" in entry_html(_element("i)/fqimos"))


def test_entry_html_has_a_paragraph_per_sense():
    html = entry_html(_element("a)nh/r"))
    assert html.count("<p") == 3
    assert '<p data-level="2"><b>II.</b> <em>husband</em></p>' in html


def test_read_entries_only_builds_wanted_ones():
    entries = read_entries([FIXTURE], {"n1", "n8"})
    assert set(entries) == {"n1", "n8"}
    assert entries["n8"].headword == "ἥλιος"
    assert entries["n8"].short_def == "sun"
    assert (entries["n8"].source, entries["n8"].key) == ("LSJ", "h(/lios")


def test_download_uses_saved_copies(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "greek.fetch.lsj.fetch_url",
        lambda url, timeout: calls.append(url) or b"<TEI.2/>",
    )
    paths = download_lsj(tmp_path)
    assert len(paths) == len(calls) == 27
    assert paths[0] == tmp_path / "lsj" / "grc.lsj.perseus-eng1.xml"
    assert calls[26].endswith("/grc/lsj/grc.lsj.perseus-eng27.xml")
    download_lsj(tmp_path)
    assert len(calls) == 27
    download_lsj(tmp_path, refresh=True)
    assert len(calls) == 54
    assert not list((tmp_path / "lsj").glob("*.part"))
