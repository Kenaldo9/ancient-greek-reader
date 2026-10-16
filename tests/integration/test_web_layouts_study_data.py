"""Optional browser regressions for Opus study data in the reader (see
test_web_layouts.py).

Every line of the fixture has study data, in units of three lines. The reader
makes no requests but reads.
"""

import re

import pytest

from greek.api.study import MODEL as STUDY_MODEL
from greek.db.repos.parses import save_treebank
from greek.db.repos.study import save_study_result
from greek.models import LineTranslation, TranslationUnit, WordParse, WordStudy

pw = pytest.importorskip("playwright.sync_api")

from tests.integration import test_web_layouts as layouts  # noqa: E402
from tests.integration.test_web_layouts_study import _open  # noqa: E402

browser = layouts.browser
live_reader = layouts.live_reader


@pytest.fixture
def study_data_reader(live_reader, web_db):
    tid = int(live_reader.split("/texts/")[1].split("/")[0])
    parses = []
    for line in range(1, 46):
        parses.append(WordParse(tid, line, 0, 0, "λόγος", "λόγος", "n-s---mn-", line))
        parses.append(WordParse(tid, line, 1, 0, "ἔργον", "ἔργον", "n-s---na-", line))
    save_treebank(web_db, tid, [], parses)
    for first in range(1, 46, 3):
        numbers = range(first, first + 3)
        save_study_result(
            web_db,
            TranslationUnit(tid, first, first + 2, f"Fluent {first}.", STUDY_MODEL),
            [LineTranslation(tid, n, f"literal 1.{n}", STUDY_MODEL) for n in numbers],
            [
                WordStudy(tid, n, i, gloss, f"note {gloss}", check, STUDY_MODEL)
                for n in numbers
                for i, (gloss, check) in enumerate(
                    [("a word", None), ("a deed", "accusative, not nominative")]
                )
            ],
            [],
        )
    return live_reader.replace("interlinear=false", "interlinear=true")


def test_tablet_study_data(browser, study_data_reader):
    context, page, errors, blocked = _open(browser, study_data_reader, 1373, 789, True)
    try:
        page.goto(study_data_reader + "&lines=1.4-1.5")
        page.wait_for_load_state("networkidle")
        pw.expect(page.locator('.line[data-line="1"] .w-t').first).to_have_text(
            "a word"
        )
        pw.expect(page.locator("#passage-box.is-study")).to_be_attached()
        pw.expect(page.locator("#panel-fluent")).to_contain_text(
            "Fluent translation of 1.4–1.6"
        )
        pw.expect(page.locator("#reader-tab-literal")).to_be_enabled()
        page.locator("#reader-tab-literal").click()
        literal = page.locator("#panel-literal")
        pw.expect(literal).to_be_visible()
        pw.expect(literal.locator("li")).to_have_count(2)
        pw.expect(literal.locator("li").first).to_have_text("1.4literal 1.4")
        page.locator("#reader-tab-words").click()
        pw.expect(page.locator('.wt-row[data-line="4"] .wt-check').first).to_have_text(
            "Parse check by Claude Opus 5.5: accusative, not nominative"
        )
        assert page.evaluate("document.documentElement.scrollWidth") == 1373
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_monitor_shows_the_study_data(browser, study_data_reader):
    context, page, errors, blocked = _open(
        browser, study_data_reader, 2560, 1324, False
    )
    try:
        page.goto(study_data_reader + "&lines=1.1-1.3")
        box = page.locator("#passage-box")
        pw.expect(box).to_have_class(re.compile(r"\bis-study\b"))
        pw.expect(page.locator("#panel-fluent")).to_contain_text("Fluent 1.")
        # The word strip: the study gloss and note, and the parse check
        page.locator('.line[data-line="2"] .w').nth(1).click()
        strip = page.locator("#word-strip.has-word")
        pw.expect(strip.locator(".ws-meaning")).to_have_text("a deed")
        pw.expect(strip).to_contain_text("Gloss and note · Claude Opus 5.5")
        pw.expect(strip.locator(".ws-check")).to_contain_text(
            "accusative, not nominative"
        )
        pw.expect(strip.locator(".ws-note-text")).to_have_text("note a deed")
        page.wait_for_load_state("networkidle")
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_ipad_portrait_gives_a_long_note_the_whole_width(
    browser, study_data_reader, web_db
):
    # Phase 5c notes run to several sentences; in 330 px under the parsing
    # they became a tall strip running past the bottom of the Word tab
    long_note = " ".join(["A sentence of a long Phase 5c note on this word."] * 6)
    with web_db:
        web_db.execute(
            "UPDATE word_study SET note=? WHERE line_number=2 AND token_index=0",
            (long_note,),
        )
    context, page, errors, blocked = _open(browser, study_data_reader, 954, 1208, True)
    try:
        page.goto(study_data_reader + "&lines=1.1-1.3")
        page.locator('.line[data-line="2"] .w').first.click()
        page.wait_for_load_state("networkidle")
        note = page.locator("#word-strip.has-word.has-dict .ws-note-text")
        pw.expect(note).to_have_text(long_note)
        sizes = page.evaluate(
            """() => {
                const box = (s) => document.querySelector(s).getBoundingClientRect();
                return {
                    note: box('#word-strip .ws-note').width,
                    strip: box('#word-strip').width,
                    noteTop: box('#word-strip .ws-note').top,
                    dictBottom: box('#word-strip .ws-dict-col').bottom,
                    overflow: document.documentElement.scrollWidth - innerWidth,
                };
            }"""
        )
        # below both the word column and the Dictionary column, as wide as both
        assert sizes["note"] == sizes["strip"]
        assert sizes["noteTop"] >= sizes["dictBottom"]
        assert sizes["overflow"] <= 0
        page.wait_for_load_state("networkidle")
        assert errors == [] and blocked == []
    finally:
        context.close()
