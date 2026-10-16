"""The reader with Opus study data (greek study batch): interlinear glosses, the
word strip's gloss, note and parse check, and fluent and literal translations
assembled from translation units and lines for any selection.

Model calls are never made; study results are saved directly.
"""

import re

import pytest

from greek.api.study import MODEL as STUDY_MODEL
from greek.db.repos.lines import insert_lines
from greek.db.repos.parses import save_treebank
from greek.db.repos.study import save_study_result
from greek.db.repos.texts import insert_text
from greek.models import LineTranslation, TranslationUnit, WordParse, WordStudy

LINES = [
    ("1.1", "μῆνιν ἄειδε θεὰ"),
    ("1.2", "οὐδʼ ἔσπετε νῦν"),
    ("1.3", "ὣς ἔφατʼ"),
]
HAIKU = "claude-haiku-4-5-20251001"
OPUS_46 = "claude-opus-4-6"
SONNET = "claude-sonnet-5-5"  # chosen in the picker

# (gloss, note, parse_check) for each word, by line
STUDY = {
    1: [
        ("wrath", "The first word of the poem.", None),
        ("sing", "Addressed to the Muse.", None),
        ("goddess", "Vocative.", None),
    ],
    2: [
        ("nor", "οὐ + δέ, elided.", None),
        ("tell", "", "imperative, not indicative: addressed to the Muses"),
        ("now", "Temporal.", None),
    ],
    3: [("thus", "Closes a speech.", None), ("he spoke", "Elided ἔφατο.", None)],
}
FLUENT = {(1, 2): "Sing of the wrath, goddess; tell it now.", (3, 3): "So he spoke."}


def _parse(tid, line, token, form, lemma, postag, part=0):
    return WordParse(tid, line, token, part, form, lemma, postag, 100)


def _save_unit(db, tid, first, last, model=STUDY_MODEL):
    save_study_result(
        db,
        TranslationUnit(tid, first, last, FLUENT[(first, last)], model),
        [
            LineTranslation(tid, n, f"literal {LINES[n - 1][0]}", model)
            for n in range(first, last + 1)
        ],
        [
            WordStudy(tid, n, i, gloss, note, check, model)
            for n in range(first, last + 1)
            for i, (gloss, note, check) in enumerate(STUDY[n])
        ],
        [],
    )


@pytest.fixture
def iliad(web_db):
    """Three lines with treebank parses, without study data."""
    db = web_db
    tid = insert_text(db, "Iliad", "Homer", "iliad.txt", "/d/iliad.txt", 3)
    insert_lines(db, tid, [c for _, c in LINES], [r for r, _ in LINES])
    save_treebank(
        db,
        tid,
        [],
        [
            _parse(tid, 1, 0, "μῆνιν", "μῆνις", "n-s---fa-"),
            _parse(tid, 1, 1, "ἄειδε", "ἀείδω", "v2spma---"),
            _parse(tid, 1, 2, "θεὰ", "θεά", "n-s---fv-"),
            _parse(tid, 2, 0, "οὐ", "οὐ", "d--------"),
            _parse(tid, 2, 0, "δ̓", "δέ", "g--------", part=1),
            _parse(tid, 2, 1, "ἔσπετε", "εἶπον", "v2paia---"),
            _parse(tid, 2, 2, "νῦν", "νῦν", "d--------"),
            _parse(tid, 3, 0, "ὣς", "ὡς", "d--------"),
            _parse(tid, 3, 1, "ἔφατʼ", "φημί", "v3siie---"),
        ],
    )
    return db, tid


@pytest.fixture
def studied(iliad):
    """Study data for units 1.1–1.2 and 1.3."""
    db, tid = iliad
    _save_unit(db, tid, 1, 2)
    _save_unit(db, tid, 3, 3)
    return db, tid


def _get(client, url, **params):
    resp = client.get(url, params=params)
    assert resp.status_code == 200
    return resp.text


def _passage(client, tid, lines, **params):
    return _get(client, f"/texts/{tid}/passage", lines=lines, **params)


def _word(client, tid, line, i):
    return _get(client, f"/texts/{tid}/word", line=line, i=i)


def _read(client, tid, **params):
    return _get(client, f"/texts/{tid}/read", **params)


def _line_html(html, n):
    start = html.index(f'data-line="{n}" data-ref=')
    end = re.search(r'<div class="line[ "]', html[start:])
    return html[start : start + end.start()] if end else html[start:]


# --- interlinear --------------------------------------------------------------


def test_interlinear_shows_study_glosses(client, studied):
    _, tid = studied
    html = _read(client, tid, at="1.1", interlinear="true")
    assert '<span class="w-g">μῆνιν</span><span class="w-t">wrath</span>' in html
    assert '<span class="w-t">he spoke</span>' in html
    assert html.count('class="line has-glosses"') == 3


def test_line_missing_a_word_gloss_is_not_covered(client, studied):
    db, tid = studied
    db.execute(
        "DELETE FROM word_study WHERE text_id=? AND line_number=3 AND token_index=1",
        (tid,),
    )
    db.commit()
    html = _read(client, tid, at="1.1")
    assert 'class="line" data-line="3"' in html
    assert "has-glosses" not in _line_html(html, 3)


def test_another_models_study_data_is_not_used(client, iliad):
    db, tid = iliad
    _save_unit(db, tid, 1, 2, model="claude-sonnet-5-5")
    html = _read(client, tid, at="1.1")
    assert "has-glosses" not in html
    assert "wrath" not in html
    box = _passage(client, tid, "1.1-1.2")
    assert "passage-box is-none" in box


# --- gloss sources --------------------------------------------------------------


# --- word strip ------------------------------------------------------------------


def test_word_strip_shows_the_study_gloss_and_note(client, studied):
    _, tid = studied
    html = _word(client, tid, 1, 0)
    assert '<div class="ws-meaning">wrath</div>' in html
    assert '<p class="ws-note-text">The first word of the poem.</p>' in html
    assert "Gloss and note · Claude Opus 5.5" in html
    assert ">Perseus treebank</span>" in html  # the parsing is the treebank's
    assert "Parse check" not in html


def test_word_strip_shows_the_parse_check_under_the_parse(client, studied):
    _, tid = studied
    html = _word(client, tid, 2, 1)
    assert "<dt>Mood</dt><dd>indicative</dd>" in html  # the treebank's parse
    check = html.index('<div class="ws-check">')
    assert html.index("<dt>Mood</dt>") < check < html.index('class="ws-col ws-note"')
    assert '<h4 class="label">Parse check · Claude Opus 5.5</h4>' in html
    assert "<p>imperative, not indicative: addressed to the Muses</p>" in html
    # an empty note
    assert "No note for this word." in html
    assert "No gloss or note is saved" not in html


def test_word_strip_split_word_has_one_gloss(client, studied):
    _, tid = studied
    html = _word(client, tid, 2, 0)
    assert '<div class="ws-meaning">nor</div>' in html
    assert html.count('<div class="ws-part">') == 2


# --- passage box ---------------------------------------------------------------


def test_selection_shows_the_study_translations(client, studied):
    _, tid = studied
    html = _passage(client, tid, "1.1-1.2")
    assert "passage-box is-study has-tabs" in html
    assert "Claude Opus 5.5 · study data" in html
    assert 'data-tab="literal" disabled' not in html
    assert (
        'Fluent translation of <span class="ref">1.1–1.2</span></h3>\n'
        "        <p>Sing of the wrath, goddess; tell it now.</p>" in html
    )
    assert (
        '<li><span class="ref">1.1</span><span class="literal-text">literal 1.1</span>'
        in html
    )
    assert '<span class="literal-text">literal 1.2</span>' in html
    assert "literal 1.3" not in html
    assert "Analyze" not in html and "pb-ask" not in html


def test_selection_inside_a_unit_shows_the_whole_unit(client, studied):
    _, tid = studied
    html = _passage(client, tid, "1.2-1.2")
    assert 'Fluent translation of <span class="ref">1.1–1.2</span>' in html
    assert '<span class="literal-text">literal 1.2</span>' in html
    assert "literal 1.1" not in html  # literal lines are exact
    assert "Grammar (3)" in html


def test_selection_over_two_units_shows_both(client, studied):
    _, tid = studied
    html = _passage(client, tid, "1.2-1.3")
    first = html.index('Fluent translation of <span class="ref">1.1–1.2</span>')
    second = html.index('Fluent translation of <span class="ref">1.3</span>')
    assert first < second
    assert "So he spoke." in html
    assert html.count('<span class="literal-text">') == 2


def test_all_words_from_the_study_data(client, studied):
    _, tid = studied
    html = _passage(client, tid, "1.1-1.2")
    assert "Grammar (6)" in html
    assert '<span class="wt-meaning" role="cell">wrath</span>' in html
    assert (
        '<span class="wt-note" role="cell">The first word of the poem.</span>' in html
    )
    assert (
        '<span class="wt-check"><span class="visually-hidden">Parse check by Claude '
        "Opus 5.5: </span>imperative, not indicative: addressed to the Muses</span>"
        in html
    )


def test_partly_covered_selection_has_no_translation(client, iliad):
    db, tid = iliad
    _save_unit(db, tid, 1, 2)
    html = _passage(client, tid, "1.2-1.3")
    assert "passage-box is-none has-tabs" in html
    assert "No translation is saved for these lines yet" in html
    assert 'data-tab="literal" disabled' in html
    # words with study data still get its gloss and note
    assert '<span class="wt-meaning" role="cell">tell</span>' in html
    assert "imperative, not indicative" in html


def test_deleting_the_text_removes_its_study_data(client, studied):
    db, tid = studied
    db.execute("DELETE FROM texts WHERE id=?", (tid,))
    db.commit()
    for table in ("translation_units", "line_translations", "word_study"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
