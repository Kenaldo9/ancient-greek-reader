"""
The public site's outside check (deploy/public/check_site.py), run against
the app itself and against answers it must refuse.
"""

import gzip
import importlib.util
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from greek.db.repos.lines import insert_lines
from greek.db.repos.texts import insert_text

CHECK = Path(__file__).resolve().parents[2] / "deploy" / "public" / "check_site.py"
_spec = importlib.util.spec_from_file_location("check_site", CHECK)
check_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_site)
Response = check_site.Response
GZIP = {"Accept-Encoding": "gzip"}


def _app_fetch(client, site):
    """fetch(url) against the app, as the tunnel passes requests on."""
    scheme, host = site.split("://")

    def fetch(url, more=None):
        assert url.startswith(site + "/"), url
        r = client.get(
            url[len(site) :],
            headers={"Host": host, "X-Forwarded-Proto": scheme} | (more or {}),
            follow_redirects=False,
        )
        return Response(
            r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content
        )

    return fetch


def _run(site, fetch):
    lines, pauses = [], []
    passed = check_site.run(site, fetch, sleep=pauses.append, out=lines.append)
    return passed, lines, pauses


def test_the_app_passes_every_check(client, db_text):
    passed, lines, pauses = _run(
        "http://testserver", _app_fetch(client, "http://testserver")
    )
    assert passed, lines
    assert [line.split(":")[0] for line in lines] == [
        "OK   version",
        "OK   library",
        "OK   pages",
    ]
    assert "the sitemap's 4 pages and 1 texts' starts" in lines[2]
    assert "every reader page has its Greek" in lines[2]
    assert pauses == []


def test_every_page_is_opened_with_interlinear_on_and_off(client, db_text):
    _, text_id = db_text
    site = "http://testserver"
    app = _app_fetch(client, site)
    opened = set()

    def recording(url, more=None):
        r = app(url, more)
        opened.add((url[len(site) :], bool(more), "interlinear-on" in r.text))
        return r

    check_site.check_pages(recording, site)
    read = f"/texts/{text_id}/read"
    pages = ["/library", "/about", f"/texts/{text_id}/toc", read + "?at=1.1", read]
    assert {path for path, _, _ in opened} == {*pages, "/robots.txt", "/sitemap.xml"}
    for path in pages:
        reader = path.startswith(read)
        # Without a cookie Interlinear is on; the check's cookie is the
        # reader's own for off
        assert (path, False, reader) in opened
        assert (path, True, False) in opened


def test_after_the_check_a_reader_finds_every_book_in_the_cache(client, db_text):
    from greek.web.app import app
    from greek.web.page_cache import PageCache

    site = "https://reader.example"
    # A request as Cloudflare passes it on to the site
    cloudflare = {"Host": "reader.example", "X-Forwarded-Proto": "https"}
    cache = PageCache(app)
    with TestClient(cache) as c:

        def fetch(url, more=None):
            r = c.get(url[len(site) :], headers=cloudflare | GZIP | (more or {}))
            headers = {k.lower(): v for k, v in r.headers.items()}
            return Response(r.status_code, headers, r.content)

        check_site.check_pages(fetch, site)
        kept = len(cache.pages)
        # A browser's request, with and without Interlinear: every link to a
        # reader page in the Library and the contents
        browser = cloudflare | {"Accept-Encoding": "gzip, deflate, br"}
        links = set()
        for page in ("/library", f"/texts/{db_text[1]}/toc"):
            html = c.get(page, headers=browser).text
            links |= set(re.findall(r'href="(/texts/\d+/read[^"]*)"', html))
        assert len(links) == 2  # Start reading and book 1
        for url in links:
            for cookie in ({}, check_site.INTERLINEAR_OFF):
                assert c.get(url, headers=browser | cookie).status_code == 200
        assert len(cache.pages) == kept


def test_behind_cloudflare_https_is_checked_too(client, db_text):
    site = "https://reader.example"
    app = _app_fetch(client, site)

    def cloudflare(url, more=None):
        if url.startswith("http://"):  # Always Use HTTPS
            return Response(301, {"location": "https://" + url[len("http://") :]}, b"")
        return app(url, more)

    passed, lines, _ = _run(site, cloudflare)
    assert passed, lines
    assert lines[-1].startswith("OK   https: http:// is sent on to https:// (301)")


def test_a_site_served_on_plain_http_fails_the_https_check(client, db_text):
    site = "https://reader.example"
    app = _app_fetch(client, site)

    def no_redirect(url, more=None):
        return (
            app(url.replace("http://", "https://"), more)
            if url.startswith("http://")
            else app(url, more)
        )

    passed, lines, pauses = _run(site, no_redirect)
    assert not passed
    assert lines[-1].startswith("FAIL https: http:// answered 200")
    assert pauses == [check_site.PAUSE] * (check_site.ATTEMPTS - 1)


def test_cloudflare_access_asking_for_a_login_is_named():
    def access(url, more=None):
        location = (
            "https://team.cloudflareaccess.com/cdn-cgi/access/login/reader.example"
        )
        return Response(302, {"location": location}, b"")

    passed, lines, _ = _run("https://reader.example", access)
    assert not passed
    assert "Cloudflare Access asks for a login" in lines[0]


class _Answer:
    """What urllib's opener answers with, as far as http_fetch reads it."""

    def __init__(self, status, headers, body=b""):
        self.status, self.headers, self.body = status, headers, body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_access_token_goes_over_https_only(monkeypatch):
    sent = {}

    class Opener:
        def open(self, request, timeout):
            url = request.full_url
            sent[url] = {name.lower() for name in request.headers}
            if url.startswith("http://"):  # Always Use HTTPS
                return _Answer(301, {"Location": "https://" + url[len("http://") :]})
            return _Answer(200, {})

    monkeypatch.setattr(
        check_site.urllib.request, "build_opener", lambda *handlers: Opener()
    )
    token = {"CF-Access-Client-Id": "id", "CF-Access-Client-Secret": "secret"}
    fetch = check_site.http_fetch({"User-Agent": "check"}, token)
    # The https check asks for http:// itself
    assert "sent on to https://" in check_site.check_https(
        fetch, "https://reader.example"
    )
    fetch("https://reader.example/library")
    assert sent == {
        "http://reader.example/library": {"user-agent"},
        "https://reader.example/library": {
            "user-agent",
            "cf-access-client-id",
            "cf-access-client-secret",
        },
    }


def test_a_gzipped_answer_is_unzipped(monkeypatch):
    page = "<p>μῆνιν ἄειδε θεά</p>".encode()

    class Opener:
        def open(self, request, timeout):
            return _Answer(200, {"Content-Encoding": "gzip"}, gzip.compress(page))

    monkeypatch.setattr(
        check_site.urllib.request, "build_opener", lambda *handlers: Opener()
    )
    assert check_site.http_fetch(GZIP)("https://reader.example/").body == page


def _version(**database):
    body = {
        "code": {"commit": "abc1234", "built": None},
        "database": {"version": "020_reading_app", "pending": []} | database,
        "serving": True,
    }
    return Response(200, {}, json.dumps(body).encode())


@pytest.mark.parametrize(
    "version, problem",
    [
        (_version(pending=["021_next"]), "not migrated"),
        (_version(version=None), "not migrated"),
        (Response(503, {}, b"down"), "answered 503"),
        (Response(530, {}, b"<html>Cloudflare: tunnel error</html>"), "answered 530"),
    ],
)
def test_a_version_the_site_should_not_have_fails(version, problem):
    passed, lines, _ = _run("http://reader.example", lambda url, more=None: version)
    assert not passed
    assert lines[0].startswith("FAIL version") and problem in lines[0]


def test_a_failure_that_passes_on_a_retry_is_not_reported(client, db_text):
    app = _app_fetch(client, "http://testserver")
    calls = []

    def flaky(url, more=None):
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return app(url, more)

    passed, lines, pauses = _run("http://testserver", flaky)
    assert passed, lines
    assert pauses == [check_site.PAUSE]


def test_a_site_that_names_another_host_fails(client, web_db):
    tid = insert_text(web_db, "Iliad", "Homer", "iliad.txt", "/d/iliad.txt", 2)
    insert_lines(web_db, tid, ["μῆνιν ἄειδε", "θεά"], ["1.1", "1.2"])
    # The app names the host it was asked for; a check of another site's
    # address must not accept those URLs
    app = _app_fetch(client, "http://testserver")

    def other_host(url, more=None):
        return app(url.replace("http://elsewhere.example", "http://testserver"), more)

    passed, lines, _ = _run("http://elsewhere.example", other_host)
    assert not passed
    assert any(line.startswith("FAIL pages") for line in lines)
