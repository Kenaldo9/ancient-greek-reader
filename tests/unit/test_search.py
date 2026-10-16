import pytest

from greek.search import excerpt, fts_query, highlight, parse_query, plain


# --- plain ---


@pytest.mark.parametrize(
    "text, expected",
    [
        ("μῆνιν", "μηνιν"),
        ("Ἀχιλῆος", "αχιληοσ"),  # breathing, capital, circumflex, final sigma
        ("Πηληϊάδεω", "πηληιαδεω"),  # diaeresis
        ("ἀοιδῇ", "αοιδη"),  # iota subscript
        ("ὃς", "οσ"),  # final sigma
        ("ΜΗΝΙΝ", "μηνιν"),
        ("μυρίʼ", "μυρι"),  # elision mark (modifier letter apostrophe)
        ("ἄλγε’", "αλγε"),  # right single quotation mark
        ("δ᾽", "δ"),  # Greek koronis
        ("ἣ μυρί᾽ Ἀχαιοῖς", "η μυρι αχαιοισ"),
        ("", ""),
    ],
)
def test_plain(text, expected):
    assert plain(text) == expected


def test_plain_none():
    assert plain(None) is None


def test_plain_keeps_punctuation_between_words():
    assert plain("Ἀχιλῆος· οὐλομένην,") == "αχιληοσ· ουλομενην,"


# --- parse_query / fts_query ---


def test_query_words_are_plain_and_quoted():
    assert fts_query("Μῆνιν ἄειδε") == '"μηνιν" "αειδε"'


def test_query_prefix():
    assert parse_query("μην*") == [(["μην"], True)]
    assert fts_query("μην*") == '"μην"*'


def test_query_phrase():
    assert parse_query('"μῆνιν ἄειδε" θεά') == [
        (["μηνιν", "αειδε"], False),
        (["θεα"], False),
    ]
    assert fts_query('"μῆνιν ἄειδε"') == '"μηνιν αειδε"'


def test_query_unclosed_phrase():
    assert fts_query('"μῆνιν ἄειδε') == '"μηνιν αειδε"'


@pytest.mark.parametrize("query", ["", "   ", "·,;", '""', "*", "- : ( ) ^"])
def test_query_without_words(query):
    assert fts_query(query) is None


@pytest.mark.parametrize(
    "query, expected",
    [
        ("μῆνιν,", '"μηνιν"'),
        ("NOT μῆνιν", '"not" "μηνιν"'),  # FTS keywords are just words
        ("a-b", '"a" "b"'),
        ("col:μῆνιν", '"col" "μηνιν"'),
        ('μῆν"ιν', '"μην" "ιν"'),
    ],
)
def test_query_operators_and_punctuation_are_neutralized(query, expected):
    assert fts_query(query) == expected


# --- highlight ---


def test_highlight_marks_matching_words_without_punctuation():
    pieces = highlight("μῆνιν ἄειδε θεὰ, Μῆνιν·", "μηνιν")
    assert pieces == [
        ("μῆνιν", True),
        (" ἄειδε θεὰ, ", False),
        ("Μῆνιν", True),
        ("·", False),
    ]


def test_highlight_pieces_rebuild_the_line():
    line = "ἣ μυρί᾽ Ἀχαιοῖς ἄλγε᾽ ἔθηκε,"
    assert "".join(t for t, _ in highlight(line, "αλγε μυρι")) == line


def test_highlight_prefix():
    pieces = highlight("μῆνιν μήνιος μέν", "μην*")
    assert [t for t, on in pieces if on] == ["μῆνιν", "μήνιος"]


def test_highlight_phrase_words():
    pieces = highlight("μῆνιν ἄειδε θεὰ", '"μῆνιν ἄειδε"')
    assert [t for t, on in pieces if on] == ["μῆνιν", "ἄειδε"]


def test_highlight_no_query():
    assert highlight("μῆνιν ἄειδε", "") == [("μῆνιν ἄειδε", False)]


# --- excerpt ---


def _text(pieces):
    return "".join(t for t, _ in pieces)


def test_excerpt_short_text_unchanged():
    pieces = highlight("μῆνιν ἄειδε", "μηνιν")
    assert excerpt(pieces, width=100) == pieces


def test_excerpt_keeps_the_first_match_and_cuts_at_spaces():
    words = [f"λόγος{i}" for i in range(60)]
    line = " ".join(words[:30] + ["μῆνιν"] + words[30:])
    out = excerpt(highlight(line, "μηνιν"), width=120)
    text = _text(out)
    assert ("μῆνιν", True) in out
    assert text.startswith("… λόγος") and text.endswith(" …")
    inner = text[2:-2]
    assert len(inner) <= 120
    assert all(w in line.split() for w in inner.split())  # whole words only


def test_excerpt_match_at_start_has_no_leading_ellipsis():
    line = "μῆνιν " + " ".join(f"λόγος{i}" for i in range(60))
    text = _text(excerpt(highlight(line, "μηνιν"), width=100))
    assert text.startswith("μῆνιν") and text.endswith(" …")


def test_excerpt_match_at_end_has_no_trailing_ellipsis():
    line = " ".join(f"λόγος{i}" for i in range(60)) + " μῆνιν"
    text = _text(excerpt(highlight(line, "μηνιν"), width=100))
    assert text.startswith("… ") and text.endswith("μῆνιν")
