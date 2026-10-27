"""
The visitor counts' parts (greek/web/visits.py): who sent a request, the
site a visit came from, the country, and how a page is named.
"""

from pathlib import Path

import pytest

from greek.db.repos.lines import insert_lines
from greek.db.repos.texts import insert_text
from greek.web.visits import (
    BROWSER,
    CHECK,
    OTHER_BOT,
    SITE_CHECK,
    country,
    page_label,
    page_names,
    referring_site,
    who,
)

CHECK_SITE = Path(__file__).resolve().parents[2] / "deploy" / "public" / "check_site.py"

CHROME = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
SAFARI = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"

NAMES = {12: ("Iliad", frozenset({"1", "2"})), 16: ("Herodotus", frozenset({"1"}))}


def test_the_site_check_is_known_by_the_user_agent_it_sends():
    assert f'"User-Agent": "{SITE_CHECK}"' in CHECK_SITE.read_text()
    assert who(SITE_CHECK) == CHECK


@pytest.mark.parametrize("agent", [CHROME, SAFARI, FIREFOX])
def test_browsers_are_browsers(agent):
    assert who(agent) == BROWSER


@pytest.mark.parametrize(
    "agent, name",
    [
        (
            "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "Googlebot",
        ),
        (
            "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
            "Bingbot",
        ),
        ("facebookexternalhit/1.1", "Facebook"),
        (
            "Mozilla/5.0 (compatible; Claude-User/1.0; +Claude-User@anthropic.com)",
            "Claude",
        ),
        ("Mozilla/5.0 (compatible; SomeNewBot/0.1)", OTHER_BOT),
        (CHROME.replace("Chrome/", "HeadlessChrome/"), OTHER_BOT),
        ("curl/8.5.0", OTHER_BOT),
        ("python-requests/2.32", OTHER_BOT),
        ("Python-urllib/3.12", OTHER_BOT),
        ("", OTHER_BOT),
    ],
)
def test_bots_are_known_by_name_or_as_bots(agent, name):
    assert who(agent) == name


@pytest.mark.parametrize(
    "host, site",
    [
        ("", "direct"),
        ("github.com", "github.com"),
        ("WWW.Example.org", "example.org"),
        ("com.google.android.gm", "com.google.android.gm"),
        ("not a host", "other"),
        ("<b>x</b>", "other"),
        ("a" * 101, "other"),
    ],
)
def test_the_site_a_visit_came_from(host, site):
    assert referring_site(host, "greek.kenaldo.dev") == site


def test_this_site_itself_is_a_page_view_not_a_visit():
    assert referring_site("greek.kenaldo.dev", "greek.kenaldo.dev") is None
    assert referring_site("www.reader.example", "reader.example:443") is None


@pytest.mark.parametrize(
    "header, code",
    [("FI", "FI"), ("fi", "FI"), ("XX", "XX"), ("T1", "T1"), ("<i>", "unknown")],
)
def test_the_country_is_cloudflares_code(header, code):
    assert country({"cf-ipcountry": header}) == code


def test_no_country_header_is_unknown():
    assert country({}) == "unknown"


@pytest.mark.parametrize(
    "path, query, label",
    [
        ("/library", "", "Library"),
        ("/about", "", "About"),
        ("/texts/12/toc", "", "Iliad contents"),
        ("/texts/12/read", "at=1.1", "Iliad 1"),
        ("/texts/12/read", "at=2.40&interlinear=true", "Iliad 2"),
        ("/texts/12/read", "lines=2.3-2.9", "Iliad 2"),
        ("/texts/12/read", "at=9.1", "Iliad"),
        ("/texts/12/read", "", "Iliad"),
        ("/texts/16/read", "at=1.37.1", "Herodotus 1"),
    ],
)
def test_pages_are_named_by_text_and_book(path, query, label):
    assert page_label(path, query, NAMES) == label


@pytest.mark.parametrize(
    "path", ["/texts/99/read", "/texts/12/word", "/static/style.css", "/", "/goto"]
)
def test_other_paths_are_no_page(path):
    assert page_label(path, "", NAMES) is None


def test_page_names_come_from_the_texts_and_their_books(db):
    tid = insert_text(db, "Iliad", "Homer", "iliad.txt", "/d/iliad.txt", 3)
    insert_lines(db, tid, ["a", "b", "c"], ["1.1", "1.2", "2.1"])
    assert page_names(db) == {tid: ("Iliad", frozenset({"1", "2"}))}
