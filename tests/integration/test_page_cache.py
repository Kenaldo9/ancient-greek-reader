"""
The public site's page cache (greek/web/page_cache.py): a page kept is the
same bytes the app sends, each part of the key is a page of its own, only
GETs answered 200 are kept, the limit holds, and only an app on a read-only
database has the cache.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from greek.db.connection import run_migrations
from greek.web.page_cache import Page, PageCache

REPO = Path(__file__).resolve().parents[2]


class Counted:
    """An ASGI app that counts the HTTP requests it is asked."""

    def __init__(self, app):
        self.app = app
        self.calls = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            self.calls += 1
        await self.app(scope, receive, send)


@pytest.fixture
def cached(client):
    """The app behind a page cache, as on the public site; client is it without one."""
    from greek.web.app import app

    counted = Counted(app)
    cache = PageCache(counted)
    with TestClient(cache) as c:
        yield c, cache, counted


def _answer(client, url, **kwargs):
    """Status, headers and the page, decompressed."""
    response = client.get(url, **kwargs)
    return response.status_code, response.headers.multi_items(), response.content


def _sent(client, url, **kwargs):
    """The bytes as sent: gzipped when they were."""
    with client.stream("GET", url, **kwargs) as response:
        return b"".join(response.iter_raw())


PAGES = [
    "/library",
    "/about",
    "/texts/{id}/read",
    "/texts/{id}/read?at=1.3",
    "/texts/{id}/read?lines=1.1-1.2",
    "/texts/{id}/read?q=θεα",
    "/texts/{id}/toc",
    "/texts/{id}/word?line=1&i=0",
    "/texts/{id}/passage?lines=1.1-1.2",
    "/robots.txt",
    "/sitemap.xml",
]


@pytest.mark.parametrize("encoding", ["gzip", "identity"])
@pytest.mark.parametrize("path", PAGES)
def test_a_kept_page_is_the_bytes_the_app_sends(
    client, cached, db_text, path, encoding
):
    _, text_id = db_text
    url = path.format(id=text_id)
    headers = {"Accept-Encoding": encoding}
    c, cache, counted = cached

    # gzip's header holds the second it was made: the app's page made again
    # in another second differs in those bytes, so compare it decompressed
    page = _answer(client, url, headers=headers)
    assert page[0] == 200

    sent = _sent(c, url, headers=headers)  # asked and kept
    assert counted.calls == 1 and len(cache.pages) == 1
    assert _sent(c, url, headers=headers) == sent  # from the cache
    assert _answer(c, url, headers=headers) == page
    assert counted.calls == 1
    assert [kept.body for kept in cache.pages.values()] == [sent]
    if encoding == "gzip" and len(page[2]) > 1000:  # gzip's minimum_size
        assert dict(page[1])["content-encoding"] == "gzip"
        assert len(sent) < len(page[2])  # kept gzipped


def test_each_part_of_the_key_is_a_page_of_its_own(cached, db_text):
    _, text_id = db_text
    c, cache, counted = cached
    read = f"/texts/{text_id}/read"
    gzip = {"Accept-Encoding": "gzip"}
    asks = [
        (read, {}),
        (f"/texts/{text_id}/toc", {}),  # the path
        (read + "?at=1.3", {}),  # the query
        (read, {"headers": {**gzip, "Cookie": "interlinear=0"}}),  # the cookie
        (read, {"headers": {**gzip, "Host": "reader.example"}}),  # the host
        (read, {"headers": {**gzip, "X-Forwarded-Proto": "https"}}),  # the scheme
        (read, {"headers": {"Accept-Encoding": "identity"}}),  # gzip or not
    ]
    pages = []
    for url, kwargs in asks:
        kwargs.setdefault("headers", gzip)
        response = c.get(url, **kwargs)
        assert response.status_code == 200
        pages.append(response.text)
    assert counted.calls == len(asks) == len(cache.pages)

    assert "interlinear-on" in pages[0] and "interlinear-on" not in pages[3]
    assert f'href="http://reader.example{read}' in pages[4]
    assert f'href="https://testserver{read}' in pages[5]
    assert pages[6] == pages[0]

    # Anything else about the request is the same page
    c.get(read, headers={**gzip, "User-Agent": "crawler", "HX-Request": "true"})
    c.get(read, headers={**gzip, "Cookie": "interlinear=1; other=x"})
    assert counted.calls == len(asks)


WEB = REPO / "greek" / "web"

# Every place a page reads the request, besides the path and query its route
# takes: each is a part of the cache's key (interlinear_on, site_origin) or
# the path. A new one needs a part of its own.
READS_THE_REQUEST = {
    ("app.py", "request.url.path"),  # refuse_an_unusable_database
    ("reader_data.py", "request.cookies"),  # interlinear_on
    ("templates/base.html", "request.url.path"),  # the menu's current page
    ("templates_config.py", "request.headers"),  # site_origin
    ("templates_config.py", "request.url.scheme"),
    ("templates_config.py", "request.url.netloc"),
}


def test_pages_read_only_the_parts_of_the_request_in_the_key():
    found = set()
    for file in [*WEB.rglob("*.py"), *WEB.rglob("*.html")]:
        # The cache itself, and the visitor counts outside it, which change
        # no page
        if file.name in ("page_cache.py", "visits.py"):
            continue
        for read in re.findall(
            r"request\.(?:cookies|headers|client|query_params|scope|base_url"
            r"|url(?:\.\w+)?)",
            file.read_text(),
        ):
            found.add((str(file.relative_to(WEB)), read))
    assert found == READS_THE_REQUEST


def test_the_interlinear_query_wins_over_the_cookie(cached, db_text):
    _, text_id = db_text
    c, _, _ = cached
    url = f"/texts/{text_id}/read?interlinear=false"
    c.cookies.set("interlinear", "1")
    assert "interlinear-on" not in c.get(url).text
    assert "interlinear-on" in c.get(f"/texts/{text_id}/read").text


@pytest.mark.parametrize(
    "path",
    [
        "/",  # a redirect
        "/texts/9999/read",  # not found
        "/texts/{id}/read?lines=notarange",  # a bad request
        "/goto?q=Iliad",  # a redirect
        "/version",
        "/static/style.css",
        "/static/fonts/fonts.css",
    ],
)
def test_only_pages_answered_ok_are_kept(cached, db_text, path):
    _, text_id = db_text
    c, cache, counted = cached
    url = path.format(id=text_id)
    c.get(url, follow_redirects=False)
    c.get(url, follow_redirects=False)
    assert counted.calls == 2
    assert cache.pages == {} and cache.size == 0


def test_a_head_request_is_not_kept(cached):
    c, cache, _ = cached
    c.head("/library")
    assert cache.pages == {}


# -- the limit ---------------------------------------------------------------


# Without `with`, TestClient asks these apps no lifespan events


def _app(body: bytes, status=200, headers=()):
    """An ASGI app answering every request with body."""

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"text/plain"), *headers],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return app


def test_the_least_recently_used_page_goes_first():
    page = Page(200, [(b"content-type", b"text/plain")], b"x" * 1000)
    counted = Counted(_app(b"x" * 1000))
    cache = PageCache(counted, max_bytes=3 * page.size)
    c = TestClient(cache)
    for path in ("/a", "/b", "/c", "/a", "/d"):  # /a used again: /b goes
        c.get(path, headers={"Accept-Encoding": "identity"})
    assert [key[0] for key in cache.pages] == ["/c", "/a", "/d"]
    assert cache.size == 3 * page.size == cache.max_bytes
    assert counted.calls == 4  # the second /a was kept
    c.get("/b", headers={"Accept-Encoding": "identity"})
    assert counted.calls == 5  # /b was not
    assert [key[0] for key in cache.pages] == ["/a", "/d", "/b"]


def test_a_page_larger_than_the_limit_is_not_kept():
    cache = PageCache(_app(b"x" * 5000), max_bytes=4000)
    assert TestClient(cache).get("/big").content == b"x" * 5000
    assert cache.pages == {} and cache.size == 0


def test_a_streamed_page_is_kept_whole():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        for part in (b"one ", b"two ", b"three"):
            await send({"type": "http.response.body", "body": part, "more_body": True})
        await send({"type": "http.response.body", "body": b""})

    cache = PageCache(app)
    c = TestClient(cache)
    assert c.get("/s").content == b"one two three"
    assert c.get("/s").content == b"one two three"
    assert [p.body for p in cache.pages.values()] == [b"one two three"]


def test_a_response_that_sets_a_cookie_is_not_kept():
    cache = PageCache(_app(b"x", headers=[(b"set-cookie", b"a=1")]))
    TestClient(cache).get("/")
    assert cache.pages == {}


# -- only on a read-only database ----------------------------------------------

# root may write a file whatever its mode
needs_a_user = pytest.mark.skipif(os.geteuid() == 0, reason="root can write it")

# The real app, imported with the data folder given, asked twice for each
# page through its whole middleware stack
APP_ON = """
import json
from fastapi.testclient import TestClient
from greek.web.app import app, startup_error
from greek.web.page_cache import PageCache

with TestClient(app) as client:
    answers = [
        client.get(path, headers={"Accept-Encoding": "gzip"})
        for path in ("/library", "/library", "/version", "/version")
    ]
layer, cache = app.middleware_stack, None
while layer is not None and cache is None:
    cache = layer if isinstance(layer, PageCache) else None
    layer = getattr(layer, "app", None)
print(json.dumps({
    "serving": startup_error is None,
    "kept": None if cache is None else [key[0] for key in cache.pages],
    "same": answers[0].content == answers[1].content,
    "headers": [dict(a.headers) for a in answers],
}))
"""


def _app_on(data: Path) -> dict:
    env = dict(os.environ, GREEK_DATA_DIR=str(data))
    result = subprocess.run(
        [sys.executable, "-c", APP_ON],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _database(data: Path) -> Path:
    data.mkdir()
    path = data / "greek.db"
    conn = sqlite3.connect(path)
    run_migrations(conn)
    conn.execute("PRAGMA journal_mode = DELETE")  # as greek export-data leaves it
    conn.close()
    return path


@needs_a_user
def test_the_public_sites_read_only_database_gets_the_cache(tmp_path):
    data = tmp_path / "public"
    _database(data).chmod(0o444)
    data.chmod(0o555)
    try:
        app = _app_on(data)
    finally:
        data.chmod(0o755)

    assert app["serving"]
    assert app["kept"] == ["/library"]  # not /version
    assert app["same"]
    hit = app["headers"][1]
    assert hit["content-encoding"] == "gzip"
    assert hit["content-security-policy"].startswith("default-src 'self';")
    assert hit["x-frame-options"] == "DENY"


def test_the_private_readers_live_database_gets_none(tmp_path):
    _database(tmp_path / "live")
    app = _app_on(tmp_path / "live")
    assert app["serving"]
    assert app["kept"] is None
    assert app["same"]
