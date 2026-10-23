"""Optional browser regressions for whole-book pages (see test_web_layouts.py).

The fixture text has two books: book 1 of 300 lines and book 2 of 30. The
reader writes nothing to the server: its reading position is saved in the
browser (localStorage, positions.js), and every write request fails the test.
"""

import pytest

from greek.api.study import MODEL as STUDY_MODEL
from greek.db.repos.lines import insert_lines
from greek.db.repos.study import save_study_result
from greek.db.repos.texts import insert_text
from greek.models import LineTranslation, TranslationUnit, WordStudy

pw = pytest.importorskip("playwright.sync_api")

from tests.integration import test_web_layouts as layouts  # noqa: E402

browser = layouts.browser
live_reader = layouts.live_reader

BOOK1, BOOK2 = 300, 30


@pytest.fixture
def book_reader(live_reader, web_db):
    count = BOOK1 + BOOK2
    tid = insert_text(web_db, "Odyssey", "Homer", "od.txt", "/d/od.txt", count)
    refs = [f"1.{n}" for n in range(1, BOOK1 + 1)] + [
        f"2.{n}" for n in range(1, BOOK2 + 1)
    ]
    insert_lines(web_db, tid, [f"ἄνδρα{n} μοι" for n in range(1, count + 1)], refs)
    return f"{live_reader.split('/texts/')[0]}/texts/{tid}/read", tid


def _open(browser, width=2560, height=1324, touch=False):
    context = browser.new_context(
        viewport={"width": width, "height": height}, has_touch=touch
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    layouts.report_csp_violations(page, errors)
    return context, page, errors


def _block_writes(page, origin, blocked):
    """Abort (and record) every write, and every request to another site."""

    def only_local_reads(route):
        req = route.request
        if req.method != "GET" or not req.url.startswith(origin):
            blocked.append(f"{req.method} {req.url}")
            route.abort()
        else:
            route.continue_()

    page.route("**/*", only_local_reads)


def _rendering(page):
    """Wait for two frames: early on, headless Chromium may not render yet."""
    page.evaluate(
        "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
    )


def _scroll_to(page, line):
    """Scroll the text so `line` is at the top of the pane."""
    page.locator("#text-pane .text-scroll").evaluate(
        "(el, n) => el.scrollTop += document.querySelector("
        '`.line[data-line="${n}"]`).getBoundingClientRect().top'
        " - el.getBoundingClientRect().top",
        line,
    )


def _saved(page, tid):
    """This browser's saved position in a text: {"line": …, "ref": …}, or None."""
    return page.evaluate(
        "id => JSON.parse(localStorage.getItem('greek.position.' + id))", tid
    )


def _saved_line(page, tid, expected, timeout=3000):
    """The saved line, once it is `expected` (or when time runs out)."""
    try:
        page.wait_for_function(
            "([id, n]) => (JSON.parse(localStorage.getItem('greek.position.' + id))"
            " || {}).line === n",
            arg=[tid, expected],
            timeout=timeout,
        )
    except pw.Error:
        pass
    found = _saved(page, tid)
    return found["line"] if found else None


def _top_offset(page, line):
    """How far a line's top is below the top edge of the text pane's scroll box."""
    return page.evaluate(
        """n => {
            const scroller = document.querySelector('#text-pane .text-scroll');
            const line = document.querySelector(`#text-lines .line[data-line="${n}"]`);
            return line.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
        }""",
        line,
    )


def _wait_at_top(page, line):
    """Wait until a line is at the top of the text pane (after htmx settles)."""
    page.wait_for_function(
        """n => {
            const scroller = document.querySelector('#text-pane .text-scroll');
            const line = document.querySelector(`#text-lines .line[data-line="${n}"]`);
            return line && Math.abs(line.getBoundingClientRect().top
                - scroller.getBoundingClientRect().top) <= 2;
        }""",
        arg=line,
        timeout=3000,
    )


@pytest.mark.parametrize("viewport", layouts.VIEWPORTS)
def test_opening_a_line_deep_in_the_book_puts_it_at_the_top(
    browser, book_reader, viewport
):
    url, _ = book_reader
    width, height, touch = viewport
    context, page, errors = _open(browser, width, height, touch)
    blocked = []
    _block_writes(page, url.split("/texts/")[0], blocked)
    try:
        page.goto(url + "?at=1.200&interlinear=false")
        pw.expect(page.locator('.line[data-line="200"]')).to_be_visible()
        assert abs(_top_offset(page, 200)) <= 2
        pw.expect(page.locator('.line[data-line="1"]')).to_have_count(1)  # whole book
        pw.expect(page.locator(f'.line[data-line="{BOOK1 + 1}"]')).to_have_count(0)
        assert page.evaluate("window.scrollY") == 0
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_next_book_opens_at_its_start(browser, book_reader):
    url, _ = book_reader
    context, page, errors = _open(browser)
    blocked = []
    _block_writes(page, url.split("/texts/")[0], blocked)
    try:
        page.goto(url + "?at=1.250&interlinear=false")
        pw.expect(page.locator('.line[data-line="250"]')).to_be_visible()
        page.locator(".next-page").click()
        pw.expect(page.locator(".pane-title")).to_contain_text("Book 2")
        pw.expect(page.locator(f'.line[data-line="{BOOK1 + 1}"]')).to_be_visible()
        assert page.locator(".text-scroll").evaluate("el => el.scrollTop") == 0
        page.locator(".pager a[aria-label='Previous: Book 1']").click()
        pw.expect(page.locator(".pane-title")).to_contain_text("Book 1")
        assert page.locator(".text-scroll").evaluate("el => el.scrollTop") == 0
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_a_pane_link_to_a_line_scrolls_it_to_the_top(browser, book_reader):
    url, _ = book_reader
    context, page, errors = _open(browser, 1824, 1024)
    blocked = []
    _block_writes(page, url.split("/texts/")[0], blocked)
    try:
        page.goto(url + "?at=1.200&interlinear=false")
        pw.expect(page.locator('.line[data-line="200"]')).to_be_visible()
        page.locator('#text-pane a[aria-label^="Search in"]').click()
        pw.expect(page.locator("#search-back")).to_have_text("Back to 1.200")
        page.locator("#search-back").click()  # swaps the text pane (no reload)
        _wait_at_top(page, 200)
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_drawer_book_link_opens_that_book_at_its_start(browser, book_reader):
    url, _ = book_reader
    context, page, errors = _open(browser, 954, 1208, True)
    blocked = []
    _block_writes(page, url.split("/texts/")[0], blocked)
    try:
        page.goto(url + "?at=1.120&interlinear=false")
        pw.expect(page.locator('.line[data-line="120"]')).to_be_visible()
        page.locator("#reader-menu").click()
        page.locator('#reader-nav .book-grid a[aria-label="Book 2"]').click()
        pw.expect(page.locator(".pane-title")).to_contain_text("Book 2")
        pw.expect(page.locator(f'.line[data-line="{BOOK1 + 1}"]')).to_be_visible()
        assert page.locator(".text-scroll").evaluate("el => el.scrollTop") == 0
        assert errors == [] and blocked == []
    finally:
        context.close()


# --- the position follows the scroll ------------------------------------------


def test_scrolling_moves_the_toolbar_url_and_saved_position(browser, book_reader):
    url, tid = book_reader
    context, page, errors = _open(browser, 1824, 1024)
    blocked = []
    _block_writes(page, url.split("/texts/")[0], blocked)
    try:
        page.goto(url + "?at=1.100&interlinear=false")
        _wait_at_top(page, 100)
        assert _saved(page, tid) == {"line": 100, "ref": "1.100"}  # opened there
        pw.expect(page.locator("#pane-range")).to_have_text("1.100 / 1.300")
        _rendering(page)
        for line in (130, 160, 180):  # saved once the scrolling stops
            _scroll_to(page, line)
            page.wait_for_timeout(200)
            assert _saved(page, tid)["line"] == 100
        pw.expect(page.locator("#pane-range")).to_have_text("1.180 / 1.300")
        page.wait_for_function("() => location.search.includes('at=1.180')")
        assert _saved(page, tid) == {"line": 180, "ref": "1.180"}
        assert page.locator("#goto input[name='q']").input_value() == "Odyssey 1.180"
        assert page.locator("#goto input[name='at']").input_value() == "1.180"
        search = page.locator("[data-follows-position]")
        assert search.get_attribute("href").endswith("?at=1.180&q=")

        # a reload, or the copied link, opens there
        page.reload()
        _wait_at_top(page, 180)
        pw.expect(page.locator("#pane-range")).to_have_text("1.180 / 1.300")
        assert _saved(page, tid) == {"line": 180, "ref": "1.180"}
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_search_back_returns_to_the_scrolled_line(browser, book_reader):
    url, _ = book_reader
    context, page, errors = _open(browser, 1373, 789, True)
    blocked = []
    _block_writes(page, url.split("/texts/")[0], blocked)
    try:
        page.goto(url + "?at=1.50&interlinear=false")
        _wait_at_top(page, 50)
        _rendering(page)
        _scroll_to(page, 120)
        page.wait_for_timeout(300)  # sooner than the save: the link follows anyway
        page.locator("[data-follows-position]").click()
        pw.expect(page.locator("#search-back")).to_have_text("Back to 1.120")
        assert "at=1.120&q=" in page.url
        page.locator("#search-back").click()
        _wait_at_top(page, 120)
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_back_after_another_book_returns_to_the_line(browser, book_reader):
    url, tid = book_reader
    context, page, errors = _open(browser)
    blocked = []
    _block_writes(page, url.split("/texts/")[0], blocked)
    try:
        page.goto(url + "?at=1.100&interlinear=false")
        _wait_at_top(page, 100)
        _rendering(page)
        _scroll_to(page, 210)
        page.wait_for_timeout(300)
        # before the save is due (the toolbar's button: clicking the link at
        # the end would scroll the pane to it first)
        page.locator(".pager a[aria-label='Next: Book 2']").click()
        pw.expect(page.locator(".pane-title")).to_contain_text("Book 2")
        page.wait_for_timeout(1300)
        # the pending save was dropped: the position is the book just opened
        assert _saved_line(page, tid, BOOK1 + 1) == BOOK1 + 1
        # htmx kept no snapshot of the book
        assert page.evaluate("localStorage.getItem('htmx-history-cache')") is None

        page.go_back()
        pw.expect(page.locator(".pane-title")).to_contain_text("Book 1")
        assert "at=1.210" in page.url
        _wait_at_top(page, 210)
        assert _saved_line(page, tid, 210) == 210
        page.go_forward()
        pw.expect(page.locator(".pane-title")).to_contain_text("Book 2")
        assert "at=2.1" in page.url
        # the book's first line, with the padding above it
        page.wait_for_function(
            "() => document.querySelector('#text-pane .text-scroll').scrollTop === 0"
        )
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_leaving_the_page_saves_the_position(browser, book_reader):
    url, tid = book_reader
    context, page, errors = _open(browser)
    blocked = []
    _block_writes(page, url.split("/texts/")[0], blocked)
    try:
        page.goto(url + "?at=1.100&interlinear=false")
        _wait_at_top(page, 100)
        _rendering(page)
        _scroll_to(page, 240)
        pw.expect(page.locator("#pane-range")).to_have_text("1.240 / 1.300")
        page.goto(url.split("/texts/")[0] + "/library")  # sooner than the save
        continue_at = page.get_by_role("link", name="Continue at 1.240")
        pw.expect(continue_at).to_be_visible()
        assert continue_at.get_attribute("href") == f"/texts/{tid}/read?at=1.240"
        assert _saved(page, tid) == {"line": 240, "ref": "1.240"}
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_a_search_hit_stays_put_until_you_scroll(browser, book_reader):
    url, tid = book_reader
    context, page, errors = _open(browser, 1824, 1024)
    blocked = []
    _block_writes(page, url.split("/texts/")[0], blocked)
    try:
        page.goto(url + "?at=1.150&hit=1.150&interlinear=false")
        hit = page.locator('.line[data-line="150"].is-hit')
        pw.expect(hit).to_be_visible()
        page.wait_for_function("() => !location.search.includes('hit=')")
        # the hit is centred; the line at the top is now the position
        top = int(page.url.split("at=1.")[1].split("&")[0])
        assert 100 < top < 150
        assert _saved_line(page, tid, top) == top
        page.reload()
        _wait_at_top(page, top)
        pw.expect(page.locator('.line[data-line="150"]')).to_be_in_viewport()
        assert errors == [] and blocked == []
    finally:
        context.close()


# --- the position is this browser's ---------------------------------------------


def test_a_text_opened_without_a_place_continues_where_you_left_off(
    browser, book_reader
):
    url, tid = book_reader
    context, page, errors = _open(browser)
    blocked = []
    _block_writes(page, url.split("/texts/")[0], blocked)
    try:
        page.goto(url + "?interlinear=false")  # nothing saved: the start
        pw.expect(page.locator("#pane-range")).to_have_text("1.1 / 1.300")
        assert "at=" not in page.url
        page.goto(url + "?at=1.200&interlinear=false")
        _wait_at_top(page, 200)
        page.goto(url)  # Go to "Odyssey", or a typed address
        page.wait_for_url("**/read?at=1.200")
        _wait_at_top(page, 200)
        # a selection or a search opens where it says, not at the saved place
        page.goto(url + "?lines=1.5-1.6")
        pw.expect(page.locator('#passage-box[data-start="5"]')).to_be_attached()
        assert "at=" not in page.url
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_the_drawers_text_link_opens_where_you_are(browser, book_reader):
    url, tid = book_reader
    context, page, errors = _open(browser, 1824, 1024)
    blocked = []
    _block_writes(page, url.split("/texts/")[0], blocked)
    try:
        page.goto(url + "?at=1.100&interlinear=false")
        _wait_at_top(page, 100)
        _rendering(page)
        _scroll_to(page, 170)
        pw.expect(page.locator("#pane-range")).to_have_text("1.170 / 1.300")
        # sooner than the save: clicking the link saves the place first
        page.locator(f'#reader-nav a[data-resume="{tid}"]').click()
        page.wait_for_url("**/read?at=1.170")
        _wait_at_top(page, 170)
        assert errors == [] and blocked == []
    finally:
        context.close()


def test_the_reader_works_without_storage(browser, book_reader):
    url, tid = book_reader
    context, page, errors = _open(browser)
    page.add_init_script(
        "Object.defineProperty(window, 'localStorage', "
        "{ get() { throw new DOMException('blocked', 'SecurityError'); } });"
    )
    blocked = []
    _block_writes(page, url.split("/texts/")[0], blocked)
    try:
        page.goto(url + "?at=1.100&interlinear=false")
        _wait_at_top(page, 100)
        _rendering(page)
        _scroll_to(page, 150)
        page.wait_for_function("() => location.search.includes('at=1.150')")
        page.goto(url.split("/texts/")[0] + "/library")
        pw.expect(page.get_by_role("link", name="Start reading")).to_have_count(2)
        page.goto(url)  # nothing to continue from: the start
        pw.expect(page.locator("#pane-range")).to_have_text("1.1 / 1.300")
        assert errors == [] and blocked == []
    finally:
        context.close()


# --- what each book has -------------------------------------------------------


def test_interlinear_is_off_in_a_book_without_glosses(browser, book_reader, web_db):
    url, tid = book_reader
    # 1.1–1.3 have the study data, Book 2 none
    save_study_result(
        web_db,
        TranslationUnit(tid, 1, 3, "Tell me of the man.", STUDY_MODEL),
        [LineTranslation(tid, n, "man me", STUDY_MODEL) for n in range(1, 4)],
        [
            WordStudy(tid, n, i, gloss, "", None, STUDY_MODEL)
            for n in range(1, 4)
            for i, gloss in enumerate(["man", "to me"])
        ],
        [],
    )
    context, page, errors = _open(browser, 1373, 789, True)
    blocked = []
    _block_writes(page, url.split("/texts/")[0], blocked)
    toggle, note = (
        page.locator("#interlinear-toggle"),
        page.locator("#interlinear-note"),
    )
    try:
        page.goto(url + "?at=1.1&interlinear=true")
        pw.expect(page.locator('.line[data-line="1"] .w-t').first).to_have_text("man")
        pw.expect(toggle).to_be_enabled()
        pw.expect(toggle).to_be_checked()
        pw.expect(note).to_have_count(0)
        # the next book, swapped in with the pane: no glosses, so no Interlinear
        page.locator(".pager a[aria-label='Next: Book 2']").click()
        pw.expect(page.locator(".pane-title")).to_contain_text("Book 2")
        pw.expect(toggle).to_be_disabled()
        pw.expect(toggle).not_to_be_checked()
        pw.expect(note).to_have_text("No glosses yet")
        pw.expect(note).to_be_visible()
        bar = page.locator(".app-header").bounding_box()
        box = page.locator("#interlinear-control").bounding_box()
        assert (
            box["y"] >= bar["y"]
            and box["y"] + box["height"] <= bar["y"] + bar["height"]
        )
        assert box["x"] + box["width"] <= 1373
        assert page.evaluate("document.documentElement.scrollWidth") == 1373
        # and back: as this page had it (interlinear=true, not the cookie)
        page.locator(".pager a[aria-label='Previous: Book 1']").click()
        pw.expect(page.locator(".pane-title")).to_contain_text("Book 1")
        pw.expect(toggle).to_be_enabled()
        pw.expect(toggle).to_be_checked()
        pw.expect(page.locator('.line[data-line="1"] .w-t').first).to_be_visible()
        assert errors == [] and blocked == []
    finally:
        context.close()
