"""Optional browser regressions for word details in the word strip (see
test_web_layouts.py): the Syntax
block, the Parsing | Syntax tabs on a laptop or tablet, the word a role names
outlined in the text, the frequency, the dictionary form and the principal
part a form comes from, a word's use label and a verb's construction, at the
four viewports.

One treebank sentence runs from 1.4 to 1.40: λόγος (1.4) is the object of the
verb on 1.40, which is off screen when the page opens at 1.1.
"""

import pytest

from greek.db.repos.lexicon import save_lexicon
from greek.db.repos.parses import save_treebank
from greek.db.repos.study import save_forms_result, save_uses_result
from greek.db.repos.verbs import save_verb_parts
from greek.models import (
    LemmaForms,
    LexiconEntry,
    TreebankNode,
    TreebankSentence,
    WordParse,
    WordUse,
)

pw = pytest.importorskip("playwright.sync_api")

from tests.integration import test_web_layouts as layouts  # noqa: E402
from tests.integration import test_web_layouts_study as study  # noqa: E402

browser = layouts.browser
live_reader = layouts.live_reader
_open = study._open

OPUS = "claude-opus-5-5"
SENTENCE = 7
PARTS = [
    {"part": n, "name": "", "form": form, "notes": ""}
    for n, form in enumerate(["ἔρδω", "ἔρξω", "ἔρξα", "ἔοργα", "—", "ἐρέχθην"], 1)
]
TABBED = layouts.VIEWPORTS[1:]  # laptop and both iPads


@pytest.fixture
def syntax_reader(live_reader, web_db):
    tid = int(live_reader.split("/texts/")[1].split("/")[0])

    def word(line, token, form, lemma, postag, node=None):
        sentence = SENTENCE if node else None
        return WordParse(
            tid, line, token, 0, form, lemma, postag, sentence, node_id=node
        )

    def node(number, head, relation, form, lemma, postag):
        return TreebankNode(tid, SENTENCE, number, head, relation, form, lemma, postag)

    save_treebank(
        web_db,
        tid,
        [TreebankSentence(tid, SENTENCE, 4, 40)],
        [
            word(4, 0, "λόγος", "λόγος", "n-s---ma-", 1),
            word(4, 1, "ἔργον", "ἔργον", "n-s---nn-", 2),
            word(40, 1, "ἔρεξε", "ἔρδω", "v3saia---", 3),
            word(41, 0, "λόγος", "λόγος", "n-s---mn-"),
            word(42, 0, "λόγος", "λόγος", "n-s---mn-"),
        ],
        [
            node(1, 3, "OBJ", "λόγος", "λόγος", "n-s---ma-"),
            node(2, 3, "SBJ", "ἔργον", "ἔργον", "n-s---nn-"),
            node(3, 0, "PRED", "ἔρεξε", "ἔρδω", "v3saia---"),
        ],
    )
    save_lexicon(
        web_db,
        "LSJ",
        [
            LexiconEntry("LSJ", "lo/gos", "λόγος", "word", "<p><em>word</em></p>"),
            LexiconEntry("LSJ", "e)/rgon", "ἔργον", "work", "<p><em>work</em></p>"),
            LexiconEntry("LSJ", "e)/rdw", "ἔρδω", "do", "<p><em>do</em></p>"),
        ],
        {
            "λόγος": ("lo/gos", "headword"),
            "ἔργον": ("e)/rgon", "headword"),
            "ἔρδω": ("e)/rdw", "headword"),
        },
    )
    save_forms_result(
        web_db, [LemmaForms("λόγος", OPUS, "noun", "λόγος, -ου, ὁ", "gen. -οιο")]
    )
    save_verb_parts(web_db, "ἔρδω", OPUS, PARTS)
    return live_reader


def _click(page, line, i, touch):
    word = page.locator(f'.line[data-line="{line}"] .w[data-i="{i}"]')
    word.tap() if touch else word.click()
    strip = page.locator(f'#word-strip[data-line="{line}"][data-i="{i}"]')
    pw.expect(strip).to_be_visible()
    return strip


def _head(page):
    return page.locator('.line[data-line="40"] .w[data-i="1"]')


def _in_view(page, locator):
    return locator.evaluate(
        """el => {
            const r = el.getBoundingClientRect();
            const s = el.closest('.text-scroll').getBoundingClientRect();
            return r.top >= s.top && r.bottom <= s.bottom;
        }"""
    )


def _no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
    wide = page.locator(
        "#word-strip .ws-col, #word-strip .syn-grid, #word-strip .ws-ptabs, "
        "#word-strip .ws-constr"
    ).evaluate_all(
        "els => els.filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.className)"
    )
    assert wide == []


def test_monitor_shows_syntax_under_the_parsing(browser, syntax_reader):
    context, page, errors, blocked = _open(browser, syntax_reader, 2560, 1324, False)
    try:
        page.goto(syntax_reader)
        strip = _click(page, 4, 0, False)
        syntax = strip.locator(".ws-syntax")
        pw.expect(syntax).to_be_visible()
        pw.expect(syntax.locator(".label")).to_have_text("Syntax")
        pw.expect(syntax).to_contain_text("object of ἔργον (1.40)")
        pw.expect(strip.locator(".ws-ptabs")).to_be_hidden()
        pw.expect(strip.locator(".ws-ppanel-source")).to_be_hidden()
        grid = strip.locator(".ws-ppanel .parse-grid").bounding_box()
        assert syntax.bounding_box()["y"] >= grid["y"] + grid["height"]
        # the word's frequency, and its lemma's dictionary form
        pw.expect(strip.locator(".ws-dict-line")).to_have_text("LSJ λόγος, -ου, ὁ word")
        pw.expect(strip.locator(".ws-freq")).to_have_text(
            "3× in the Iliad · 3× in Book 1"
        )
        pw.expect(strip.locator(".ws-dict-forms")).to_have_text(
            "gen. -οιο · form: Claude Opus 5.5"
        )
        # The word the role names is outlined, off screen until its name is clicked
        head = _head(page)
        pw.expect(head).to_have_class("w is-head")
        pw.expect(page.locator(".w.is-head")).to_have_count(1)
        assert not _in_view(page, head)
        ref = syntax.locator(".syn-ref")
        ref.hover()
        pw.expect(head).to_have_class("w is-head is-pointed")
        page.mouse.move(5, 5)
        pw.expect(head).to_have_class("w is-head")
        ref.click()
        page.wait_for_function(
            """() => {
                const el = document.querySelector('.line[data-line="40"] .w[data-i="1"]');
                const r = el.getBoundingClientRect();
                const s = el.closest('.text-scroll').getBoundingClientRect();
                return r.top >= s.top && r.bottom <= s.bottom;
            }"""
        )
        # in view now: clicking again doesn't scroll
        scroller = page.locator("#text-pane .text-scroll")
        top = scroller.evaluate("el => el.scrollTop")
        ref.click()
        assert scroller.evaluate("el => el.scrollTop") == top
        ref.focus()
        pw.expect(head).to_have_class("w is-head is-pointed")
        # The next word clicked takes the outline away
        _click(page, 41, 0, False)
        pw.expect(page.locator(".w.is-head")).to_have_count(0)
        pw.expect(page.locator("#word-strip .ws-syntax")).to_have_count(0)
        _no_overflow(page)
        assert errors == [] and blocked == []
    finally:
        context.close()


@pytest.mark.parametrize("width, height, touch", TABBED)
def test_parsing_and_syntax_are_tabs(browser, syntax_reader, width, height, touch):
    context, page, errors, blocked = _open(browser, syntax_reader, width, height, touch)
    try:
        page.goto(syntax_reader)
        strip = _click(page, 4, 0, touch)
        parsing = strip.locator("#ws-ptab-parsing")
        syntax_tab = strip.locator("#ws-ptab-syntax")
        pw.expect(parsing).to_have_attribute("aria-selected", "true")
        pw.expect(strip.locator(".ws-parse > .label")).to_be_hidden()
        pw.expect(strip.locator("#ws-ppanel")).to_be_visible()
        pw.expect(strip.locator("#ws-ppanel")).to_have_attribute("role", "tabpanel")
        pw.expect(strip.locator(".ws-ppanel-source")).to_have_text("Perseus treebank")
        pw.expect(strip.locator("#ws-spanel")).to_be_hidden()
        pw.expect(page.locator(".w.is-head")).to_have_count(0)  # Syntax isn't shown
        if touch:
            assert syntax_tab.bounding_box()["height"] >= 44
        syntax_tab.tap() if touch else syntax_tab.click()
        pw.expect(syntax_tab).to_have_attribute("aria-selected", "true")
        pw.expect(strip.locator("#ws-ppanel")).to_be_hidden()
        pw.expect(strip.locator("#ws-spanel")).to_be_visible()
        pw.expect(strip.locator("#ws-spanel .label")).to_be_hidden()
        pw.expect(strip.locator("#ws-spanel")).to_contain_text("object of ἔργον (1.40)")
        pw.expect(_head(page)).to_have_class("w is-head")
        # a laptop's 236px card: each label starts its line
        display = strip.locator(".syn-grid").evaluate(
            "el => getComputedStyle(el).display"
        )
        assert display == ("block" if width > 1500 else "grid")
        _no_overflow(page)
        # Arrow keys move between the tabs
        syntax_tab.focus()
        page.keyboard.press("ArrowLeft")
        pw.expect(parsing).to_be_focused()
        pw.expect(parsing).to_have_attribute("aria-selected", "true")
        pw.expect(page.locator(".w.is-head")).to_have_count(0)
        page.keyboard.press("End")
        pw.expect(syntax_tab).to_be_focused()
        pw.expect(_head(page)).to_have_class("w is-head")
        if touch:
            # another study tab covers the Syntax block, and its outline goes
            page.locator("#reader-tab-dictionary").tap()
            pw.expect(page.locator(".w.is-head")).to_have_count(0)
            page.locator("#reader-tab-word").tap()
            pw.expect(_head(page)).to_have_class("w is-head")
        # A newly clicked word opens on Parsing
        strip = _click(page, 4, 1, touch)
        pw.expect(strip.locator("#ws-ptab-parsing")).to_have_attribute(
            "aria-selected", "true"
        )
        pw.expect(strip.locator("#ws-spanel")).to_be_hidden()
        pw.expect(page.locator(".w.is-head")).to_have_count(0)
        assert errors == [] and blocked == []
    finally:
        context.close()


@pytest.mark.parametrize("width, height, touch", layouts.VIEWPORTS)
def test_a_verbs_arguments_and_principal_part(
    browser, syntax_reader, width, height, touch
):
    context, page, errors, blocked = _open(browser, syntax_reader, width, height, touch)
    try:
        page.goto(syntax_reader.replace("at=1.1", "at=1.38"))
        strip = _click(page, 40, 1, touch)
        current = strip.locator(".pp.is-current")
        pw.expect(current).to_have_count(1)
        pw.expect(current.locator(".pp-this")).to_have_text("this form")
        pw.expect(current.locator(".pp-form")).to_have_text("ἔρξα")
        if width <= 2100:
            strip.locator("#ws-ptab-syntax").click()
        syntax = strip.locator("#ws-spanel")
        pw.expect(syntax).to_contain_text("main verb")
        pw.expect(syntax.locator("dt")).to_have_text(["Role", "Subject", "Object"])
        pw.expect(syntax.locator("dd").nth(1)).to_have_text("ἔργον (1.4)")
        pw.expect(page.locator(".w.is-head")).to_have_count(0)  # it names no word
        _no_overflow(page)
        assert errors == [] and blocked == []
    finally:
        context.close()


@pytest.mark.parametrize("width, height, touch", layouts.VIEWPORTS)
def test_a_use_label_and_a_construction(
    browser, syntax_reader, web_db, width, height, touch
):
    tid = int(syntax_reader.split("/texts/")[1].split("/")[0])
    save_uses_result(
        web_db,
        tid,
        OPUS,
        40,
        40,
        [WordUse(tid, 40, 1, OPUS, "gnomic aorist, as with ἔρδω in maxims", "high")],
    )
    save_forms_result(
        web_db,
        [
            LemmaForms(
                "ἔρδω",
                OPUS,
                "verb",
                "ἔρδω",
                construction="+ double acc., do something to someone; "
                "+ acc. of the offering, sacrifice (ἱερὰ ἔρδειν)",
            )
        ],
    )
    context, page, errors, blocked = _open(browser, syntax_reader, width, height, touch)
    try:
        page.goto(syntax_reader.replace("at=1.1", "at=1.38"))
        strip = _click(page, 40, 1, touch)
        if width <= 2100:
            strip.locator("#ws-ptab-syntax").click()
        syntax = strip.locator("#ws-spanel")
        pw.expect(syntax.locator("dt")).to_have_text(
            ["Role", "Subject", "Object", "Use"]
        )
        pw.expect(syntax.locator("dd").last).to_have_text(
            "gnomic aorist, as with ἔρδω in maxims"
        )
        pw.expect(syntax.locator(".ws-source")).to_have_text(
            "Role: Perseus treebank · Use: Claude Opus 5.5"
        )
        # the model's row has its own tint, and its Greek the Greek font
        role_bg, use_bg = syntax.locator("dt").evaluate_all(
            "els => [els[0], els[3]].map(e => getComputedStyle(e).backgroundColor)"
        )
        assert role_bg != use_bg
        font = (
            syntax.locator("dd")
            .last.locator("[lang=grc]")
            .evaluate("el => getComputedStyle(el).fontFamily")
        )
        assert "Gentium" in font
        _no_overflow(page)
        # the construction, under the principal parts
        if touch:
            page.locator("#reader-tab-dictionary").tap()
        constr = strip.locator(".ws-constr")
        pw.expect(constr).to_be_visible()
        pw.expect(constr.locator(".label")).to_have_text(
            "Construction · Claude Opus 5.5"
        )
        pw.expect(constr.locator("p")).to_contain_text("+ double acc., do something")
        parts = strip.locator(".ws-parts").bounding_box()
        assert constr.bounding_box()["y"] >= parts["y"] + parts["height"]
        _no_overflow(page)
        assert errors == [] and blocked == []
    finally:
        context.close()
