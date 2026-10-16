"""Beta Code to Unicode (greek/fetch/betacode.py), as written in the Perseus LSJ."""

import unicodedata

import pytest

from greek.fetch.betacode import beta_to_unicode


@pytest.mark.parametrize(
    "beta, greek",
    [
        ("a)/lgos", "ἄλγος"),  # smooth breathing + acute
        ("h(/lios", "ἥλιος"),  # rough breathing + acute
        ("mh=nis", "μῆνις"),  # circumflex
        ("w)=|", "ᾦ"),  # breathing, circumflex and iota subscript
        ("a(/|dhs", "ᾅδης"),
        ("kai\\", "καὶ"),  # grave
        ("r(h/twr", "ῥήτωρ"),  # rho with rough breathing
    ],
)
def test_accents_breathings_and_iota_subscript(beta, greek):
    assert beta_to_unicode(beta) == greek


@pytest.mark.parametrize(
    "beta, greek",
    [
        ("proi+/ayen", "προΐαψεν"),  # diaeresis before the accent
        ("proi/+ayen", "προΐαψεν"),  # or after it: the same character
        ("*)ai/+dhs", "Ἀΐδης"),
        ("e)u+/s", "ἐΰς"),
    ],
)
def test_diaeresis(beta, greek):
    assert beta_to_unicode(beta) == greek


@pytest.mark.parametrize(
    "beta, greek",
    [
        ("*)axilleu/s", "Ἀχιλλεύς"),  # marks between * and the letter
        ("*(/aidhs", "Ἅιδης"),
        ("*s", "Σ"),
        ("*a|)", "ᾈ"),  # iota subscript written after the letter
        ("*r(h=a", "Ῥῆα"),  # breathing after the capital
        (")*apo/llwn", "Ἀπόλλων"),  # breathing before the *
        ("tou= )*epikou/rou", "τοῦ Ἐπικούρου"),
    ],
)
def test_capitals(beta, greek):
    assert beta_to_unicode(beta) == greek


@pytest.mark.parametrize(
    "beta, greek",
    [
        ("lo/gos", "λόγος"),
        ("pa=s, pa=sa", "πᾶς, πᾶσα"),  # before punctuation and space
        ("ou)k ei)=s;", "οὐκ εἶς;"),
        ("dus-", "δυσ-"),  # a prefix keeps medial sigma
        ("s'", "σʼ"),  # so does an elided σέ
        ("ei)s1", "εἰσ"),  # s1, s2: explicit medial and final sigma
        ("s2a", "ςα"),
        ("SOFOS", "σοφος"),  # uppercase letters read as Beta Code too
    ],
)
def test_final_sigma(beta, greek):
    assert beta_to_unicode(beta) == greek


def test_vowel_length_and_underdot_are_dropped():
    assert beta_to_unicode("i)/fqi_mos") == "ἴφθιμος"
    assert beta_to_unicode("ba^lw=") == "βαλῶ"
    assert beta_to_unicode("a?") == "α"


def test_punctuation_and_other_characters():
    assert beta_to_unicode("a)ll' e)/ba^n:") == "ἀλλʼ ἔβαν·"
    assert beta_to_unicode("w)/mois i). 18.204") == "ὤμοις ἰ. 18.204"
    assert beta_to_unicode("[a]—") == "[α]—"
    assert beta_to_unicode("v") == "ϝ"


def test_output_is_nfc():
    text = beta_to_unicode("*)ai/+dhs a(/|dhs proi+/ayen")
    assert text == unicodedata.normalize("NFC", text)
