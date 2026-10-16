"""Sentence structure in the passage box:
Grammar's In order table with each word's role and use, and its Structure
view, the outline of every treebank sentence a selection touches, read from
the saved trees with GET only."""

import pytest

from greek.db.repos.lexicon import save_lexicon
from greek.db.repos.parses import list_tb_sentences_between
from greek.db.repos.study import save_study_result, save_uses_result, saved_word_uses
from greek.db.repos.texts import get_text
from greek.models import (
    LexiconEntry,
    LineTranslation,
    TranslationUnit,
    WordStudy,
    WordUse,
)
from greek.web import reader_data
from greek.web.reader_data import passage_view
from tests.integration.test_glaux_import import _add_text, _import
from tests.integration.test_word_details import _import_excerpt

OPUS = "claude-opus-5-5"


@pytest.fixture
def iliad(web_db):
    """The Iliad excerpt: sentence 100 is 1.1–1.3, 200 is 1.5, 300 is 2.1–2.2."""
    return _import_excerpt(web_db)


@pytest.fixture
def thucydides(web_db):
    """GLAUx: sentence 1 (by hand) runs 1.1.1–1.1.2, sentence 2 (by GLAUx's
    parser) is in 1.1.2, sentence 3 (by hand, an elliptic verb) is 2.1.1."""
    text_id = _add_text(web_db)
    _import(web_db, text_id)
    return web_db, text_id


def _view(db, text_id, start, end):
    return passage_view(db, get_text(db, text_id), start, end)


def _rows(outline):
    return [r for top in outline.rows for r in (top, *top.descendants())]


def _by_greek(outline):
    return {r.greek: r for r in _rows(outline)}


def _passage(client, text_id, lines):
    resp = client.get(f"/texts/{text_id}/passage", params={"lines": lines})
    assert resp.status_code == 200
    return resp.text


def _use(text_id, line, i, label, model=OPUS):
    return WordUse(text_id, line, i, model, label, "high")


# --- Repositories -------------------------------------------------------------------


def test_sentences_between_lines(iliad):
    db, text_id = iliad
    assert [s.sentence_id for s in list_tb_sentences_between(db, text_id, 2, 2)] == [
        100
    ]
    found = list_tb_sentences_between(db, text_id, 3, 6)
    assert [(s.sentence_id, s.first_line, s.last_line) for s in found] == [
        (100, 1, 3),
        (200, 5, 5),
        (300, 6, 7),
    ]
    assert list_tb_sentences_between(db, text_id, 4, 4) == []


def test_saved_word_uses_prefer_the_model_then_the_newest(iliad):
    db, text_id = iliad
    save_uses_result(
        db,
        text_id,
        "other-model",
        1,
        2,
        [
            _use(text_id, 1, 0, "old", "other-model"),
            _use(text_id, 2, 3, "only other", "other-model"),
        ],
    )
    save_uses_result(db, text_id, OPUS, 1, 2, [_use(text_id, 1, 0, "chosen")])
    uses = saved_word_uses(db, text_id, 1, 2, OPUS)
    assert {place: u.use_label for place, u in uses.items()} == {
        (1, 0): "chosen",
        (2, 3): "only other",
    }
    assert saved_word_uses(db, text_id, 3, 7, OPUS) == {}


# --- The view -----------------------------------------------------------------------


def test_a_selection_shows_each_sentence_it_touches_whole(iliad):
    db, text_id = iliad
    view = _view(db, text_id, 2, 2)  # 1.2, the middle of sentence 100
    [outline] = view.outline
    assert (outline.ref_start, outline.ref_end) == ("1.1", "1.3")
    assert outline.credit.label == "Perseus treebank"
    assert not outline.credit.automatic
    # the sentence's words as printed, those outside the selection marked
    assert outline.text[:6] == [
        ("Μῆνιν", False),
        ("ἄειδε", False),
        ("θεὰ", False),
        ("Πηληϊάδεω", False),
        ("Ἀχιλῆος", False),
        ("οὐλομένην,", True),
    ]
    assert outline.text[-1] == ("προΐαψεν.", False)
    rows = _by_greek(outline)
    assert rows["ἄειδε"].key in outline.outside
    assert rows["ἔθηκε"].key not in outline.outside
    assert rows["ἣ"].role == "subject of ἔθηκε and προΐαψεν"
    assert rows["προΐαψεν"].role == "relative clause, joined by δέ"


def test_a_selection_across_sentences_lists_them_in_order(iliad):
    db, text_id = iliad
    view = _view(db, text_id, 3, 7)
    assert [o.sentence_id for o in view.outline] == [100, 200, 300]
    assert [(o.ref_start, o.ref_end) for o in view.outline] == [
        ("1.1", "1.3"),
        ("1.5", "1.5"),
        ("2.1", "2.2"),
    ]
    # an elliptic verb is an understood word, named by the words it shares
    rows = [f"{r.greek} · {r.role}" for r in _rows(view.outline[2])]
    assert rows == [
        "θεοί · subject of εὗδον and [understood verb]",
        "Ἄλλοι · agrees",
        "εὗδον · main verb",
        "understood verb · main verb, joined by τε",
        "παννύχιοι · predicative",
    ]


def test_in_order_gets_each_words_role_and_use(iliad):
    db, text_id = iliad
    save_uses_result(
        db, text_id, OPUS, 1, 2, [_use(text_id, 1, 1, "imperative of prayer")]
    )
    view = _view(db, text_id, 1, 2)
    words = {(w.line_number, w.index): w for sl in view.study for w in sl.words}
    assert words[(1, 0)].role.rows[0].text == "object of ἄειδε"
    assert words[(1, 1)].use.use_label == "imperative of prayer"
    assert words[(2, 1)].role.rows[0].text == "subject of ἔθηκε and προΐαψεν (1.3)"
    assert words[(1, 0)].use is None
    assert view.use_models == [OPUS]
    # the outline's row has the same use
    rows = _by_greek(view.outline[0])
    assert rows["ἄειδε"].use == "imperative of prayer"


def test_outline_meanings_saved_glosses_then_the_dictionary(iliad):
    db, text_id = iliad
    save_study_result(
        db,
        TranslationUnit(text_id, 2, 2, "accursed", OPUS),
        [LineTranslation(text_id, 2, "accursed", OPUS)],
        [WordStudy(text_id, 2, 5, "laid", "", None, OPUS)],
        [],
    )
    save_lexicon(
        db,
        "LSJ",
        [
            LexiconEntry("LSJ", "mh=nis", "μῆνις", "wrath, anger; rage", "<p/>"),
            LexiconEntry("LSJ", "*)axileu/s", "Ἀχιλλεύς", "Ἀχιλῆος l.c.]", "<p/>"),
        ],
        {"μῆνις": ("mh=nis", "headword"), "Ἀχιλλεύς": ("*)axileu/s", "headword")},
    )
    view = _view(db, text_id, 1, 1)  # 1.2 is outside the selection: same meanings
    rows = _by_greek(view.outline[0])
    assert (rows["ἔθηκε"].gloss, rows["ἔθηκε"].lsj) == ("laid", False)
    # LSJ's first sense, in grey
    assert (rows["Μῆνιν"].gloss, rows["Μῆνιν"].lsj) == ("wrath", True)
    # a cross-reference is no meaning
    assert (rows["Ἀχιλῆος"].gloss, rows["Ἀχιλῆος"].lsj) == ("", False)


def test_glaux_sentences_credit_the_parser_and_gloss_the_article(thucydides):
    db, text_id = thucydides
    view = _view(db, text_id, 2, 2)  # 1.1.2
    assert [o.sentence_id for o in view.outline] == [1, 2]
    by_hand, auto = view.outline
    assert (by_hand.ref_start, by_hand.ref_end) == ("1.1.1", "1.1.2")
    assert by_hand.credit.label == "GLAUx treebank" and not by_hand.credit.automatic
    assert auto.credit.automatic
    # the article reads "the", not LSJ's "the following"
    save_lexicon(
        db,
        "LSJ",
        [LexiconEntry("LSJ", "o(", "ὁ", "the following; that; he", "<p/>")],
        {"ὁ": ("o(", "headword")},
    )
    view = _view(db, text_id, 1, 2)
    article = _by_greek(view.outline[0])["τὸν"]
    assert (article.gloss, article.lsj) == ("the", True)
    # a split word's MWE part has no row (οὐδʼ)
    assert all("δʼ" not in r.greek for r in _rows(view.outline[0]))


def test_a_long_selection_shows_the_sentences_of_the_words_shown(iliad, monkeypatch):
    db, text_id = iliad
    monkeypatch.setattr(reader_data, "MAX_STUDY_WORDS", 6)
    view = _view(db, text_id, 1, 7)
    assert [sl.line.line_number for sl in view.study] == [1]
    assert [o.sentence_id for o in view.outline] == [100]
    assert view.study_left_out > 0


def test_a_text_without_a_treebank_has_no_outline(db_text):
    db, text_id = db_text
    view = _view(db, text_id, 1, 3)
    assert view.state == "none" and view.outline == [] and view.study == []


# --- The passage box ----------------------------------------------------------------


def test_all_words_has_a_switch_roles_and_an_outline(client, iliad):
    db, text_id = iliad
    save_uses_result(
        db, text_id, OPUS, 1, 2, [_use(text_id, 2, 3, "dative of disadvantage")]
    )
    html = _passage(client, text_id, "1-3")
    flat = " ".join(html.split())
    assert (
        '<div class="st-switch" role="radiogroup" aria-label="Show the words">' in html
    )
    assert 'aria-checked="true" data-words-view="order">In order</button>' in html
    assert 'data-words-view="sentence">Structure</button>' in html
    assert "Role: the treebank.</span> <span>Use: Claude Opus 5.5.</span>" in flat
    # In order: a Role · use column, the role's words in Greek, the use
    assert '<div class="words-table has-roles"' in html
    assert '<span role="columnheader">Role · use</span>' in html
    assert (
        '<span class="wt-role" role="cell"><span class="wt-role-text">object of '
        '<span lang="grc">ἄειδε</span></span></span>' in html
    )
    assert '<span class="wt-use">dative of disadvantage</span>' in html
    assert "syn-ref" not in html  # no buttons in the table
    # Structure: hidden until chosen, a tree per sentence
    assert '<div class="st-outline" data-view="sentence" hidden>' in html
    assert (
        '<h3 class="st-head"><span class="ref">1.1–1.3</span><span title="Ancient '
        'Greek Dependency Treebank 2.1, Perseus Digital Library (CC BY-SA 3.0)">'
        "Perseus treebank, by hand</span>" in html
    )
    assert '<ul class="st-rows" role="tree" aria-label="Structure of 1.1–1.3">' in html
    # the first row is the tree's tab stop; a row names its word and words
    assert (
        '<li class="st-item" role="treeitem" id="st-100-0" '
        'aria-labelledby="st-100-0-row" aria-level="1" tabindex="0" '
        'data-line="1" data-i="1" data-words="1:1">' in html
    )
    assert 'aria-level="2" aria-expanded="true" tabindex="-1"' in html  # Μῆνιν folds
    assert '<span class="st-more">+ 13 words</span>' in html
    assert '<span class="st-role">dative object</span>' in html
    assert '<span class="st-use">dative of disadvantage</span>' in html
    # an understood word in sentence 100's apposition is not shown as Greek
    assert "[0]" not in html


def test_an_automatic_sentence_says_so(client, thucydides):
    _, text_id = thucydides
    html = _passage(client, text_id, "2-2")
    assert ">GLAUx, automatic parse: roles may be wrong</span>" in html
    assert ">GLAUx treebank, by hand</span>" in html
    # the In order role of a word in it is marked too
    assert '<span class="wt-auto" title="GLAUx corpus' in html
    # words of 1.1.1 in the hand-annotated sentence are dimmed
    assert '<span class="st-out">Θουκυδίδης</span>' in html
    assert 'class="st-row is-clause is-top is-out"' in html  # ξυνέγραψε, in 1.1.1


def test_a_text_without_a_treebank_or_study_data_has_no_grammar(client, db_text):
    _, text_id = db_text
    html = _passage(client, text_id, "1-3")
    assert "Translations of the Iliad are not yet available" in html
    assert 'id="panel-words"' not in html and "st-outline" not in html
