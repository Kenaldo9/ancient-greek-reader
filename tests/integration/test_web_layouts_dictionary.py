"""Optional browser regressions for the word strip's Dictionary column (see
test_web_layouts.py): principal parts, and the full LSJ entry at the four
viewports. Lines 1.1–1.3 have the study data; 1.4–1.7 have treebank parses
and LSJ entries, and 1.7 starts with a verb whose principal parts are saved.
1.6 starts with a participle, the longest parsing ("middle/passive").
"""

import pytest

from greek.db.repos.lexicon import save_lexicon
from greek.db.repos.parses import save_treebank
from greek.db.repos.verbs import save_verb_parts
from greek.models import LexiconEntry, WordParse

pw = pytest.importorskip("playwright.sync_api")

from tests.integration import test_web_layouts as layouts  # noqa: E402
from tests.integration import test_web_layouts_study as study  # noqa: E402

browser = layouts.browser
live_reader = layouts.live_reader
_open = study._open

# A long entry, so that the scroll box and the open entry have something to show
LONG_ENTRY = "".join(
    f'<p data-level="{1 + n % 3}"><b>{n}.</b> <em>say</em>, speak, <cite>Il. 1.{n}</cite></p>'
    for n in range(1, 41)
)
PARTS = [
    {"part": n, "name": "", "form": form, "notes": note}
    for n, (form, note) in enumerate(
        [
            ("λέγω", ""),
            ("λέξω", "Attic ἐρῶ"),
            ("ἔλεξα", "Attic εἶπον"),
            ("εἴρηκα", ""),
            ("λέλεγμαι", ""),
            ("ἐλέχθην", ""),
        ],
        1,
    )
]


@pytest.fixture
def dict_reader(live_reader, web_db):
    tid = int(live_reader.split("/texts/")[1].split("/")[0])
    parses = []
    for line in (4, 5, 6, 7):
        first = {
            6: ("λέγω", "v-pppemn-"),
            7: ("λέγω", "v1spia---"),
        }.get(line, ("λόγος", "n-s---mn-"))
        parses.append(WordParse(tid, line, 0, 0, "λόγος", *first, line))
        parses.append(WordParse(tid, line, 1, 0, "ἔργον", "ἔργον", "n-s---na-", line))
    save_treebank(web_db, tid, [], parses)
    save_lexicon(
        web_db,
        "LSJ",
        [
            LexiconEntry("LSJ", "lo/gos", "λόγος", "word", "<p><em>word</em></p>"),
            LexiconEntry("LSJ", "e)/rgon", "ἔργον", "work", "<p><em>work</em></p>"),
            LexiconEntry("LSJ", "le/gw", "λέγω", "say, speak", LONG_ENTRY),
        ],
        {
            "λόγος": ("lo/gos", "headword"),
            "ἔργον": ("e)/rgon", "headword"),
            "λέγω": ("le/gw", "headword"),
        },
    )
    save_verb_parts(web_db, "λέγω", "claude-opus-5-5", PARTS)
    return live_reader


def _show_verb(page, touch):
    word = page.locator('.line[data-line="7"] .w').first
    word.tap() if touch else word.click()
    strip = page.locator("#word-strip.has-dict")
    pw.expect(strip).to_be_visible()
    return strip


def _height(page, selector):
    return page.locator(selector).evaluate("el => el.getBoundingClientRect().height")


def _fits(page):
    return page.evaluate(
        """() => document.documentElement.scrollWidth === innerWidth
                 && document.documentElement.scrollHeight === innerHeight"""
    )


def test_monitor_entry_beside_the_word_and_expand(browser, dict_reader):
    context, page, errors, blocked = _open(browser, dict_reader, 2560, 1324, False)
    try:
        page.goto(dict_reader + "&lines=1.4-1.5")
        strip = _show_verb(page, False)
        pw.expect(strip.locator(".pp")).to_have_count(6)
        pw.expect(strip.locator(".pp-list")).to_be_visible()
        pw.expect(strip.locator(".pp-list")).to_contain_text("Attic ἐρῶ")
        # Parsing sits below the word, the dictionary beside both
        word = strip.locator(".ws-word").bounding_box()
        parse = strip.locator(".ws-parse").bounding_box()
        dictionary = strip.locator(".ws-dict-col").bounding_box()
        assert parse["y"] >= word["y"] + word["height"] - 1
        assert dictionary["x"] >= word["x"] + word["width"] - 1
        scroll = strip.locator(".ws-lsj-scroll")
        pw.expect(scroll).to_be_visible()
        assert scroll.evaluate("el => el.scrollHeight > el.clientHeight")
        toggle = strip.locator(".ws-lsj-toggle")
        pw.expect(toggle).to_have_accessible_name("Expand")
        before = _height(page, "#panel-word")
        toggle.click()
        pw.expect(page.locator("#reader.dict-open")).to_be_attached()
        pw.expect(toggle).to_have_accessible_name("Collapse")
        pw.expect(toggle).to_have_attribute("aria-expanded", "true")
        assert _height(page, "#panel-word") > before + 150
        pw.expect(page.locator("#passage-box")).to_be_visible()
        pw.expect(page.locator("#passage-bar")).to_be_hidden()
        assert _fits(page)
        page.locator("#text-pane").click(position={"x": 5, "y": 5})
        page.keyboard.press("Escape")
        pw.expect(page.locator("#reader.dict-open")).to_have_count(0)
        pw.expect(toggle).to_have_accessible_name("Expand")
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_laptop_entry_fills_the_column(browser, dict_reader):
    context, page, errors, blocked = _open(browser, dict_reader, 1824, 1024, False)
    try:
        page.goto(dict_reader + "&lines=1.4-1.5")
        strip = _show_verb(page, False)
        pw.expect(strip.locator(".ws-lsj")).to_be_hidden()
        pw.expect(strip.locator(".pp-list")).to_be_visible()
        toggle = strip.locator(".ws-lsj-toggle")
        pw.expect(toggle).to_have_accessible_name("Full LSJ entry")
        toggle.click()
        pw.expect(strip.locator(".ws-lsj-scroll")).to_be_visible()
        pw.expect(toggle).to_have_accessible_name("Close LSJ entry")
        # The word shrinks to a summary; the passage box to a bar
        pw.expect(strip.locator(".ws-parse")).to_be_hidden()
        pw.expect(strip.locator(".ws-summary")).to_have_text(
            "1st person · singular · present · indicative · active"
        )
        pw.expect(strip.locator(".pp-summary")).to_contain_text(
            "λέγω, λέξω, ἔλεξα, εἴρηκα, λέλεγμαι, ἐλέχθην"
        )
        pw.expect(page.locator("#passage-box")).to_be_hidden()
        bar = page.locator("#passage-bar")
        pw.expect(bar).to_be_visible()
        pw.expect(bar).to_contain_text("Passage 1.4–1.5")
        assert _fits(page)
        # Another word keeps the entry open; Show passage brings the box back
        page.locator('.line[data-line="4"] .w').first.click()
        pw.expect(page.locator("#word-strip .ws-dict-line")).to_have_text(
            "LSJ λόγος word"
        )
        pw.expect(page.locator("#word-strip .ws-lsj-scroll")).to_be_visible()
        bar.get_by_role("button", name="Show passage").click()
        pw.expect(page.locator("#passage-box")).to_be_visible()
        pw.expect(bar).to_be_hidden()
        pw.expect(page.locator("#word-strip .ws-lsj-toggle")).to_be_focused()
        # Selecting lines shows their passage, closing an open entry
        page.locator("#word-strip .ws-lsj-toggle").click()
        pw.expect(page.locator("#passage-box")).to_be_hidden()
        page.locator('.line[data-line="1"] .line-ref').click()
        page.locator('.line[data-line="3"] .line-ref').click(modifiers=["Shift"])
        pw.expect(page.locator('#passage-box.is-study[data-start="1"]')).to_be_visible()
        pw.expect(page.locator("#reader.dict-open")).to_have_count(0)
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_tablet_dictionary_tab(browser, dict_reader):
    context, page, errors, blocked = _open(browser, dict_reader, 1373, 789, True)
    try:
        page.goto(dict_reader + "&lines=1.4-1.5")
        tab = page.locator("#reader-tab-dictionary")
        pw.expect(tab).to_be_disabled()
        strip = _show_verb(page, True)
        pw.expect(page.locator("#reader-tab-word")).to_have_attribute(
            "aria-selected", "true"
        )
        pw.expect(tab).to_be_enabled()
        pw.expect(strip.locator(".pp-list")).to_be_visible()
        pw.expect(strip.locator(".ws-lsj")).to_be_hidden()
        strip.locator(".ws-lsj-toggle").tap()
        pw.expect(tab).to_have_attribute("aria-selected", "true")
        pw.expect(tab).to_be_focused()
        pw.expect(page.locator("#panel-word")).to_have_attribute(
            "aria-labelledby", "reader-tab-dictionary"
        )
        pw.expect(strip.locator(".ws-word")).to_be_hidden()
        pw.expect(strip.locator(".ws-dict-for")).to_have_text("for λόγος · line 1.7")
        pw.expect(strip.locator(".pp-summary")).to_be_visible()
        pw.expect(strip.locator(".ws-lsj")).to_be_visible()
        pw.expect(strip.locator(".ws-lsj-toggle")).to_be_hidden()
        assert _fits(page)
        # Another word with an entry stays in the Dictionary tab…
        page.locator('.line[data-line="5"] .w').first.tap()
        pw.expect(page.locator("#word-strip .ws-dict-line")).to_have_text(
            "LSJ λόγος word"
        )
        pw.expect(tab).to_have_attribute("aria-selected", "true")
        # …and one without falls back to the Word tab
        page.locator('.line[data-line="1"] .w').first.tap()
        pw.expect(page.locator("#word-strip.has-word")).to_be_visible()
        pw.expect(page.locator("#reader-tab-word")).to_have_attribute(
            "aria-selected", "true"
        )
        pw.expect(tab).to_be_disabled()
        pw.expect(page.locator("#reader.dict-open")).to_have_count(0)
        page.locator("#reader-tab-fluent").tap()
        pw.expect(page.locator("#passage-box")).to_be_visible()
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_portrait_word_and_dictionary_side_by_side(browser, dict_reader):
    context, page, errors, blocked = _open(browser, dict_reader, 954, 1208, True)
    try:
        page.goto(dict_reader + "&lines=1.4-1.5")
        strip = _show_verb(page, True)
        word = strip.locator(".ws-word").bounding_box()
        dictionary = strip.locator(".ws-dict-col").bounding_box()
        assert dictionary["x"] >= word["x"] + word["width"] - 1
        # principal parts in three columns
        tops = strip.locator(".pp").evaluate_all(
            "els => els.map(e => Math.round(e.getBoundingClientRect().top))"
        )
        assert len(set(tops)) == 2
        page.locator("#reader-tab-dictionary").tap()
        pw.expect(strip.locator(".ws-lsj")).to_be_visible()
        assert _fits(page)
        assert errors == [] and blocked == []
    finally:
        context.close()


@pytest.mark.parametrize(
    "width, height, touch", [(2560, 1324, False), (954, 1208, True)]
)
def test_the_longest_parsing_fits_under_the_word(
    browser, dict_reader, width, height, touch
):
    # The parsing column is narrowest on a monitor and in tablet portrait
    context, page, errors, blocked = _open(browser, dict_reader, width, height, touch)
    try:
        page.goto(dict_reader + "&lines=1.4-1.5")
        word = page.locator('.line[data-line="6"] .w').first
        word.tap() if touch else word.click()
        grid = page.locator("#word-strip.has-dict .ws-parse .parse-grid")
        pw.expect(grid).to_contain_text("middle/passive")
        overflow = page.locator(
            "#word-strip .ws-col, #word-strip .parse-grid"
        ).evaluate_all(
            "els => els.filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.className)"
        )
        assert overflow == []
        assert errors == [] and blocked == []
    finally:
        context.close()
