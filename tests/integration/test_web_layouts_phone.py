"""Optional browser regressions for phones (see test_web_layouts.py), in
Chromium and WebKit.

On a phone, and a phone in landscape, the text takes the screen under one
slim bar and the study panel is a sheet: closed until a word or a line number
is tapped, closed again by its handle, a swipe down or Escape. Every write
request fails the test. WebKit is Safari's engine, so an iPad's layouts that
only it gets wrong are tested here too.
"""

import os
from pathlib import Path
import re

import pytest

from greek.db.repos.study import save_uses_result
from greek.models import WordUse

pw = pytest.importorskip("playwright.sync_api")

from tests.integration import test_web_layouts as layouts  # noqa: E402
from tests.integration import test_web_layouts_structure as structure  # noqa: E402

live_reader = layouts.live_reader
outline_reader = structure.outline_reader

PORTRAIT = (393, 852)
SMALL = (360, 800)
LANDSCAPE = (852, 393)


@pytest.fixture(scope="module")
def runtime():
    with pw.sync_playwright() as instance:
        yield instance


@pytest.fixture(scope="module", params=["chromium", "webkit"])
def engine(runtime, request):
    kind = getattr(runtime, request.param)
    executable = kind.executable_path
    if request.param == "chromium":
        executable = os.environ.get("GREEK_TEST_BROWSER", executable)
    if not Path(executable).is_file():
        pytest.skip(f"Playwright {request.param} is not installed")
    options = {"executable_path": executable} if request.param == "chromium" else {}
    with kind.launch(**options) as instance:
        yield instance


@pytest.fixture
def phone(engine, live_reader, request):
    width, height = getattr(request, "param", PORTRAIT)
    context = engine.new_context(
        viewport={"width": width, "height": height}, has_touch=True
    )
    page = context.new_page()
    errors, blocked = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    layouts.report_csp_violations(page, errors)
    origin = live_reader.split("/texts/")[0]

    def only_local_reads(route):
        req = route.request
        if req.method != "GET" or not req.url.startswith(origin):
            blocked.append(f"{req.method} {req.url}")
            route.abort()
        else:
            route.continue_()

    page.route("**/*", only_local_reads)
    try:
        page.goto(live_reader)
        pw.expect(page.locator("#text-lines .line").first).to_be_visible()
        yield page
        assert errors == []
        assert blocked == [], "Reading interactions attempted a write request"
    finally:
        context.close()


RECTS = """() => {
    const rect = selector => {
        const el = document.querySelector(selector);
        if (!el || !el.getClientRects().length) return null;
        const r = el.getBoundingClientRect();
        return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height };
    };
    const toolbar = document.querySelector('#text-pane > .pane-toolbar');
    return {
        width: innerWidth, height: innerHeight,
        scrollWidth: document.documentElement.scrollWidth,
        scrollHeight: document.documentElement.scrollHeight,
        bar: rect('.app-header'), menu: rect('#reader-menu'), toolbar: rect('#text-pane > .pane-toolbar'),
        fits: toolbar.scrollWidth <= toolbar.clientWidth,
        text: rect('#text-pane'), scroll: rect('#text-pane .text-scroll'), sheet: rect('#reader-side'),
    };
}"""


@pytest.mark.parametrize("phone", [PORTRAIT, SMALL, LANDSCAPE], indirect=True)
def test_the_text_takes_the_screen_under_one_bar(phone):
    box = phone.evaluate(RECTS)
    bar = 60 if box["width"] > 700 else 56
    assert box["scrollWidth"] == box["width"]
    assert box["scrollHeight"] == box["height"]
    assert box["bar"]["height"] == bar
    # the text toolbar is in the bar, right of the drawer button, and fits
    assert (box["toolbar"]["top"], box["toolbar"]["bottom"]) == (0, bar)
    assert box["menu"]["right"] < box["toolbar"]["left"]
    assert box["toolbar"]["right"] <= box["width"]
    assert box["fits"]
    # no study panel until something is tapped: the text fills the rest
    assert box["sheet"] is None
    assert box["text"] == {
        "top": bar,
        "bottom": box["height"],
        "left": 0,
        "right": box["width"],
        "height": box["height"] - bar,
    }
    pw.expect(phone.locator(".app-header .brand")).to_be_hidden()
    pw.expect(phone.locator(".app-header .main-nav")).to_be_hidden()
    nav = phone.locator("#reader-nav")
    pw.expect(nav.locator("#goto")).to_have_count(1)
    # a phone's Interlinear is in the drawer; in landscape the bar has room
    holder = nav if box["width"] <= 700 else phone.locator(".app-header")
    pw.expect(holder.locator("#interlinear-toggle")).to_have_count(1)


@pytest.mark.parametrize("phone", [PORTRAIT, LANDSCAPE], indirect=True)
def test_a_word_opens_the_sheet_above_it_and_the_handle_closes_it(phone):
    # a word under where the sheet will be: it is brought up above it
    word = phone.locator('.line[data-line="20"] .w').first
    word.scroll_into_view_if_needed()
    word.tap()
    pw.expect(phone.locator("#word-strip.has-word")).to_be_visible()
    pw.expect(phone.locator("#reader-tab-word")).to_have_attribute(
        "aria-selected", "true"
    )
    pw.expect(word).to_have_class(re.compile(r"\bis-active\b"))
    # it slides up
    phone.locator("#reader-side").evaluate(
        "el => Promise.all(el.getAnimations().map(a => a.finished))"
    )
    box = phone.evaluate(RECTS)
    assert box["sheet"]["bottom"] == box["height"]
    assert box["scroll"]["bottom"] <= box["sheet"]["top"]
    shown = word.bounding_box()
    assert box["scroll"]["top"] <= shown["y"]
    assert shown["y"] + shown["height"] <= box["scroll"]["bottom"]
    assert box["scrollWidth"] == box["width"]
    # its height: about half the screen, a bit more on a phone
    share = 0.5 if box["height"] <= 500 else 0.55
    assert abs(box["sheet"]["height"] - share * box["height"]) < 1

    phone.locator(".sheet-handle").tap()
    pw.expect(phone.locator("#reader-side")).to_be_hidden()
    pw.expect(phone.locator("#text-lines .w.is-active")).to_have_count(0)
    box = phone.evaluate(RECTS)
    assert box["text"]["bottom"] == box["height"]


@pytest.mark.parametrize("phone", [PORTRAIT, LANDSCAPE], indirect=True)
def test_line_numbers_select_in_the_sheet_and_escape_lets_go(phone):
    phone.locator('.line[data-line="2"] .line-ref').tap()
    pw.expect(phone.locator("#reader-side")).to_be_visible()
    pw.expect(phone.locator("#selection-hint")).to_be_visible()
    pw.expect(phone.locator("#reader-tab-fluent")).to_have_attribute(
        "aria-selected", "true"
    )
    phone.locator('.line[data-line="3"] .line-ref').tap()
    pw.expect(
        phone.locator('#passage-box.is-study[data-start="2"][data-end="3"]')
    ).to_be_visible()
    pw.expect(phone.locator("#panel-fluent")).to_contain_text("The word and the deed.")
    assert "lines=1.2-1.3" in phone.url
    phone.keyboard.press("Escape")
    pw.expect(phone.locator("#reader-side")).to_be_hidden()
    pw.expect(phone.locator(".line.is-selected")).to_have_count(0)
    assert "lines=" not in phone.url
    # tapping the only line selected again lets it go too
    phone.locator('.line[data-line="4"] .line-ref').tap()
    pw.expect(phone.locator("#reader-side")).to_be_visible()
    phone.locator('.line[data-line="4"] .line-ref').tap()
    pw.expect(phone.locator("#reader-side")).to_be_hidden()
    pw.expect(phone.locator(".line.is-selected")).to_have_count(0)
    # in landscape the bar has room for the selection; clearing it closes the sheet
    chip = phone.locator("#selection-chip")
    phone.locator('.line[data-line="5"] .line-ref').tap()
    if phone.viewport_size["width"] <= 700:
        pw.expect(chip).to_be_hidden()
        return
    pw.expect(chip).to_be_visible()
    chip.locator("#selection-clear").tap()
    pw.expect(phone.locator("#reader-side")).to_be_hidden()
    pw.expect(phone.locator(".line.is-selected")).to_have_count(0)


def _drag(page, selector, dy, steps=10):
    box = page.locator(selector).bounding_box()
    x, y = box["x"] + min(box["width"] / 2, 30), box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for k in range(1, steps + 1):
        page.mouse.move(x, y + dy * k / steps)
        page.wait_for_timeout(16)
    page.mouse.up()


@pytest.mark.parametrize("phone", [PORTRAIT, LANDSCAPE], indirect=True)
def test_a_swipe_down_closes_the_sheet(phone, live_reader):
    # a page opened with lines selected shows them in the sheet
    phone.goto(live_reader + "&lines=1.1-1.3")
    pw.expect(phone.locator("#reader-side")).to_be_visible()
    pw.expect(phone.locator(".line.is-selected")).to_have_count(3)
    pw.expect(phone.locator("#panel-fluent")).to_contain_text("The word and the deed.")
    # a short one on the tabs: the sheet springs back, and no tab is chosen
    _drag(phone, "#reader-tab-literal", 20)
    pw.expect(phone.locator("#reader-side")).to_be_visible()
    pw.expect(phone.locator("#reader-tab-fluent")).to_have_attribute(
        "aria-selected", "true"
    )
    assert phone.locator("#reader-side").evaluate("el => el.style.transform") == ""
    _drag(phone, ".sheet-grip", 150)
    pw.expect(phone.locator("#reader-side")).to_be_hidden()
    pw.expect(phone.locator(".line.is-selected")).to_have_count(0)
    assert "lines=" not in phone.url


def test_a_finger_swipe_closes_the_sheet(phone, live_reader):
    # real touch events, through the touch-action rules (Chromium only)
    if phone.context.browser.browser_type.name != "chromium":
        pytest.skip("Chromium's DevTools protocol dispatches the touches")
    phone.goto(live_reader + "&lines=1.1-1.3")
    cdp = phone.context.new_cdp_session(phone)

    def swipe(selector, dy, steps=10):
        box = phone.locator(selector).bounding_box()
        x, y = box["x"] + 20, box["y"] + box["height"] / 2
        cdp.send(
            "Input.dispatchTouchEvent",
            {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]},
        )
        for k in range(1, steps + 1):
            point = {"x": x, "y": y + dy * k / steps}
            cdp.send(
                "Input.dispatchTouchEvent",
                {"type": "touchMove", "touchPoints": [point]},
            )
            phone.wait_for_timeout(16)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

    swipe("#reader-tab-literal", 20)
    pw.expect(phone.locator("#reader-tab-fluent")).to_have_attribute(
        "aria-selected", "true"
    )
    pw.expect(phone.locator("#reader-side")).to_be_visible()
    swipe(".sheet-grip", 180)
    pw.expect(phone.locator("#reader-side")).to_be_hidden()
    pw.expect(phone.locator(".line.is-selected")).to_have_count(0)


def test_the_drawer_has_go_to_and_interlinear_through_pane_swaps(phone):
    phone.locator("#reader-menu").tap()
    nav = phone.locator("#reader-nav")
    pw.expect(nav).to_be_visible()
    pw.expect(nav.locator("#goto input[name='q']")).to_be_visible()
    toggle = nav.locator("#interlinear-toggle")
    pw.expect(toggle).to_be_visible()
    pw.expect(toggle).not_to_be_checked()
    toggle.tap()
    pw.expect(phone.locator("#reader")).to_have_class(re.compile(r"\binterlinear-on\b"))
    phone.keyboard.press("Escape")
    pw.expect(nav).to_be_hidden()
    pw.expect(phone.locator('.line[data-line="1"] .w-t').first).to_have_text("word")
    # search and back swap the text pane, and with it the control (out of band)
    phone.locator("#text-pane .pane-toolbar a[data-follows-position]").tap()
    pw.expect(phone.locator(".search-form input[name='q']")).to_be_visible()
    box = phone.evaluate("""() => [
        document.querySelector('.search-field').getBoundingClientRect().right,
        document.querySelector('#search-back').getBoundingClientRect().left,
        document.querySelector('#search-back').getBoundingClientRect().right,
        innerWidth]""")
    assert box[0] <= box[1] and box[2] <= box[3]  # the field ends before Back
    phone.locator("#search-back").tap()
    pw.expect(phone.locator("#text-lines")).to_be_visible()
    pw.expect(phone.locator(".app-header #interlinear-toggle")).to_have_count(0)
    pw.expect(nav.locator("#interlinear-toggle")).to_be_checked()
    pw.expect(phone.locator("#reader")).to_have_class(re.compile(r"\binterlinear-on\b"))


def test_wider_than_a_phone_the_tools_and_the_panel_come_back(phone):
    pw.expect(phone.locator("#reader-side")).to_be_hidden()
    phone.set_viewport_size({"width": 954, "height": 1208})
    pw.expect(phone.locator("#reader-side")).to_be_visible()
    pw.expect(phone.locator(".app-header #interlinear-toggle")).to_have_count(1)
    pw.expect(phone.locator("#reader-nav #goto")).to_have_count(1)
    box = phone.evaluate(RECTS)
    assert box["sheet"]["height"] == 540
    assert box["text"]["top"] == 72
    phone.set_viewport_size({"width": 1824, "height": 1024})
    pw.expect(phone.locator(".app-header #goto")).to_have_count(1)
    phone.set_viewport_size({"width": PORTRAIT[0], "height": PORTRAIT[1]})
    pw.expect(phone.locator("#reader-nav #interlinear-toggle")).to_have_count(1)
    pw.expect(phone.locator("#reader-nav #goto")).to_have_count(1)


LONG_USE = (
    "genitive of possession, with the noun it belongs to understood from the "
    "line before, as often in Homer's catalogues"
)


@pytest.mark.parametrize("phone", [(954, 1208)], indirect=True)
def test_on_ipad_portrait_a_use_that_wraps_stays_in_its_row(
    phone, outline_reader, web_db
):
    """
    The outline puts the use in a column of its own there. A label too long
    for it makes its row taller; WebKit lined an inline label up by its last
    line, so it reached into the row above.
    """
    tid = int(outline_reader.split("/texts/")[1].split("/")[0])
    model = "claude-opus-5-5"
    save_uses_result(
        web_db, tid, model, 4, 5, [WordUse(tid, 5, 0, model, LONG_USE, "high")]
    )
    phone.goto(outline_reader)
    phone.locator("#reader-tab-words").tap()
    phone.locator("[data-words-view='sentence']").tap()
    row = phone.locator('.st-item[data-line="5"][data-i="0"] > .st-row')
    use = row.locator(".st-use")
    pw.expect(use).to_have_text(LONG_USE)
    r, u = row.bounding_box(), use.bounding_box()
    assert u["height"] > 25  # it wraps
    assert r["y"] - 0.5 <= u["y"]
    assert u["y"] + u["height"] <= r["y"] + r["height"] + 0.5
