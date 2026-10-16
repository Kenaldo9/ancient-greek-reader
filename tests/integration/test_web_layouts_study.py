"""Optional browser regressions for treebank words in the reader (see test_web_layouts.py).

Lines 1.1–1.3 have the study data; 1.4–1.6 have treebank parses and LSJ
entries (and no study data), so they have Grammar but no Literal tab.
"""

import pytest

from greek.db.repos.lexicon import save_lexicon
from greek.db.repos.parses import save_treebank
from greek.models import LexiconEntry, WordParse

pw = pytest.importorskip("playwright.sync_api")

from tests.integration import test_web_layouts as layouts  # noqa: E402

# The responsive reader's fixtures: its browser and live server with one
# passage of study data
browser = layouts.browser
live_reader = layouts.live_reader


@pytest.fixture
def study_reader(live_reader, web_db):
    tid = int(live_reader.split("/texts/")[1].split("/")[0])
    parses = []
    for line in (4, 5, 6):
        parses.append(WordParse(tid, line, 0, 0, "λόγος", "λόγος", "n-s---mn-", line))
        parses.append(WordParse(tid, line, 1, 0, "ἔργον", "ἔργον", "n-s---na-", line))
    save_treebank(web_db, tid, [], parses)
    entries = [
        LexiconEntry("LSJ", "lo/gos", "λόγος", "word", "<p><em>word</em>, speech</p>"),
        LexiconEntry("LSJ", "e)/rgon", "ἔργον", "work", "<p><em>work</em>, deed</p>"),
    ]
    save_lexicon(
        web_db,
        "LSJ",
        entries,
        {"λόγος": ("lo/gos", "headword"), "ἔργον": ("e)/rgon", "headword")},
    )
    return live_reader


def _open(browser, url, width, height, touch):
    context = browser.new_context(
        viewport={"width": width, "height": height}, has_touch=touch
    )
    page = context.new_page()
    errors, blocked = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    origin = url.split("/texts/")[0]

    def only_local_reads(route):
        req = route.request
        if req.method != "GET" or not req.url.startswith(origin):
            blocked.append(f"{req.method} {req.url}")
            route.abort()
        else:
            route.continue_()

    page.route("**/*", only_local_reads)
    return context, page, errors, blocked


def test_tablet_all_words_without_study_data(browser, study_reader):
    context, page, errors, blocked = _open(browser, study_reader, 1373, 789, True)
    try:
        page.goto(study_reader + "&lines=1.4-1.5")
        pw.expect(page.locator("#passage-box.has-tabs")).to_be_attached()
        pw.expect(page.locator("#reader-tab-literal")).to_be_disabled()
        pw.expect(page.locator("#reader-tab-words")).to_be_enabled()
        pw.expect(page.locator("#reader-tab-words")).to_have_text("Grammar (4)")
        pw.expect(page.locator("#panel-fluent")).to_contain_text(
            "No translation is saved for these lines yet"
        )
        page.locator("#reader-tab-words").click()
        pw.expect(page.locator("#panel-words")).to_be_visible()
        pw.expect(page.locator('.wt-row[data-line="4"]').first).to_contain_text(
            "noun · nom. sg. masc."
        )
        # A word from the table opens the Word tab with the treebank parse
        page.locator('.wt-row[data-line="4"]').first.click()
        pw.expect(page.locator("#word-strip")).to_contain_text("Perseus treebank")
        pw.expect(page.locator("#passage-box")).to_be_hidden()
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_remembered_literal_tab_falls_back_without_being_forgotten(
    browser, study_reader
):
    context, page, errors, blocked = _open(browser, study_reader, 2560, 1324, False)
    try:
        page.goto(study_reader + "&lines=1.1-1.3")
        page.locator("#tab-literal").click()
        pw.expect(page.locator("#panel-literal")).to_be_visible()
        # Lines without study data have no Literal tab: Fluent shows instead
        page.locator('.line[data-line="4"] .line-ref').click()
        page.locator('.line[data-line="5"] .line-ref').click(modifiers=["Shift"])
        pw.expect(
            page.locator('#passage-box[data-start="4"][data-end="5"]')
        ).to_be_visible()
        pw.expect(page.locator("#tab-literal")).to_be_disabled()
        pw.expect(page.locator("#panel-fluent")).to_be_visible()
        # …and the choice comes back for lines with study data
        page.locator('.line[data-line="1"] .line-ref').click()
        page.locator('.line[data-line="3"] .line-ref').click(modifiers=["Shift"])
        pw.expect(page.locator('#passage-box.is-study[data-start="1"]')).to_be_visible()
        pw.expect(page.locator("#panel-literal")).to_be_visible()
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_monitor_word_strip_shows_the_full_lsj_entry(browser, study_reader):
    context, page, errors, blocked = _open(browser, study_reader, 2560, 1324, False)
    try:
        page.goto(study_reader + "&lines=1.4-1.5")
        page.locator('.line[data-line="5"] .w').first.click()
        strip = page.locator("#word-strip.has-word")
        pw.expect(strip).to_contain_text("Parsing · Perseus treebank")
        pw.expect(strip.locator(".ws-dict-line")).to_have_text("LSJ λόγος word")
        # A monitor has room for the entry beside the word, without a click
        pw.expect(strip.locator(".lsj-entry")).to_be_visible()
        pw.expect(strip.locator(".lsj-entry")).to_contain_text("word, speech")
        width = page.evaluate("document.documentElement.scrollWidth")
        assert width == 2560
        assert errors == [] and blocked == []
    finally:
        context.close()
