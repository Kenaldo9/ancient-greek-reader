"""
The public site's outside check (deploy/public/check_site.py), run against
the app itself and against answers it must refuse.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from greek.db.repos.lines import insert_lines
from greek.db.repos.texts import insert_text

CHECK = Path(__file__).resolve().parents[2] / "deploy" / "public" / "check_site.py"
_spec = importlib.util.spec_from_file_location("check_site", CHECK)
check_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_site)
Response = check_site.Response


def _app_fetch(client, site):
    """fetch(url) against the app, as the tunnel passes requests on."""
    scheme, host = site.split("://")

    def fetch(url):
        assert url.startswith(site + "/"), url
        r = client.get(
            url[len(site) :],
            headers={"Host": host, "X-Forwarded-Proto": scheme},
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
    assert "/texts/" in lines[2] and "has its Greek" in lines[2]
    assert pauses == []


def test_behind_cloudflare_https_is_checked_too(client, db_text):
    site = "https://reader.example"
    app = _app_fetch(client, site)

    def cloudflare(url):
        if url.startswith("http://"):  # Always Use HTTPS
            return Response(301, {"location": "https://" + url[len("http://") :]}, b"")
        return app(url)

    passed, lines, _ = _run(site, cloudflare)
    assert passed, lines
    assert lines[-1].startswith("OK   https: http:// is sent on to https:// (301)")


def test_a_site_served_on_plain_http_fails_the_https_check(client, db_text):
    site = "https://reader.example"
    app = _app_fetch(client, site)

    def no_redirect(url):
        return (
            app(url.replace("http://", "https://"))
            if url.startswith("http://")
            else app(url)
        )

    passed, lines, pauses = _run(site, no_redirect)
    assert not passed
    assert lines[-1].startswith("FAIL https: http:// answered 200")
    assert pauses == [check_site.PAUSE] * (check_site.ATTEMPTS - 1)


def test_cloudflare_access_asking_for_a_login_is_named():
    def access(url):
        location = (
            "https://team.cloudflareaccess.com/cdn-cgi/access/login/reader.example"
        )
        return Response(302, {"location": location}, b"")

    passed, lines, _ = _run("https://reader.example", access)
    assert not passed
    assert "Cloudflare Access asks for a login" in lines[0]


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
    passed, lines, _ = _run("http://reader.example", lambda url: version)
    assert not passed
    assert lines[0].startswith("FAIL version") and problem in lines[0]


def test_a_failure_that_passes_on_a_retry_is_not_reported(client, db_text):
    app = _app_fetch(client, "http://testserver")
    calls = []

    def flaky(url):
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return app(url)

    passed, lines, pauses = _run("http://testserver", flaky)
    assert passed, lines
    assert pauses == [check_site.PAUSE]


def test_a_site_that_names_another_host_fails(client, web_db):
    tid = insert_text(web_db, "Iliad", "Homer", "iliad.txt", "/d/iliad.txt", 2)
    insert_lines(web_db, tid, ["μῆνιν ἄειδε", "θεά"], ["1.1", "1.2"])
    # The app names the host it was asked for; a check of another site's
    # address must not accept those URLs
    app = _app_fetch(client, "http://testserver")

    def other_host(url):
        return app(url.replace("http://elsewhere.example", "http://testserver"))

    passed, lines, _ = _run("http://elsewhere.example", other_host)
    assert not passed
    assert any(line.startswith("FAIL pages") for line in lines)
