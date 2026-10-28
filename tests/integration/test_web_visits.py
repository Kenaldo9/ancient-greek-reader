"""
The public site's visitor counts (greek/web/visits.py) on the real app:
what a person, the site check and a bot add, what counts for nothing, the
daily files, and the private reader, which counts nothing.
"""

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from greek.db.connection import run_migrations
from greek.web.page_cache import PageCache
from greek.web.visits import (
    KEEP_DAYS,
    MOST_SITES,
    SITE_CHECK,
    VisitCounter,
    Visits,
    page_names,
)

REPO = Path(__file__).resolve().parents[2]
DAY = date(2026, 11, 3)
CHROME = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
# What a request through Cloudflare's tunnel carries
CLOUDFLARE = {"cf-ray": "8d1f0c2e5a7b-ARN", "cf-ipcountry": "FI"}
PERSON = {**CLOUDFLARE, "user-agent": CHROME}
HTMX = {**PERSON, "hx-request": "true"}
# visit.js's fetch from one of the site's pages
FROM_A_PAGE = {**PERSON, "sec-fetch-site": "same-origin"}


class Counted:
    """The app behind the visitor counts, as on the public site."""

    def __init__(self, app, folder: Path, names, today=lambda: DAY, cache=False):
        self.folder = folder
        self.visits = Visits(folder, names, today=today)
        inner = PageCache(app) if cache else app
        self.client = TestClient(VisitCounter(inner, self.visits))

    def get(self, url, headers=PERSON, **kwargs):
        return self.client.get(url, headers=headers, **kwargs)

    def visit(self, page, came_from="", headers=FROM_A_PAGE):
        response = self.get(
            "/visit", headers=headers, params={"page": page, "from": came_from}
        )
        assert response.status_code == 204
        return response

    def counts(self, day=DAY) -> dict:
        self.visits.write()
        path = self.folder / f"{day.isoformat()}.json"
        return json.loads(path.read_text()) if path.exists() else {}


@pytest.fixture
def site(client, db_text, tmp_path):
    from greek.web.app import app

    db, tid = db_text
    site = Counted(app, tmp_path, lambda: page_names(db))
    site.tid = tid
    with site.client:
        yield site


# -- people -----------------------------------------------------------------


def test_a_person_from_another_site(site):
    assert site.get("/library").status_code == 200
    site.visit("/library", "github.com")
    counts = site.counts()
    assert counts["people"] == {"visits": 1, "views": 1}
    assert counts["from"] == {"github.com": 1}
    assert counts["countries"] == {"FI": 1}
    assert counts["pages"] == {"Library": 1}
    assert counts["loads"] == {"browsers": 1, "confirmed": 1}
    assert counts["unconfirmed"] == 0


def test_a_typed_address_or_bookmark_is_direct(site):
    site.visit("/about")
    assert site.counts()["from"] == {"direct": 1}


def test_the_next_page_on_the_site_is_a_view_not_a_visit(site):
    site.visit("/library", "duckduckgo.com")
    site.visit(f"/texts/{site.tid}/read?at=1.1", "testserver")
    counts = site.counts()
    assert counts["people"] == {"visits": 1, "views": 2}
    assert counts["pages"] == {"Library": 1, "Iliad 1": 1}
    assert counts["countries"] == {"FI": 1}


def test_what_htmx_asks_for_is_a_persons(site):
    tid = site.tid
    assert site.get(f"/texts/{tid}/read?at=1.3", headers=HTMX).status_code == 200
    site.get(f"/texts/{tid}/read?at=1.1&q=μηνιν", headers=HTMX)
    site.get(f"/texts/{tid}/word?line=1&i=0", headers=HTMX)
    site.get(f"/texts/{tid}/word?line=2&i=0", headers=HTMX)
    site.get(f"/texts/{tid}/passage?lines=1.1-1.2", headers=HTMX)
    counts = site.counts()
    assert counts["people"] == {"visits": 0, "views": 1}
    assert counts["pages"] == {"Iliad 1": 1}
    assert counts["tools"] == {"word": 2, "passage": 1, "search": 1}
    assert counts["loads"]["browsers"] == 0


def test_the_first_word_a_page_opens_by_itself_is_not_counted(site):
    word = f"/texts/{site.tid}/word?line=1&i=0"
    first_word = {**HTMX, "x-first-word": "1"}  # reader.js, a first visit
    assert site.get(word, headers=first_word).status_code == 200
    assert site.counts().get("tools", {}).get("word", 0) == 0
    site.get(word, headers=HTMX)  # then a click on it
    assert site.counts()["tools"]["word"] == 1


def test_a_page_never_confirmed_is_unconfirmed(site):
    site.get("/library")
    site.get(f"/texts/{site.tid}/toc")
    site.visit(f"/texts/{site.tid}/toc", "testserver")
    counts = site.counts()
    assert counts["loads"] == {"browsers": 2, "confirmed": 1}
    assert counts["unconfirmed"] == 1


def test_a_page_the_cache_answers_counts_too(client, db_text, tmp_path):
    from greek.web.app import app

    db, _ = db_text
    site = Counted(app, tmp_path, lambda: page_names(db), cache=True)
    with site.client:
        for _ in range(3):
            assert site.get("/library").status_code == 200
    assert site.counts()["loads"]["browsers"] == 3


# -- not people -------------------------------------------------------------


def test_the_site_check_is_counted_apart(site):
    check = {**CLOUDFLARE, "user-agent": SITE_CHECK}
    site.get("/version", headers=check)
    site.get("/library", headers=check)
    site.get(f"/texts/{site.tid}/read?at=1.1", headers=check)
    counts = site.counts()
    assert counts["checks"] == {"runs": 1, "requests": 2}
    assert counts["people"] == {"visits": 0, "views": 0}
    assert counts["loads"]["browsers"] == 0


def test_a_bot_is_counted_by_name(site):
    bot = {**CLOUDFLARE, "user-agent": GOOGLEBOT}
    site.get("/library", headers=bot)
    site.get(f"/texts/{site.tid}/word?line=1&i=0", headers=bot)
    site.visit("/library", headers={**bot, "sec-fetch-site": "same-origin"})
    counts = site.counts()
    assert counts["bots"] == {"Googlebot": 3}
    assert counts["people"] == {"visits": 0, "views": 0}


def test_requests_that_did_not_come_through_cloudflare_count_for_nothing(site):
    """The deploy's own checks, from inside the container."""
    site.get("/library", headers={"user-agent": "Python-urllib/3.12"})
    site.get("/version", headers={"user-agent": SITE_CHECK})
    site.visit(
        "/library", headers={"user-agent": CHROME, "sec-fetch-site": "same-origin"}
    )
    assert site.counts() == {}


def test_another_site_cant_count_its_readers_here(site):
    site.visit(
        "/library", "evil.example", headers={**PERSON, "sec-fetch-site": "cross-site"}
    )
    site.visit("/library", headers={**PERSON, "referer": "https://evil.example/"})
    site.visit("/library", headers=PERSON)
    assert site.counts() == {}


def test_a_browser_without_sec_fetch_site_counts_from_the_sites_own_page(site):
    site.visit("/library", headers={**PERSON, "referer": "http://testserver/library"})
    assert site.counts()["people"]["views"] == 1


def test_what_isnt_a_page_is_not_counted(site):
    site.get("/texts/999/read")  # 404
    site.get("/static/style.css")
    site.get("/robots.txt")
    site.get(f"/texts/{site.tid}/word?line=1&i=0")  # not htmx: no person's
    assert site.counts() == {}


def test_a_page_or_site_that_doesnt_fit_is_dropped(site):
    site.visit("/texts/999/read?at=1.1", "<script>")
    site.visit("x" * 5000)
    counts = site.counts()
    assert counts["pages"] == {"other page": 2}
    assert counts["from"] == {"other": 1, "direct": 1}


def test_referring_sites_are_capped(site):
    for n in range(MOST_SITES + 5):
        site.visit("/library", f"site{n}.example")
    sites = site.counts()["from"]
    assert len(sites) == MOST_SITES + 1
    assert sites["other"] == 5


# -- the files --------------------------------------------------------------


def test_a_days_file_is_totals_others_may_read(site):
    site.visit("/library", "github.com")
    path = site.folder / f"{DAY.isoformat()}.json"
    site.visits.write()
    assert path.stat().st_mode & 0o777 == 0o644
    saved = json.loads(path.read_text())
    assert saved["format"] == 1 and saved["day"] == "2026-11-03"
    assert set(saved) == {
        "format",
        "day",
        "people",
        "pages",
        "from",
        "countries",
        "tools",
        "bots",
        "checks",
        "loads",
        "unconfirmed",
    }
    assert list(site.folder.iterdir()) == [path]


def test_nothing_new_is_not_written_again(site):
    site.visits.write()
    assert list(site.folder.iterdir()) == []


def test_counting_goes_on_after_a_restart(client, db_text, tmp_path):
    from greek.web.app import app

    db, _ = db_text
    for _ in range(2):
        site = Counted(app, tmp_path, lambda: page_names(db))
        with site.client:
            site.visit("/library", "github.com")
        site.visits.write()
    assert site.counts()["people"] == {"visits": 2, "views": 2}


def test_a_new_day_writes_the_last_and_drops_the_oldest(client, db_text, tmp_path):
    from greek.web.app import app

    db, _ = db_text
    new = DAY + timedelta(days=1)
    old = new - timedelta(days=KEEP_DAYS + 1)
    kept = new - timedelta(days=KEEP_DAYS)
    for day in (old, kept):
        (tmp_path / f"{day.isoformat()}.json").write_text("{}")
    (tmp_path / "notes.json").write_text("{}")
    days = [DAY]
    site = Counted(app, tmp_path, lambda: page_names(db), today=lambda: days[0])
    with site.client:
        site.visit("/library")
        days[0] = new
        site.visit("/about")
    assert site.counts(DAY)["pages"] == {"Library": 1}
    assert site.counts(new)["pages"] == {"About": 1}
    names = sorted(p.name for p in tmp_path.iterdir())
    assert f"{old.isoformat()}.json" not in names
    assert f"{kept.isoformat()}.json" in names and "notes.json" in names


def test_a_file_that_cant_be_written_is_said_once(client, db_text, tmp_path, capsys):
    from greek.web.app import app

    db, _ = db_text
    site = Counted(app, tmp_path / "missing", lambda: page_names(db))
    with site.client:
        site.visit("/library")
        site.visits.write()
        site.visit("/about")
        site.visits.write()
    assert capsys.readouterr().err.count("Visitor counts not written") == 1


# -- only on the public site --------------------------------------------------


def test_the_private_readers_visit_answers_and_counts_nothing(client):
    from greek.web import app as web

    response = client.get("/visit?page=/library&from=github.com")
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    assert web.visits is None


def test_search_engines_are_kept_off_visit(client):
    assert "Disallow: /visit" in client.get("/robots.txt").text.splitlines()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_public_site_writes_its_counts_when_docker_stops_it(tmp_path):
    """greek web, as the container runs it, stopped with SIGTERM."""
    data, folder = tmp_path / "data", tmp_path / "visits"
    data.mkdir()
    folder.mkdir()
    conn = sqlite3.connect(data / "greek.db")
    run_migrations(conn)
    conn.close()
    port = _free_port()
    env = dict(os.environ, GREEK_DATA_DIR=str(data), GREEK_VISITS_DIR=str(folder))
    server = subprocess.Popen(
        [sys.executable, "greek.py", "web", "--port", str(port)],
        cwd=REPO,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        visit = urllib.request.Request(
            f"http://127.0.0.1:{port}/visit?page=/library&from=github.com",
            headers={**FROM_A_PAGE, "user-agent": "Mozilla/5.0 Firefox/131.0"},
        )
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(visit, timeout=5) as response:
                    assert response.status == 204
                break
            except OSError:
                assert time.monotonic() < deadline, "the app didn't start"
                time.sleep(0.2)
        assert list(folder.iterdir()) == []  # not yet written
    finally:
        server.send_signal(signal.SIGTERM)
        _, err = server.communicate(timeout=30)
    [written] = folder.iterdir()
    saved = json.loads(written.read_text())
    assert saved["people"] == {"visits": 1, "views": 1}, err
    assert saved["from"] == {"github.com": 1}
