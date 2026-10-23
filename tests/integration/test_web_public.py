"""
The app on the open internet: no API docs, security headers on every
response, how long static files may be kept, and what search engines may
index (robots.txt, the sitemap and the reader's canonical link).
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from greek.db.repos.lines import insert_lines
from greek.db.repos.texts import insert_text
from greek.web.app import SECURITY_HEADERS
from greek.web.templates_config import static_url

TEMPLATES = Path(__file__).resolve().parents[2] / "greek" / "web" / "templates"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_no_api_docs(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "path", ["/library", "/about", "/version", "/static/style.css", "/no-such-page"]
)
def test_every_response_carries_the_security_headers(client, db_text, path):
    response = client.get(path)
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value
    assert response.headers["Content-Security-Policy"].startswith("default-src 'self';")


def test_templates_have_no_inline_code_or_style_attributes():
    """The Content-Security-Policy allows the app's own files only."""
    for template in TEMPLATES.glob("*.html"):
        html = template.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), template.name
        assert "<style" not in html, template.name
        assert not re.search(r"\sstyle=", html), template.name
        assert not re.search(r"\son[a-z]+=", html), template.name


def test_htmx_injects_no_style_element(client):
    html = client.get("/library").text
    assert (
        """<meta name="htmx-config" content='{"includeIndicatorStyles": false}'>"""
        in html
    )


# -- static files ---------------------------------------------------------------


def test_a_versioned_static_file_is_kept_for_a_year(client):
    response = client.get(static_url("style.css"))
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_a_static_file_under_an_old_version_is_asked_for_again(client):
    response = client.get("/static/style.css?v=0000000000")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-cache"
    assert client.get("/static/style.css").headers["Cache-Control"] == "no-cache"


def test_fonts_are_kept_for_a_week(client):
    response = client.get("/static/fonts/ibm-plex-sans/ibm-plex-sans-latin.woff2")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "public, max-age=604800"


def test_an_unchanged_file_answers_304_with_its_lifetime(client):
    url = static_url("reader.js")
    etag = client.get(url).headers["etag"]
    response = client.get(url, headers={"If-None-Match": etag})
    assert response.status_code == 304
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"


# -- what search engines may index ----------------------------------------------


def _long_book(web_db):
    """An Iliad whose Book 1 is long enough to be split into parts."""
    tid = insert_text(web_db, "Iliad", "Homer", "iliad.txt", "/d/iliad.txt", 102)
    insert_lines(
        web_db,
        tid,
        ["μῆνιν " * 170] * 100 + ["ἄνδρα μοι"] * 2,
        [f"1.{n}" for n in range(1, 101)] + ["2.1", "2.2"],
    )
    return tid


def _allowed(robots: str, url: str) -> bool:
    """RFC 9309 for one user agent: the longest matching rule wins, Allow on a tie."""
    best = (-1, True)
    for kind, pattern in re.findall(r"^(Allow|Disallow): (\S+)$", robots, re.M):
        regex = "".join(".*" if c == "*" else re.escape(c) for c in pattern.rstrip("$"))
        if re.match(regex + ("$" if pattern.endswith("$") else ""), url):
            best = max(best, (len(pattern), kind == "Allow"))
    return best[1]


def test_robots_lets_search_engines_index_the_books_but_not_the_panels(client, web_db):
    tid = _long_book(web_db)
    robots = client.get("/robots.txt")
    assert robots.headers["content-type"].startswith("text/plain")
    text = robots.text

    parts = re.findall(rf"^Allow: /texts/{tid}/read\?at=(\S+)\$$", text, re.M)
    assert parts[0] == "1.1" and parts[-1] == "2.1"
    assert len(parts) >= 4  # Book 1 is 100 lines of 1,020 characters
    for path in ["/library", "/about", f"/texts/{tid}/toc", "/static/style.css"] + [
        f"/texts/{tid}/read?at={at}" for at in parts
    ]:
        assert _allowed(text, path), path
    for path in [
        f"/texts/{tid}/read",
        f"/texts/{tid}/read?at=1.2",
        f"/texts/{tid}/read?at=1.1&interlinear=true",
        f"/texts/{tid}/read?lines=1.1-1.3",
        f"/texts/{tid}/read?q=μῆνιν",
        f"/texts/{tid}/word?line=1&i=0",
        f"/texts/{tid}/passage?lines=1-2",
        f"/texts/{tid}/search?q=a",
        "/goto?q=Iliad",
        "/version",
    ]:
        assert not _allowed(text, path), path
    assert text.endswith("\nSitemap: http://testserver/sitemap.xml\n")


def test_the_sitemap_lists_every_page_the_robots_allow(client, web_db):
    tid = _long_book(web_db)
    headers = {"X-Forwarded-Proto": "https", "Host": "reader.example"}

    response = client.get("/sitemap.xml", headers=headers)

    assert response.headers["content-type"] == "application/xml"
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [e.text for e in ET.fromstring(response.content).findall("s:url/s:loc", ns)]
    site = "https://reader.example"
    assert locs[:3] == [f"{site}/library", f"{site}/about", f"{site}/texts/{tid}/toc"]
    robots = client.get("/robots.txt", headers=headers).text
    allowed = re.findall(r"^Allow: (\S+)\$$", robots, re.M)
    assert locs[3:] == [site + path for path in allowed]
    assert robots.endswith("\nSitemap: https://reader.example/sitemap.xml\n")


def test_a_page_opened_anywhere_names_its_start_as_canonical(client, web_db):
    tid = _long_book(web_db)
    robots = client.get("/robots.txt").text
    parts = re.findall(rf"^Allow: /texts/{tid}/read\?at=(\S+)\$$", robots, re.M)
    second = parts[1]  # Book 1's second part
    inside = f"1.{int(second.split('.')[1]) + 1}"

    for query in (f"at={inside}&interlinear=true", f"lines={inside}-{inside}"):
        html = client.get(f"/texts/{tid}/read?{query}").text
        assert (
            f'<link rel="canonical" href="http://testserver/texts/{tid}/read?at={second}">'
            in html
        )
    html = client.get(f"/texts/{tid}/read").text
    assert f'href="http://testserver/texts/{tid}/read?at=1.1">' in html


def test_a_text_without_references_is_one_page_from_its_first_line(client, web_db):
    tid = insert_text(web_db, "Plain", None, "p.txt", "/d/p.txt", 2)
    insert_lines(web_db, tid, ["λόγος", "ἔργον"])
    robots = client.get("/robots.txt").text
    assert f"Allow: /texts/{tid}/read?at=1$" in robots
    html = client.get(f"/texts/{tid}/read").text
    assert (
        f'<link rel="canonical" href="http://testserver/texts/{tid}/read?at=1">' in html
    )
