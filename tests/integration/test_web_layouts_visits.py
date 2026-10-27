"""Optional browser regressions for visit.js (see test_web_layouts.py).

A page asks for /visit once: after 3 seconds in view, or at once when the
reader clicks, never while its tab is in the background. It names the page
as it was opened and the referring site's host. The test app counts nothing
(GREEK_VISITS_DIR is unset), and every write request fails the test.
"""

from urllib.parse import parse_qs, urlsplit

import pytest

pw = pytest.importorskip("playwright.sync_api")

from tests.integration import test_web_layouts as layouts  # noqa: E402

browser = layouts.browser
live_reader = layouts.live_reader

START = 1_793_000_000_000  # ms, 2026-10-26

# document.visibilityState as the test sets it, from before the page's scripts
HIDDEN = """
window.__visibility = "hidden";
Object.defineProperty(document, "visibilityState", {
  get: () => window.__visibility, configurable: true,
});
"""


@pytest.fixture
def opened(browser, live_reader):
    """A page on a clock the test moves, and the /visit requests it makes."""
    context = browser.new_context(viewport={"width": 1824, "height": 1024})
    page = context.new_page()
    errors, blocked, visits = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    layouts.report_csp_violations(page, errors)
    origin = live_reader.split("/texts/")[0]

    def only_local_reads(route):
        req = route.request
        if req.method != "GET" or not req.url.startswith(origin):
            blocked.append(f"{req.method} {req.url}")
            route.abort()
        else:
            if urlsplit(req.url).path == "/visit":
                visits.append(parse_qs(urlsplit(req.url).query, keep_blank_values=True))
            route.continue_()

    page.route("**/*", only_local_reads)
    # A clock that moves only when the test moves it, from before the page opens
    page.clock.install(time=START)
    page.clock.pause_at(START + 1000)
    try:
        yield page, visits
        assert errors == []
        assert blocked == []
    finally:
        context.close()


def _settle(page):
    """Let the page's fetches go out (they don't wait on the clock)."""
    page.wait_for_timeout(200)


def test_a_page_in_view_for_3_seconds_counts_once(opened, live_reader):
    page, visits = opened
    page.goto(live_reader)
    page.clock.run_for(2900)
    _settle(page)
    assert visits == []
    page.clock.run_for(200)
    _settle(page)
    page.clock.run_for(10000)
    _settle(page)
    path = live_reader[live_reader.index("/texts/") :]
    assert visits == [{"page": [path], "from": [""]}]


def test_a_click_counts_at_once_and_only_once(opened, live_reader):
    page, visits = opened
    page.goto(live_reader)
    page.locator('.line[data-line="1"] .w').first.click()
    _settle(page)
    assert len(visits) == 1
    page.clock.run_for(5000)
    _settle(page)
    assert len(visits) == 1


def test_a_page_in_a_background_tab_waits_until_it_is_seen(opened, live_reader):
    page, visits = opened
    page.add_init_script(HIDDEN)
    page.goto(live_reader)
    page.clock.run_for(10000)
    _settle(page)
    assert visits == []
    page.evaluate(
        """() => {
        window.__visibility = "visible";
        document.dispatchEvent(new Event("visibilitychange"));
    }"""
    )
    page.clock.run_for(3100)
    _settle(page)
    assert len(visits) == 1


def test_the_referring_sites_host_is_all_it_names(opened, live_reader):
    page, visits = opened
    page.goto(live_reader, referer="https://forum.example/t/greek-readers?page=2")
    page.clock.run_for(3100)
    _settle(page)
    assert visits[0]["from"] == ["forum.example"]
