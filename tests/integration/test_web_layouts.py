"""Optional browser regressions for the responsive reader.

Run with Playwright and its Chromium browser installed:
    python -m pytest tests/integration/test_web_layouts.py -q

GREEK_TEST_BROWSER can point to an existing Chromium executable. The normal
suite skips this module if Playwright or its browser is unavailable. All data
is in memory; the reader only reads, so every write request is blocked (and
fails the test).
"""

import os
from pathlib import Path
import socket
import threading
import time

import pytest
import uvicorn

from greek.api.study import MODEL as STUDY_MODEL
from greek.db.repos.lines import insert_lines
from greek.db.repos.parses import save_treebank
from greek.db.repos.study import save_study_result
from greek.db.repos.texts import insert_text
from greek.models import LineTranslation, TranslationUnit, WordParse, WordStudy

pw = pytest.importorskip("playwright.sync_api")

VIEWPORTS = [
    (2560, 1324, False),
    (1824, 1024, False),
    (1373, 789, True),
    (954, 1208, True),
]


def report_csp_violations(page, errors):
    """
    The browser's Content-Security-Policy refusals count as page errors. In
    WebKit, Playwright's own screenshots add a style the policy refuses too.
    """
    page.on(
        "console",
        lambda message: "Content Security Policy" in message.text
        and errors.append(message.text),
    )


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as runtime:
        executable = os.environ.get(
            "GREEK_TEST_BROWSER", runtime.chromium.executable_path
        )
        if not Path(executable).is_file():
            pytest.skip("Playwright Chromium is not installed")
        with runtime.chromium.launch(executable_path=executable) as instance:
            yield instance


@pytest.fixture
def live_reader(client, web_db):
    # The client fixture supplies the app's in-memory DB dependency override.
    from greek.web.app import app

    tid = insert_text(web_db, "Iliad", "Homer", "iliad.txt", "/d/iliad.txt", 45)
    insert_lines(web_db, tid, ["λόγος ἔργον"] * 45, [f"1.{n}" for n in range(1, 46)])
    # every line has treebank words; 1.1–1.3 have the study data
    save_treebank(
        web_db,
        tid,
        [],
        [
            WordParse(tid, n, i, 0, form, lemma, postag, 100)
            for n in range(1, 46)
            for i, (form, lemma, postag) in enumerate(
                [("λόγος", "λόγος", "n-s---mn-"), ("ἔργον", "ἔργον", "n-s---na-")]
            )
        ],
    )
    save_study_result(
        web_db,
        TranslationUnit(tid, 1, 3, "The word and the deed.", STUDY_MODEL),
        [LineTranslation(tid, n, "Word deed.", STUDY_MODEL) for n in range(1, 4)],
        [
            WordStudy(
                text_id=tid,
                line_number=n,
                token_index=i,
                gloss=gloss,
                note="A saved note.",
                parse_check=None,
                model_used=STUDY_MODEL,
            )
            for n in range(1, 4)
            for i, gloss in enumerate(["word", "deed"])
        ],
        [],
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="none"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 5
    try:
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Preview server failed to start"
        yield f"http://127.0.0.1:{port}/texts/{tid}/read?at=1.1&interlinear=false"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


@pytest.fixture
def page(browser, live_reader, request):
    width, height, touch = getattr(request, "param", VIEWPORTS[-1])
    context = browser.new_context(
        viewport={"width": width, "height": height}, has_touch=touch
    )
    page = context.new_page()
    errors, blocked = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    report_csp_violations(page, errors)

    def only_local_reads(route):
        req = route.request
        if req.method != "GET" or not req.url.startswith(live_reader.split("/texts/")[0]):
            blocked.append(f"{req.method} {req.url}")
            route.abort()
        else:
            route.continue_()

    page.route("**/*", only_local_reads)
    try:
        page.goto(live_reader + "&lines=1.1-1.3")
        pw.expect(page.locator("#panel-fluent")).to_be_visible()
        yield page
        assert errors == []
        assert blocked == [], "Reading interactions attempted a write request"
    finally:
        context.close()


@pytest.mark.parametrize("page", VIEWPORTS, indirect=True)
def test_reader_fits_window_and_panes_scroll_independently(page):
    page.locator('.line[data-line="1"] .w').first.click()
    pw.expect(page.locator("#word-strip.has-word")).to_be_visible()
    bounds = page.evaluate("""() => {
        const rect = selector => {
            const r = document.querySelector(selector).getBoundingClientRect();
            return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
        };
        return {
            width: innerWidth, height: innerHeight,
            scrollWidth: document.documentElement.scrollWidth,
            scrollHeight: document.documentElement.scrollHeight,
            text: rect('#text-pane'), study: rect('.reader-side'),
            navVisible: getComputedStyle(document.querySelector('#reader-nav')).display !== 'none'
        };
    }""")
    assert bounds["scrollWidth"] == bounds["width"]
    assert bounds["scrollHeight"] == bounds["height"]
    for pane in (bounds["text"], bounds["study"]):
        assert 0 <= pane["x"] < pane["right"] <= bounds["width"]
        assert 0 <= pane["y"] < pane["bottom"] <= bounds["height"]
    assert bounds["navVisible"] == (bounds["width"] > 1500)
    if bounds["width"] == 1824:
        assert bounds["study"]["width"] == 560
    if bounds["width"] == 1373:
        assert bounds["study"]["width"] == 540
    if bounds["width"] == 954:
        assert bounds["study"]["height"] == 540
        assert bounds["text"]["bottom"] < bounds["study"]["y"]
    if bounds["width"] > 1500:
        pw.expect(page.locator("#reader-menu")).to_be_hidden()
    page.locator(".text-scroll").evaluate("el => el.scrollTop = 200")
    assert page.locator(".text-scroll").evaluate("el => el.scrollTop") > 0
    assert page.evaluate("window.scrollY") == 0


def test_touch_range_reverses_clears_and_resets_after_completion(page):
    page.locator('.line[data-line="3"] .line-ref').tap()
    pw.expect(page.locator("#selection-hint")).to_be_visible()
    page.locator('.line[data-line="1"] .line-ref').tap()
    pw.expect(page.locator('#passage-box.is-study[data-start="1"][data-end="3"]')).to_be_visible()
    pw.expect(page.locator(".line.is-selected")).to_have_count(3)
    pw.expect(page.locator("#selection-hint")).to_be_hidden()
    page.locator('.line[data-line="5"] .line-ref').tap()
    pw.expect(page.locator('#passage-box[data-start="5"][data-end="5"]')).to_be_visible()
    page.locator('.line[data-line="5"] .line-ref').tap()
    pw.expect(page.locator(".line.is-selected")).to_have_count(0)
    pw.expect(page.locator("#selection-hint")).to_be_hidden()
    pw.expect(page.locator("#passage-box")).to_contain_text("Select lines to study them")


def test_tablet_tabs_survive_swaps_and_word_click_opens_word(page):
    page.locator("#reader-tab-words").click()
    pw.expect(page.locator("#panel-words")).to_be_visible()
    page.locator('.wt-row[data-line="1"]').first.click()
    pw.expect(page.locator("#word-strip.has-word")).to_be_visible()
    pw.expect(page.locator("#passage-box")).to_be_hidden()
    page.locator("#reader-tab-word").press("ArrowRight")
    pw.expect(page.locator("#panel-fluent")).to_be_visible()
    page.locator("#reader-tab-literal").click()
    pw.expect(page.locator("#panel-literal")).to_be_visible()
    page.locator('.line[data-line="5"] .line-ref').tap()
    pw.expect(page.locator("#reader-tab-literal")).to_be_disabled()
    pw.expect(page.locator("#reader-tab-words")).to_be_enabled()  # treebank words
    pw.expect(page.locator("#reader-tab-fluent")).to_have_attribute("aria-selected", "true")
    pw.expect(page.locator("#passage-box")).to_contain_text(
        "No translation is saved for this line yet"
    )
    page.locator('.line[data-line="1"] .w').first.tap()
    pw.expect(page.locator("#word-strip.has-word")).to_be_visible()
    page.set_viewport_size({"width": 1824, "height": 1024})
    pw.expect(page.locator("#word-strip")).to_be_visible()
    pw.expect(page.locator("#passage-box")).to_be_visible()
    pw.expect(page.locator(".reader-tabs")).to_be_hidden()


def test_drawer_traps_focus_dismisses_and_closes_after_book_link(page):
    menu = page.locator("#reader-menu")
    menu.click()
    nav = page.locator("#reader-nav")
    pw.expect(nav).to_be_visible()
    pw.expect(nav).to_have_attribute("aria-modal", "true")
    pw.expect(page.locator("#text-pane")).to_have_attribute("inert", "")
    close = nav.locator('[data-action="close-nav"]')
    pw.expect(close).to_be_focused()
    close.press("Shift+Tab")
    pw.expect(nav.locator("a").last).to_be_focused()
    page.keyboard.press("Tab")
    pw.expect(close).to_be_focused()
    page.keyboard.press("Escape")
    pw.expect(nav).to_be_hidden()
    pw.expect(menu).to_be_focused()
    menu.click()
    page.locator(".drawer-backdrop").click(position={"x": 700, "y": 200})
    pw.expect(nav).to_be_hidden()
    menu.click()
    nav.locator(".book-grid a").first.click()
    pw.expect(nav).to_be_hidden()
    pw.expect(menu).to_have_attribute("aria-expanded", "false")
    pw.expect(page.locator("#text-pane")).not_to_have_attribute("inert", "")


TABLETS = [VIEWPORTS[2], VIEWPORTS[3]]


@pytest.mark.parametrize("page", TABLETS, indirect=True)
def test_tablet_slim_bar_holds_the_toolbar_and_the_drawer_the_rest(page):
    bar = page.evaluate("""() => {
        const rect = selector => document.querySelector(selector).getBoundingClientRect();
        const toolbar = document.querySelector('#text-pane > .pane-toolbar');
        return {
            header: rect('.app-header').height,
            toolbar: [toolbar.getBoundingClientRect().top, toolbar.getBoundingClientRect().bottom],
            between: rect('#reader-menu').right < toolbar.getBoundingClientRect().left
                && toolbar.getBoundingClientRect().right < rect('.app-header .interlinear-control').left,
            fits: toolbar.scrollWidth <= toolbar.clientWidth,
            text: rect('#text-pane').top,
        };
    }""")
    assert bar == {"header": 60, "toolbar": [0, 60], "between": True, "fits": True, "text": 72}
    pw.expect(page.locator(".app-header .brand")).to_be_hidden()
    pw.expect(page.locator(".app-header .main-nav")).to_be_hidden()
    nav = page.locator("#reader-nav")
    pw.expect(nav.locator("#goto")).to_have_count(1)
    page.locator("#pane-range").tap()
    pw.expect(nav).to_be_visible()
    pw.expect(nav.locator("#goto input[name='q']")).to_be_focused()
    pw.expect(nav.locator(".drawer-pages").get_by_role("link", name="Library")).to_be_visible()
    page.keyboard.press("Escape")
    pw.expect(nav).to_be_hidden()
    pw.expect(page.locator("#pane-range")).to_be_focused()
    # wider than an iPad: the header and the text toolbar are as they were
    page.set_viewport_size({"width": 1824, "height": 1024})
    header = page.locator(".app-header")
    pw.expect(header.locator("#goto")).to_have_count(1)
    pw.expect(header.locator(".main-nav")).to_be_visible()
    pw.expect(page.locator("#reader-nav .drawer-only").first).to_be_hidden()
    toolbar = page.locator("#text-pane > .pane-toolbar")
    assert toolbar.evaluate("el => getComputedStyle(el).position") == "relative"


@pytest.mark.parametrize("page", TABLETS, indirect=True)
def test_tablet_boxes_stay_put_while_selecting(page):
    boxes = """() => ['#text-pane', '#text-pane .text-scroll', '.reader-side', '.line[data-line="2"]']
        .map(selector => {
            const r = document.querySelector(selector).getBoundingClientRect();
            return [r.x, r.y, r.width, r.height];
        })"""
    before = page.evaluate(boxes)
    page.locator('.line[data-line="5"] .line-ref').tap()
    pw.expect(page.locator("#selection-hint")).to_be_visible()
    assert page.evaluate(boxes) == before
    # a finger on something that doesn't scroll doesn't pull the page along
    overscroll = page.evaluate(
        "[document.documentElement, document.body].map(el => getComputedStyle(el).overscrollBehaviorY)"
    )
    assert overscroll == ["none", "none"]


@pytest.mark.parametrize("page", [VIEWPORTS[2]], indirect=True)
def test_tablet_goto_error_opens_the_drawer(page, live_reader):
    page.goto(live_reader + "&goto_error=Iliad+has+no+9.9")
    nav = page.locator("#reader-nav")
    pw.expect(nav).to_be_visible()
    pw.expect(nav.locator("#goto-error")).to_have_text("Iliad has no 9.9")
    pw.expect(nav.locator("#goto input[name='q']")).to_be_focused()


@pytest.mark.parametrize("page", [VIEWPORTS[1]], indirect=True)
def test_mouse_shift_selection_keeps_desktop_word_card_visible(page):
    page.locator('.line[data-line="1"] .line-ref').click()
    page.locator('.line[data-line="3"] .line-ref').click(modifiers=["Shift"])
    pw.expect(page.locator('#passage-box.is-study[data-start="1"][data-end="3"]')).to_be_visible()
    pw.expect(page.locator(".line.is-selected")).to_have_count(3)
    page.locator('.line[data-line="1"] .w').first.click()
    pw.expect(page.locator("#word-strip.has-word")).to_be_visible()
    pw.expect(page.locator("#passage-box")).to_be_visible()


def test_touch_range_spans_what_were_two_pages_by_scrolling(page):
    page.locator('.line[data-line="39"] .line-ref').tap()
    page.locator('.line[data-line="43"]').scroll_into_view_if_needed()
    pw.expect(page.locator('.line[data-line="43"]')).to_be_visible()
    page.locator('.line[data-line="43"] .line-ref').tap()
    pw.expect(page.locator('#passage-box[data-start="39"][data-end="43"]')).to_be_visible()
    pw.expect(page.locator("#selection-refs")).to_have_text("1.39–1.43")
    pw.expect(page.locator("#selection-hint")).to_be_hidden()


def test_a_slow_word_does_not_hold_up_the_next_click(browser, live_reader, monkeypatch):
    # The "Find in" count of ἔργον is slow. Another word clicked meanwhile,
    # and a selected passage, show at once; the slow response is not swapped in.
    from greek.web.routes import reader

    search = reader.search_lines

    def slow_for_ergon(conn, text_id, query, limit=200):
        if query == "ἔργον":
            time.sleep(2)
        return search(conn, text_id, query, limit)

    monkeypatch.setattr(reader, "search_lines", slow_for_ergon)
    context = browser.new_context(viewport={"width": 2560, "height": 1324})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    report_csp_violations(page, errors)
    page.route("**/*", lambda route: route.abort() if route.request.method != "GET" else route.continue_())
    try:
        page.goto(live_reader)
        with page.expect_response(lambda r: "/word?line=2&i=1" in r.url, timeout=10_000):
            page.locator('.line[data-line="2"] .w').nth(1).click()
            page.locator('.line[data-line="1"] .w').first.click()
            pw.expect(page.locator("#word-strip .ws-greek")).to_have_text("λόγος", timeout=1500)
            page.locator('.line[data-line="4"] .line-ref').click()
            page.locator('.line[data-line="5"] .line-ref').click(modifiers=["Shift"])
            pw.expect(page.locator('#passage-box[data-start="4"][data-end="5"]')).to_be_attached(timeout=1500)
        page.wait_for_timeout(300)
        pw.expect(page.locator("#word-strip")).to_have_count(1)
        pw.expect(page.locator("#word-strip .ws-greek")).to_have_text("λόγος")
        pw.expect(page.locator('.line[data-line="1"] .w.is-active')).to_have_count(1)
        assert errors == []
    finally:
        context.close()
