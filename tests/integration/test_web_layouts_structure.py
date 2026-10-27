"""Optional browser regressions for sentence structure in Grammar (see
test_web_layouts.py): the In order /
Structure switch, its memory and its place at the top as the words scroll,
the outline's rows (opening a word, lighting its words in the text, folding,
the keyboard), depth markers in a narrow box and the folded sentence text on
iPad portrait, at the four viewports.

One treebank sentence runs from 1.4 to 1.7 and nests six deep; the
selection is 1.4–1.5, so the words of 1.6 and 1.7 are outside it.
"""

import re

import pytest

from greek.db.repos.parses import save_treebank
from greek.models import TreebankNode, TreebankSentence, WordParse

pw = pytest.importorskip("playwright.sync_api")

from tests.integration import test_web_layouts as layouts  # noqa: E402
from tests.integration import test_web_layouts_study as study  # noqa: E402

browser = layouts.browser
live_reader = layouts.live_reader
_open = study._open

SENTENCE = 7
# (node, line, token, head, relation, postag): every line is "λόγος ἔργον"
TREE = [
    (1, 4, 0, 0, "PRED", "v3saia---"),
    (2, 4, 1, 1, "SBJ", "n-s---nn-"),
    (3, 5, 0, 1, "OBJ", "n-s---ma-"),
    (4, 5, 1, 3, "ATR", "n-s---ng-"),
    (5, 6, 0, 4, "ATR", "n-s---mg-"),
    (6, 6, 1, 5, "ATR", "n-s---ng-"),
    (7, 7, 0, 6, "ATR", "n-s---mg-"),
    (8, 7, 1, 7, "ATR", "n-s---ng-"),
]
NARROW = {(1824, 1024), (1373, 789)}  # the passage box is 540–560 px wide


@pytest.fixture
def outline_reader(live_reader, web_db):
    tid = int(live_reader.split("/texts/")[1].split("/")[0])
    forms = ["λόγος", "ἔργον"]
    save_treebank(
        web_db,
        tid,
        [TreebankSentence(tid, SENTENCE, 4, 7)],
        [
            WordParse(tid, line, i, 0, forms[i], forms[i], tag, SENTENCE, node_id=n)
            for n, line, i, _, _, tag in TREE
        ],
        [
            TreebankNode(tid, SENTENCE, n, head, rel, forms[i], forms[i], tag)
            for n, _, i, head, rel, tag in TREE
        ],
    )
    return live_reader + "&lines=1.4-1.5"


@pytest.fixture
def long_reader(live_reader, web_db):
    """Thirty one-line sentences, 1.4–1.33: more than the Grammar tab shows at once."""
    tid = int(live_reader.split("/texts/")[1].split("/")[0])
    forms = ["λόγος", "ἔργον"]
    sentences, parses, nodes = [], [], []
    for line in range(4, 34):
        sid = 100 + line
        sentences.append(TreebankSentence(tid, sid, line, line))
        for n, head, rel, tag in [
            (1, 0, "PRED", "v3saia---"),
            (2, 1, "SBJ", "n-s---nn-"),
        ]:
            form = forms[n - 1]
            parses.append(
                WordParse(tid, line, n - 1, 0, form, form, tag, sid, node_id=n)
            )
            nodes.append(TreebankNode(tid, sid, n, head, rel, form, form, tag))
    save_treebank(web_db, tid, sentences, parses, nodes)
    return live_reader + "&lines=1.4-1.33"


def _all_words(page, touch):
    page.locator("#reader-tab-words" if touch else "#tab-words").click()
    pw.expect(page.locator("#panel-words")).to_be_visible()


def _by_sentence(page):
    page.locator("[data-words-view='sentence']").click()
    pw.expect(page.locator(".st-outline")).to_be_visible()


def _in_order(page):
    page.locator("[data-words-view='order']").click()
    pw.expect(page.locator("#panel-words .words-table")).to_be_visible()


def _scroll_words(page, top):
    page.locator("#panel-words").evaluate("(el, top) => { el.scrollTop = top; }", top)
    # the scroll event reaches reader.js on the next frame
    page.evaluate(
        "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
    )


def _words_top(page):
    return page.locator("#panel-words").evaluate("el => el.scrollTop")


def _leave_grammar(page, touch):
    if touch:
        page.locator("#reader-tab-word").click()
        pw.expect(page.locator("#panel-word")).to_be_visible()
    else:
        page.locator("#tab-fluent").click()
        pw.expect(page.locator("#panel-fluent")).to_be_visible()
    pw.expect(page.locator("#panel-words")).to_be_hidden()


def _item(page, node):
    line, token = next((line, i) for n, line, i, *_ in TREE if n == node)
    return page.locator(f'.st-item[data-line="{line}"][data-i="{token}"]')


def _word(page, line, i):
    return page.locator(f'#text-lines .line[data-line="{line}"] .w[data-i="{i}"]')


def _lit(page):
    return page.locator("#text-lines .w.st-own, #text-lines .w.st-dep").evaluate_all(
        """els => els.map(e => e.closest('.line').dataset.line + ':' + e.dataset.i
            + (e.classList.contains('st-own') ? ' own' : ''))"""
    )


@pytest.mark.parametrize("width, height, touch", layouts.VIEWPORTS)
def test_the_switch_shows_the_outline_and_is_remembered(
    browser, outline_reader, width, height, touch
):
    context, page, errors, blocked = _open(
        browser, outline_reader, width, height, touch
    )
    try:
        page.goto(outline_reader)
        _all_words(page, touch)
        table = page.locator("#panel-words .words-table")
        pw.expect(table).to_be_visible()
        pw.expect(page.locator(".st-outline")).to_be_hidden()
        pw.expect(table.locator(".wt-role").first).to_contain_text("main verb")
        _by_sentence(page)
        pw.expect(table).to_be_hidden()
        pw.expect(page.locator("[data-words-view='sentence']")).to_have_attribute(
            "aria-checked", "true"
        )
        head = page.locator(".st-head")
        pw.expect(head).to_contain_text("1.4–1.7")
        pw.expect(head.locator(".src")).to_have_text("source: Perseus")
        # the rows of 1.6 and 1.7 are outside the selection
        pw.expect(_item(page, 3).locator(":scope > .st-row")).not_to_have_class(
            "st-row is-out"
        )
        pw.expect(_item(page, 5).locator(":scope > .st-row")).to_have_class(
            "st-row is-out"
        )
        # deep rows: indentation stops at four levels in a narrow box, six in a
        # wide one, and the rows deeper say by how much
        markers = page.locator(".st-deep").evaluate_all(
            "els => els.filter(e => e.getClientRects().length).map(e => e.textContent)"
        )
        assert markers == (["+1", "+2"] if (width, height) in NARROW else [])
        # the sentence's text folds on iPad portrait only
        text = page.locator(".st-text")
        if width < 1100:
            pw.expect(text).to_be_hidden()
            toggle = page.locator(".st-text-toggle")
            toggle.click()
            pw.expect(text).to_be_visible()
            pw.expect(toggle).to_have_attribute("aria-expanded", "true")
            pw.expect(toggle).to_have_text("Hide the sentence")
        else:
            pw.expect(text).to_be_visible()
            pw.expect(page.locator(".st-text-toggle")).to_be_hidden()
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
        wide = page.locator(".st-row, .st-head, .st-switch").evaluate_all(
            "els => els.filter(e => e.scrollWidth > e.clientWidth + 1).length"
        )
        assert wide == 0
        # a new selection, and a new page, keep Structure
        page.goto(outline_reader.replace("1.4-1.5", "1.5-1.6"))
        _all_words(page, touch)
        pw.expect(page.locator(".st-outline")).to_be_visible()
        assert errors == [] and blocked == []
    finally:
        context.close()


@pytest.mark.parametrize("width, height, touch", layouts.VIEWPORTS)
def test_a_row_opens_its_word(browser, outline_reader, width, height, touch):
    context, page, errors, blocked = _open(
        browser, outline_reader, width, height, touch
    )
    try:
        page.goto(outline_reader)
        _all_words(page, touch)
        _by_sentence(page)
        row = _item(page, 3).locator(":scope > .st-row")
        row.tap() if touch else row.click()
        strip = page.locator('#word-strip[data-line="5"][data-i="0"]')
        pw.expect(strip).to_be_attached()
        if touch:  # a tablet shows the Word tab
            pw.expect(page.locator("#reader-tab-word")).to_have_attribute(
                "aria-selected", "true"
            )
            _all_words(page, touch)
        pw.expect(_item(page, 3)).to_have_class(re.compile(r"\bis-active\b"))
        pw.expect(_word(page, 5, 0)).to_have_class(re.compile(r"\bis-active\b"))
        assert errors == [] and blocked == []
    finally:
        context.close()


@pytest.mark.parametrize("width, height, touch", layouts.VIEWPORTS)
def test_a_finger_folds_a_row_from_anywhere_left_of_its_greek(
    browser, outline_reader, width, height, touch
):
    """
    On a touch screen the fold triangle is larger, and a tap anywhere left of
    the row's Greek, the row's height and up to 44 px of its indent, folds
    the row without opening its word. With a mouse it stays the small
    triangle.
    """
    context, page, errors, blocked = _open(
        browser, outline_reader, width, height, touch
    )
    try:
        page.goto(outline_reader)
        _all_words(page, touch)
        _by_sentence(page)
        folding = _item(page, 3)  # one level down, five rows under it
        row = folding.locator(":scope > .st-row")
        row.scroll_into_view_if_needed()
        toggle = row.locator(".st-toggle")
        size = toggle.evaluate("e => getComputedStyle(e).fontSize")
        box, greek = row.bounding_box(), row.locator(".st-greek").bounding_box()
        # the row's far left, low down: well away from the triangle
        x, y = box["x"] + 2, box["y"] + box["height"] - 2
        hit = page.evaluate(
            "([x, y]) => document.elementFromPoint(x, y).matches('.st-toggle')",
            [x, y],
        )
        if touch:
            assert size == "18px" and hit
            assert greek["x"] - box["x"] >= 36
            page.touchscreen.tap(x, y)
            pw.expect(folding).to_have_attribute("aria-expanded", "false")
            pw.expect(_item(page, 4)).to_be_hidden()
            pw.expect(page.locator("#reader-tab-words")).to_have_attribute(
                "aria-selected", "true"
            )
            pw.expect(page.locator('#word-strip[data-line="5"]')).not_to_be_attached()
            toggle.tap()
            pw.expect(folding).to_have_attribute("aria-expanded", "true")
        else:
            assert size == "12px" and not hit
        assert errors == [] and blocked == []
    finally:
        context.close()


@pytest.mark.parametrize("width, height, touch", layouts.VIEWPORTS[:2])
def test_hovering_a_row_lights_its_words(browser, outline_reader, width, height, touch):
    context, page, errors, blocked = _open(
        browser, outline_reader, width, height, touch
    )
    try:
        page.goto(outline_reader)
        _all_words(page, touch)
        _by_sentence(page)
        _item(page, 5).locator(":scope > .st-row").hover()
        assert _lit(page) == ["6:0 own", "6:1", "7:0", "7:1"]
        page.locator(".st-head").hover()
        assert _lit(page) == []
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_the_outline_works_from_the_keyboard(browser, outline_reader):
    context, page, errors, blocked = _open(browser, outline_reader, 2560, 1324, False)
    try:
        page.goto(outline_reader)
        _all_words(page, False)
        # the switch is a radio group
        page.locator("[data-words-view='order']").focus()
        page.keyboard.press("ArrowRight")
        pw.expect(page.locator(".st-outline")).to_be_visible()
        pw.expect(page.locator("[data-words-view='sentence']")).to_be_focused()
        # the tree is one tab stop, its first row
        top = _item(page, 1)
        pw.expect(top).to_have_attribute("tabindex", "0")
        top.focus()
        assert _lit(page)[0] == "4:0 own"  # focus lights the row's words too
        page.keyboard.press("ArrowDown")
        pw.expect(_item(page, 2)).to_be_focused()
        pw.expect(_item(page, 2)).to_have_attribute("tabindex", "0")
        pw.expect(top).to_have_attribute("tabindex", "-1")
        page.keyboard.press("ArrowDown")
        folding = _item(page, 3)  # five rows under it
        pw.expect(folding).to_be_focused()
        pw.expect(folding).to_have_attribute("aria-expanded", "true")
        page.keyboard.press("ArrowLeft")
        pw.expect(folding).to_have_attribute("aria-expanded", "false")
        pw.expect(_item(page, 4)).to_be_hidden()
        pw.expect(folding.locator(":scope > .st-row .st-more")).to_have_text(
            "+ 5 words"
        )
        page.keyboard.press("ArrowDown")  # nothing below a folded row is shown
        pw.expect(folding).to_be_focused()
        page.keyboard.press("ArrowRight")
        pw.expect(folding).to_have_attribute("aria-expanded", "true")
        page.keyboard.press("ArrowRight")
        pw.expect(_item(page, 4)).to_be_focused()
        page.keyboard.press("End")
        pw.expect(_item(page, 8)).to_be_focused()
        page.keyboard.press("ArrowLeft")  # not foldable: up to its parent
        pw.expect(_item(page, 7)).to_be_focused()
        page.keyboard.press("Home")
        pw.expect(top).to_be_focused()
        page.keyboard.press("Enter")
        pw.expect(
            page.locator('#word-strip[data-line="4"][data-i="0"]')
        ).to_be_attached()
        # the mouse folds with the toggle
        folding.locator(":scope > .st-row .st-toggle").click()
        pw.expect(folding).to_have_attribute("aria-expanded", "false")
        assert errors == [] and blocked == []
    finally:
        context.close()


@pytest.mark.parametrize("width, height, touch", layouts.VIEWPORTS)
def test_grammar_keeps_its_place_in_each_view(
    browser, long_reader, width, height, touch
):
    """
    While the same lines stay selected, each view of the Grammar tab keeps
    how far it was scrolled: through the Word tab (a tablet) or another
    passage tab, the box loading again, and a word opened from a row. A new
    selection starts at the top.
    """
    context, page, errors, blocked = _open(browser, long_reader, width, height, touch)
    try:
        page.goto(long_reader)
        _all_words(page, touch)
        _by_sentence(page)
        _scroll_words(page, 400)
        assert _words_top(page) == 400
        _leave_grammar(page, touch)
        _all_words(page, touch)
        assert _words_top(page) == 400
        # the same lines again: the box is new, the place is kept
        box = page.locator("#passage-box")
        box.evaluate("el => { el.dataset.old = '1'; }")
        box.evaluate(
            """el => htmx.ajax('GET', `/texts/${document.querySelector('#reader')
                .dataset.textId}/passage?lines=${el.dataset.start}-${el.dataset.end}`,
                { target: '#passage-box', swap: 'outerHTML' })"""
        )
        pw.expect(page.locator("#passage-box:not([data-old])")).to_be_attached()
        pw.expect(page.locator(".st-outline")).to_be_visible()
        assert _words_top(page) == 400
        # In order keeps its own place (the switch is at the top of the box)
        _in_order(page)
        assert _words_top(page) == 0
        _scroll_words(page, 250)
        _leave_grammar(page, touch)
        _all_words(page, touch)
        assert _words_top(page) == 250
        _by_sentence(page)
        _scroll_words(page, 400)
        # a row on screen opens its word (a tablet's Word tab) and stays put
        n = page.locator("#panel-words").evaluate(
            """panel => {
                const top = panel.getBoundingClientRect().top + 60;
                return [...panel.querySelectorAll('.st-row')]
                    .findIndex(r => r.getBoundingClientRect().top > top);
            }"""
        )
        row = page.locator(".st-row").nth(n)
        row.tap() if touch else row.click()
        pw.expect(page.locator("#word-strip.has-word")).to_be_attached()
        if touch:
            pw.expect(page.locator("#passage-box")).to_be_hidden()
            _all_words(page, touch)
        pw.expect(page.locator(".st-item.is-active")).to_have_count(1)
        assert _words_top(page) == 400
        # another selection, without that word (whose row would scroll into view)
        first, last = (
            page.locator(f'.line[data-line="{n}"] .line-ref') for n in (20, 33)
        )
        first.tap() if touch else first.click()
        pw.expect(
            page.locator('#passage-box[data-start="20"][data-end="20"]')
        ).to_be_attached()
        last.tap() if touch else last.click(modifiers=["Shift"])
        pw.expect(
            page.locator('#passage-box[data-start="20"][data-end="33"]')
        ).to_be_attached()
        pw.expect(page.locator(".st-outline")).to_be_visible()
        pw.expect(page.locator(".st-item.is-active")).to_have_count(0)
        assert _words_top(page) == 0
        assert errors == [] and blocked == []
    finally:
        context.close()


def _top(page, selector):
    return page.locator(selector).evaluate("el => el.getBoundingClientRect().top")


def _bottom(page, selector):
    return page.locator(selector).evaluate("el => el.getBoundingClientRect().bottom")


def _gap(page, upper, lower):
    """How far `lower`'s top is below `upper`'s bottom, both measured at once."""
    return page.evaluate(
        "([a, b]) => document.querySelector(b).getBoundingClientRect().top"
        " - document.querySelector(a).getBoundingClientRect().bottom",
        [upper, lower],
    )


@pytest.mark.parametrize(
    "width, height, touch",
    layouts.VIEWPORTS + [(393, 852, True), (360, 800, True), (852, 393, True)],
)
def test_the_switch_stays_at_the_top_as_the_words_scroll(
    browser, long_reader, width, height, touch
):
    """
    The In order / Structure switch stays at the top of the Grammar tab, with
    In order's column heads under it, and a word's row scrolled into view
    stops below both. A phone in landscape has room for the heads only: the
    switch scrolls away with the words.
    """
    context, page, errors, blocked = _open(browser, long_reader, width, height, touch)
    try:
        page.goto(long_reader)
        _all_words(page, touch)
        _scroll_words(page, 400)
        assert _words_top(page) == 400
        panel = _top(page, "#panel-words")
        if height <= 500:
            # a phone in landscape: the switch has scrolled away
            assert _bottom(page, ".st-bar") <= panel
            assert abs(_top(page, ".wt-head") - panel) < 1
        else:
            assert abs(_top(page, ".st-bar") - panel) < 1
            assert abs(_top(page, ".wt-head") - _bottom(page, ".st-bar")) < 1
            # a click on the switch there shows the other view
            switch = page.locator("[data-words-view='sentence']")
            switch.tap() if touch else switch.click()
            pw.expect(page.locator(".st-outline")).to_be_visible()
            _scroll_words(page, 400)
            assert abs(_top(page, ".st-bar") - panel) < 1
            _in_order(page)
        if not touch:  # a tablet's or phone's word opens the Word tab instead
            # a word whose row is above: the row comes in below the heads
            _scroll_words(page, 10_000)
            _word(page, 5, 0).click()
            pw.expect(page.locator(".wt-row.is-active")).to_have_count(1)
            # The word opening in the word pane moves the panel (on a monitor
            # by 79 px as it loads): measure once it has, in one call
            pw.expect(
                page.locator('#word-strip.has-word[data-line="5"][data-i="0"]')
            ).to_be_attached()
            assert -1 < _gap(page, ".wt-head", ".wt-row.is-active") < 2
        assert errors == [] and blocked == []
    finally:
        context.close()


@pytest.mark.parametrize("width, height, touch", layouts.VIEWPORTS)
def test_parsing_names_the_role_where_it_sits_under_it(
    browser, outline_reader, width, height, touch
):
    """Below a monitor, In order puts the role and use under the parsing."""
    context, page, errors, blocked = _open(
        browser, outline_reader, width, height, touch
    )
    try:
        page.goto(outline_reader)
        _all_words(page, touch)
        heads = page.locator(".wt-head [role='columnheader']")
        if width > 2100:
            pw.expect(heads.nth(2)).to_have_text("Parsing", use_inner_text=True)
            pw.expect(heads.nth(3)).to_have_text("Role · use")
        else:
            pw.expect(heads.nth(2)).to_have_text("Parsing · role", use_inner_text=True)
            pw.expect(heads.nth(3)).to_be_hidden()
        assert errors == [] and blocked == []
    finally:
        context.close()
