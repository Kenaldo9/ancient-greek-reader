"""Treebank parsing and alignment (greek/fetch/treebank.py) on a small excerpt."""

from pathlib import Path

import pytest

from greek.fetch.treebank import (
    TbWord,
    _align_run,
    align_text,
    bare,
    download_treebank,
    parse_treebank,
)
from greek.models import Line

FIXTURE = Path(__file__).parent.parent / "fixtures" / "iliad_treebank_excerpt.tb.xml"

# The app's text for the excerpt. The treebank omits 1.4, splits οὐδʼ (1.5)
# into οὐ + δ̓, and reads σὰ for σὺ (1.5).
APP_LINES = [
    ("1.1", "Μῆνιν ἄειδε θεὰ Πηληϊάδεω Ἀχιλῆος"),
    ("1.2", "οὐλομένην, ἣ μυρίʼ Ἀχαιοῖς ἄλγεʼ ἔθηκε,"),
    ("1.3", "πολλὰς δʼ ἰφθίμους ψυχὰς Ἄϊδι προΐαψεν."),
    ("1.4", "ἔρδον ἀθανάτοισι τεληέσσας ἑκατόμβας"),
    ("1.5", "οὐδʼ ἔθελον σὺ"),
    ("2.1", "Ἄλλοι μέν ῥα θεοί τε"),
    ("2.2", "εὗδον παννύχιοι."),
]


def _lines(rows=APP_LINES):
    return [
        Line(id=n, text_id=1, line_number=n, content=content, reference=ref)
        for n, (ref, content) in enumerate(rows, 1)
    ]


@pytest.fixture(scope="module")
def alignment():
    return align_text(1, _lines(), parse_treebank(FIXTURE))


def _parts(result, line_number, token_index):
    return [
        p
        for p in result.parses
        if (p.line_number, p.token_index) == (line_number, token_index)
    ]


def test_bare_drops_accents_breathings_and_elision_marks():
    assert bare("Ἄϊδι") == "αιδι"
    assert bare("οὐδʼ") == "ουδ"  # ʼ (U+02BC) is a modifier letter
    assert bare("δ̓") == "δ"  # the treebank's combining elision mark
    assert bare("ἔθηκε,") == "εθηκε"


def test_parse_skips_punctuation_and_placeholders():
    words = parse_treebank(FIXTURE)
    forms = [w.form for w in words]
    assert "." not in forms and "," not in forms
    assert "[0]" not in forms
    assert len(words) == 28


def test_parse_keeps_document_order_and_reads_book_from_citation():
    words = parse_treebank(FIXTURE)
    assert (words[0].form, words[0].book, words[0].sentence_id) == ("Ἄλλοι", "2", 300)
    assert words[7].form == "μῆνιν" and words[7].book == "1"


def test_parse_reads_missing_lemma_and_parse_as_none():
    words = {w.form: w for w in parse_treebank(FIXTURE)}
    assert words["Ἄϊδι"].lemma is None
    assert words["Ἄϊδι"].postag is None
    assert words["μῆνιν"].postag == "n-s---fa-"


def test_counts(alignment):
    assert alignment.words == 31
    assert alignment.identical == 25
    assert alignment.split == 1
    assert len(alignment.unmatched) == 5
    assert alignment.untagged == [("1.3", "Ἄϊδι")]
    assert alignment.unused == 1  # σὰ
    assert alignment.aligned_share == pytest.approx(26 / 31)


def test_every_word_has_a_row_with_contiguous_parts(alignment):
    keys = {}
    for p in alignment.parses:
        keys.setdefault((p.line_number, p.token_index), []).append(p.part)
    expected = {
        (n, i)
        for n, (_, content) in enumerate(APP_LINES, 1)
        for i, _ in enumerate(content.split())
    }
    assert set(keys) == expected
    assert all(parts == list(range(len(parts))) for parts in keys.values())


def test_identical_words_get_the_treebank_parse(alignment):
    [p] = _parts(alignment, 1, 0)
    assert (p.form, p.lemma, p.postag, p.sentence_id) == (
        "μῆνιν",
        "μῆνις",
        "n-s---fa-",
        100,
    )
    assert p.source == "treebank" and p.model_used is None
    # elided with ʼ in the app and with a combining mark in the treebank
    [p] = _parts(alignment, 2, 2)
    assert p.lemma == "μυρίος"


def test_citation_a_line_off_still_aligns(alignment):
    [p] = _parts(alignment, 3, 3)  # ψυχὰς, cited 1.4 in the treebank
    assert p.lemma == "ψυχή"


def test_split_word_has_two_parts(alignment):
    parts = _parts(alignment, 5, 0)  # οὐδʼ
    assert [(p.part, p.lemma, p.postag) for p in parts] == [
        (0, "οὐ", "d--------"),
        (1, "δέ", "g--------"),
    ]
    assert all(p.sentence_id == 200 for p in parts)


def test_omitted_line_words_have_no_treebank_word(alignment):
    parts = [p for p in alignment.parses if p.line_number == 4]
    assert [p.form for p in parts] == [
        "ἔρδον",
        "ἀθανάτοισι",
        "τεληέσσας",
        "ἑκατόμβας",
    ]
    assert all(
        p.sentence_id is None and p.lemma is None and p.postag is None for p in parts
    )
    assert ("1.4", "ἔρδον") in alignment.unmatched


def test_variant_reading_has_no_treebank_word(alignment):
    [p] = _parts(alignment, 5, 2)
    assert p.form == "σὺ" and not p.has_parse and p.sentence_id is None
    assert ("1.5", "σὺ") in alignment.unmatched


def test_untagged_treebank_word_is_kept_without_parse(alignment):
    [p] = _parts(alignment, 3, 4)
    assert p.form == "Ἄϊδι" and p.sentence_id == 100
    assert not p.has_parse


def test_unmatched_form_drops_trailing_punctuation():
    lines = _lines([("1.1", "ἡδεῖαν· τῆς")])
    result = align_text(1, lines, [])
    assert [p.form for p in result.parses] == ["ἡδεῖαν", "τῆς"]


def test_sentence_spans_use_aligned_lines_not_citations(alignment):
    spans = [(s.sentence_id, s.first_line, s.last_line) for s in alignment.sentences]
    # sentence 100 has a word cited 1.4, but it ends on line 3 (1.3)
    assert spans == [(100, 1, 3), (200, 5, 5), (300, 6, 7)]


def test_book_missing_from_treebank_is_unmatched():
    lines = _lines(APP_LINES + [("3.1", "Αὐτὰρ ἐπεὶ")])
    result = align_text(1, lines, parse_treebank(FIXTURE))
    assert ("3.1", "Αὐτὰρ") in result.unmatched
    assert result.words == 33


def test_treebank_for_another_text_aligns_little():
    lines = _lines([("1.1", "ἄνδρα μοι ἔννεπε μοῦσα πολύτροπον")])
    result = align_text(1, lines, parse_treebank(FIXTURE))
    assert result.aligned_share == 0
    assert result.unused == 28


def test_align_run_matches_split_word_next_to_unmatched_words():
    # a split word right after words the treebank omits, and before a variant
    assert _align_run(["ερδον", "ουδ", "συ"], ["ου", "δ", "σα"]) == [
        None,
        (0, 2),
        None,
    ]


def test_align_run_rejects_a_word_straddling_two_app_words():
    assert _align_run(["ab", "c"], ["a", "bc"]) == [None, None]


def test_align_run_with_nothing_to_match():
    assert _align_run(["a", "b"], []) == [None, None]
    assert _align_run([], ["a"]) == []


def test_split_word_beside_omitted_words_in_one_run():
    """The omitted line and the split word fall into one differing run."""
    words = [
        TbWord(1, "1", "ἔθηκε", "τίθημι", "v3saia---"),
        TbWord(2, "1", "οὐ", "οὐ", "d--------"),
        TbWord(2, "1", "δ̓", "δέ", "g--------"),
        TbWord(2, "1", "ἔθελον", "ἐθέλω", "v3piia---"),
    ]
    lines = _lines(
        [("1.1", "ἔθηκε"), ("1.2", "ἔρδον ἀθανάτοισι"), ("1.3", "οὐδʼ ἔθελον")]
    )
    result = align_text(1, lines, words)
    assert (result.identical, result.split, len(result.unmatched)) == (2, 1, 2)
    assert [p.lemma for p in result.parses if p.line_number == 3] == [
        "οὐ",
        "δέ",
        "ἐθέλω",
    ]


def test_download_uses_saved_copy(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "greek.fetch.treebank.fetch_url",
        lambda url, timeout: calls.append(url) or b"<treebank/>",
    )
    path = download_treebank("iliad", tmp_path)
    assert path == tmp_path / "tlg0012.tlg001.perseus-grc1.tb.xml"
    assert path.read_bytes() == b"<treebank/>"
    assert calls[0].endswith("/v2.1/Greek/texts/tlg0012.tlg001.perseus-grc1.tb.xml")
    download_treebank("iliad", tmp_path)
    assert len(calls) == 1
    download_treebank("iliad", tmp_path, refresh=True)
    assert len(calls) == 2
    assert not list(tmp_path.glob("*.part"))


def test_download_names_glaux_files_by_work(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "greek.fetch.treebank.fetch_url",
        lambda url, timeout: calls.append(url) or b"<treebank/>",
    )
    path = download_treebank("herodotus", tmp_path)
    assert path == tmp_path / "glaux-0016-001.xml"
    assert calls == [
        "https://raw.githubusercontent.com/alekkeersmaekers/glaux/main/xml/0016-001.xml"
    ]
