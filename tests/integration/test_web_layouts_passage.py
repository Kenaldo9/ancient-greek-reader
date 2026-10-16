"""Optional browser regressions for the expanded passage box (see
test_web_layouts.py): on a laptop, Expand makes the passage box fill the study
column, with the word folded into a line or two above it. Monitors and tablets
have no Expand button. The data is test_web_layouts_dictionary.py's: 1.7
starts with a verb with an LSJ entry and principal parts.
"""

import pytest

pw = pytest.importorskip("playwright.sync_api")

from tests.integration import test_web_layouts as layouts  # noqa: E402
from tests.integration import test_web_layouts_dictionary as dictionary  # noqa: E402
from tests.integration import test_web_layouts_study as study  # noqa: E402

browser = layouts.browser
live_reader = layouts.live_reader
dict_reader = dictionary.dict_reader
_open = study._open
_show_verb = dictionary._show_verb
_height = dictionary._height
_fits = dictionary._fits


def _expanded(page):
    return page.locator("#reader.passage-expanded")


def _select(page, first, last):
    page.locator(f'.line[data-line="{first}"] .line-ref').click()
    page.locator(f'.line[data-line="{last}"] .line-ref').click(modifiers=["Shift"])
    pw.expect(page.locator(f'#passage-box[data-start="{first}"]')).to_be_visible()


def test_laptop_passage_fills_the_column(browser, dict_reader):
    context, page, errors, blocked = _open(browser, dict_reader, 1824, 1024, False)
    try:
        page.goto(dict_reader + "&lines=1.4-1.5")
        strip = _show_verb(page, False)
        unfold = page.get_by_role("button", name="Show word")
        pw.expect(unfold).to_be_hidden()
        toggle = page.locator("#passage-box .pb-expand")
        pw.expect(toggle).to_have_accessible_name("Expand")
        pw.expect(toggle).to_have_attribute("aria-expanded", "false")
        before = _height(page, "#passage-box")
        toggle.click()
        pw.expect(_expanded(page)).to_be_attached()
        pw.expect(toggle).to_have_accessible_name("Collapse")
        pw.expect(toggle).to_have_attribute("aria-expanded", "true")
        assert _height(page, "#passage-box") > before + 250
        # The word folds into its summary, beside Show word
        pw.expect(strip.locator(".ws-parse")).to_be_hidden()
        pw.expect(strip.locator(".ws-dict-col")).to_be_hidden()
        pw.expect(strip.locator(".ws-summary")).to_have_text(
            "1st person · singular · present · indicative · active"
        )
        pw.expect(unfold).to_be_visible()
        button = unfold.bounding_box()
        for part in (".label", ".ws-meaning", ".ws-summary"):
            box = strip.locator(f".ws-word {part}").bounding_box()
            assert box["x"] + box["width"] <= button["x"]
        assert _fits(page)

        # Another word keeps the passage expanded and shows in the fold
        page.locator('.line[data-line="4"] .w').first.click()
        pw.expect(page.locator("#word-strip .ws-dict-line")).to_be_attached()
        pw.expect(page.locator("#word-strip .ws-greek")).to_have_text("λόγος")
        pw.expect(page.locator("#word-strip .ws-parse")).to_be_hidden()
        pw.expect(_expanded(page)).to_be_attached()
        # So do new lines, and the choice is remembered
        _select(page, 1, 3)
        pw.expect(_expanded(page)).to_be_attached()
        pw.expect(toggle).to_have_attribute("aria-expanded", "true")
        page.reload()
        pw.expect(page.locator('#passage-box[data-start="1"]')).to_be_visible()
        pw.expect(_expanded(page)).to_be_attached()
        pw.expect(toggle).to_have_accessible_name("Collapse")
        # Without a word there is nothing to unfold
        pw.expect(page.locator("#word-strip .ws-empty")).to_be_visible()
        pw.expect(unfold).to_be_hidden()

        # A monitor has room for both boxes
        page.set_viewport_size({"width": 2560, "height": 1324})
        pw.expect(_expanded(page)).to_have_count(0)
        pw.expect(toggle).to_be_hidden()
        page.set_viewport_size({"width": 1824, "height": 1024})
        pw.expect(_expanded(page)).to_be_attached()

        # Show word unfolds it and gives the focus to Expand
        _show_verb(page, False)
        unfold.click()
        pw.expect(_expanded(page)).to_have_count(0)
        pw.expect(page.locator("#word-strip .ws-parse")).to_be_visible()
        pw.expect(toggle).to_be_focused()
        pw.expect(toggle).to_have_accessible_name("Expand")
        pw.expect(toggle).to_have_attribute("aria-expanded", "false")
        # Escape folds it too, before it clears the selection
        toggle.click()
        pw.expect(_expanded(page)).to_be_attached()
        page.keyboard.press("Escape")
        pw.expect(_expanded(page)).to_have_count(0)
        pw.expect(page.locator('#passage-box[data-start="1"]')).to_be_visible()
        # Without selected lines there is no passage to expand
        toggle.click()
        page.locator("#selection-clear").click()
        pw.expect(page.locator("#passage-box:not([data-start])")).to_be_visible()
        pw.expect(_expanded(page)).to_have_count(0)
        pw.expect(page.locator("#word-strip .ws-parse")).to_be_visible()
        _select(page, 4, 5)
        pw.expect(_expanded(page)).to_be_attached()

        # The full LSJ entry still fills the column once the word unfolds
        unfold.click()
        page.locator("#word-strip .ws-lsj-toggle").click()
        pw.expect(page.locator("#reader.dict-open")).to_be_attached()
        pw.expect(_expanded(page)).to_have_count(0)
        pw.expect(page.locator("#passage-bar")).to_be_visible()
        assert errors == [] and blocked == []
    finally:
        context.close()


@pytest.mark.parametrize(
    "width, height, touch",
    [(2560, 1324, False), (1373, 789, True), (954, 1208, True)],
)
def test_only_a_laptop_expands_the_passage(browser, dict_reader, width, height, touch):
    context, page, errors, blocked = _open(browser, dict_reader, width, height, touch)
    try:
        # even with the choice remembered from a laptop
        page.add_init_script("localStorage.setItem('greek.passageExpanded', '1')")
        page.goto(dict_reader + "&lines=1.4-1.5")
        pw.expect(page.locator('#passage-box[data-start="4"]')).to_be_attached()
        _show_verb(page, touch)
        pw.expect(page.locator("#passage-box .pb-expand")).to_be_hidden()
        pw.expect(page.locator(".word-unfold")).to_be_hidden()
        pw.expect(_expanded(page)).to_have_count(0)
        assert errors == [] and blocked == []
    finally:
        context.close()
