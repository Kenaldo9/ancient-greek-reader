"""Integration tests for the FastAPI web interface.

Uses FastAPI's TestClient with an in-memory SQLite DB injected via
dependency_overrides, so no real DB file is touched.
"""

import re
from urllib.parse import urljoin

import pytest

from greek.db.repos.lines import insert_lines
from greek.db.repos.texts import insert_text


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------


def test_root_redirects_to_library(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/library"


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------


def test_library_empty(client):
    resp = client.get("/library")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_library_lists_texts(client, db_text):
    resp = client.get("/library")
    assert resp.status_code == 200
    assert "Iliad" in resp.text
    assert "Homer" in resp.text


def test_library_empty_shows_fetch_hint(client):
    resp = client.get("/library")
    assert "No texts yet" in resp.text
    assert "greek fetch" in resp.text


def test_library_card_contents(client, db_text):
    _, text_id = db_text
    resp = client.get("/library")
    assert "1 text from the Perseus Digital Library" in resp.text
    assert "1 book · 5 lines" in resp.text
    assert "Μῆνιν ἄειδε θεά" in resp.text  # first line
    assert f'href="/texts/{text_id}/read?at=1.1"' in resp.text  # book 1 link
    assert f'href="/texts/{text_id}/toc"' in resp.text
    assert "Recently studied" not in resp.text and "Last studied" not in resp.text


def test_library_start_reading_continues_where_this_browser_left_off(client, db_text):
    # positions.js turns it into "Continue at …" from localStorage
    _, text_id = db_text
    resp = client.get("/library")
    assert (
        f'href="/texts/{text_id}/read" data-resume="{text_id}" data-resume-label>'
        "Start reading</a>"
    ) in resp.text
    assert '<script src="/static/positions.js?v=' in resp.text


def test_library_prose_counts_sections(client, web_db):
    text_id = insert_text(web_db, "Histories", "Herodotus", "hdt.txt", "/d/hdt.txt", 3)
    insert_lines(
        web_db, text_id, ["Ἡροδότου", "Περσέων", "Ἰοῦν"], ["1.1.0", "1.2.1", "2.1.1"]
    )
    resp = client.get("/library")
    assert "2 books · 3 sections" in resp.text
    assert f'href="/texts/{text_id}/read?at=2.1.1"' in resp.text


# ---------------------------------------------------------------------------
# App shell
# ---------------------------------------------------------------------------


def test_pages_share_app_shell(client):
    resp = client.get("/library")
    assert resp.status_code == 200
    assert '<link rel="stylesheet" href="/static/fonts/fonts.css?v=' in resp.text
    assert '<nav class="main-nav" aria-label="Main">' in resp.text
    assert '<a href="/library" aria-current="page">' in resp.text
    assert resp.text.count('aria-current="page"') == 1


@pytest.mark.parametrize(
    "path", ["/bookmarks", "/history", "/verbs", "/analyses/abc", "/backup/greek.db"]
)
def test_the_pages_of_a_study_tool_are_gone(client, path):
    assert client.get(path).status_code == 404


def test_the_app_only_reads(client):
    """No route writes: no model calls, no one user's state (a reading app)."""
    from fastapi.routing import APIRoute

    from greek.web.app import app

    methods = {
        method
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert methods <= {"GET", "HEAD"}
    for path in (
        "/ask",
        "/analyze",
        "/verbs/parts",
        "/texts/1/glosses",
        "/texts/1/position",
    ):
        assert client.post(path).status_code in (404, 405)


def test_the_app_has_no_model_client():
    import subprocess
    import sys

    code = (
        "import sys, greek.web.app; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in "
        "('anthropic', 'openai', 'huggingface_hub') or m == 'greek.api.client'))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_reader_uses_app_shell_without_current_nav(client, db_text):
    _, text_id = db_text
    resp = client.get(f"/texts/{text_id}/read")
    nav = resp.text.split('<nav class="main-nav" aria-label="Main">')[1].split(
        "</nav>"
    )[0]
    assert "aria-current" not in nav


@pytest.mark.parametrize("path", ["/library", "/texts/{text_id}/read"])
def test_static_links_are_versioned_and_served(client, db_text, path):
    _, text_id = db_text
    html = client.get(path.format(text_id=text_id)).text
    urls = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
    assert {u.split("?")[0] for u in urls} >= {
        "/static/favicon.svg",
        "/static/apple-touch-icon.png",
        "/static/manifest.webmanifest",
        "/static/fonts/fonts.css",
        "/static/style.css",
        "/static/owl.svg",
        "/static/htmx.min.js",
    }
    for url in urls:
        assert re.search(r"\?v=[0-9a-f]{10}$", url), url
        assert client.get(url).status_code == 200, url
    if "read" in path:
        assert any(u.startswith("/static/reader.js?v=") for u in urls)


def test_home_screen_gets_the_owl_and_a_short_name(client):
    html = client.get("/library").text
    assert '<meta name="apple-mobile-web-app-title" content="Greek Reader">' in html
    url = re.search(r'<link rel="apple-touch-icon" href="([^"]+)">', html)[1]
    png = client.get(url).content
    # 180×180 and RGB without alpha: iPadOS would fill transparency with black
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    assert (width, height, png[25]) == (180, 180, 2)  # color type 2: RGB


@pytest.mark.parametrize("path", ["/library", "/texts/{text_id}/read"])
def test_the_home_screen_app_is_the_whole_site(client, db_text, path):
    # Without a scope iPadOS takes the page the app was added from as its
    # scope, and opens the pages outside it under an in-app browser bar
    _, text_id = db_text
    html = client.get(path.format(text_id=text_id)).text
    href = re.search(r'<link rel="manifest" href="([^"]+)">', html)[1]
    response = client.get(href)
    assert response.headers["content-type"] == "application/manifest+json"
    manifest = response.json()

    assert manifest["scope"] == "/"
    assert manifest["start_url"] == "/library"
    assert client.get(manifest["start_url"]).status_code == 200
    assert manifest["display"] == "standalone"
    assert manifest["name"] == "Greek Reader"  # as apple-mobile-web-app-title
    (icon,) = manifest["icons"]
    # relative to the manifest's URL
    assert urljoin(href, icon["src"]) == "/static/apple-touch-icon.png"
    assert client.get(urljoin(href, icon["src"])).status_code == 200
    assert icon["sizes"] == "180x180"


def test_pages_scripts_and_styles_come_compressed(client, db_text):
    # A book page is about 690 KB of HTML, and 50–70 KB with gzip
    _, text_id = db_text
    for path in (
        f"/texts/{text_id}/read",
        "/library",
        "/static/style.css",
        "/static/reader.js",
    ):
        resp = client.get(path, headers={"Accept-Encoding": "gzip"})
        assert resp.headers["content-encoding"] == "gzip", path
        assert resp.headers["vary"] == "Accept-Encoding", path
        assert resp.num_bytes_downloaded < len(resp.content) / 2, path
    plain = client.get(
        f"/texts/{text_id}/read", headers={"Accept-Encoding": "identity"}
    )
    assert "content-encoding" not in plain.headers
    assert plain.num_bytes_downloaded == len(plain.content)


def test_fonts_and_images_are_sent_as_they_are(client):
    # they are compressed already
    for path in (
        "/static/fonts/gentium-book-plus/gentium-book-plus-greek-ext-400.woff2",
        "/static/apple-touch-icon.png",
    ):
        resp = client.get(path, headers={"Accept-Encoding": "gzip"})
        assert resp.status_code == 200
        assert "content-encoding" not in resp.headers, path


def test_static_fonts_served(client):
    resp = client.get("/static/fonts/fonts.css")
    assert resp.status_code == 200
    assert "Gentium Book Plus" in resp.text
    font = client.get(
        "/static/fonts/gentium-book-plus/gentium-book-plus-greek-ext-400.woff2"
    )
    assert font.status_code == 200


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


def test_read_text_not_found(client):
    resp = client.get("/texts/9999/read")
    assert resp.status_code == 404


def test_read_text_default_page(client, db_text):
    _, text_id = db_text
    resp = client.get(f"/texts/{text_id}/read")
    assert resp.status_code == 200
    assert "Μῆνιν" in resp.text


def test_read_text_page_2(client, db_text):
    _, text_id = db_text
    # Page 2 is beyond our 5 lines but should still return 200
    resp = client.get(f"/texts/{text_id}/read?page=2")
    assert resp.status_code == 200


def test_read_text_specific_lines(client, db_text):
    _, text_id = db_text
    resp = client.get(f"/texts/{text_id}/read?lines=1-3")
    assert resp.status_code == 200
    assert "Μῆνιν" in resp.text


def test_read_text_bad_lines_range(client, db_text):
    _, text_id = db_text
    resp = client.get(f"/texts/{text_id}/read?lines=notarange")
    assert resp.status_code == 400


def test_read_at_unknown_reference(client, db_text):
    _, text_id = db_text
    resp = client.get(f"/texts/{text_id}/read?at=9.9")
    assert resp.status_code == 400


def test_read_at_opens_the_book_containing_line(client, web_db):
    text_id = insert_text(web_db, "Long", None, "long.txt", "/d/long.txt", 45)
    insert_lines(
        web_db,
        text_id,
        [f"στίχος-{i}" for i in range(1, 46)],
        [f"1.{i}" for i in range(1, 46)],
    )
    resp = client.get(f"/texts/{text_id}/read?at=1.42")
    assert resp.status_code == 200
    assert "στίχος-42" in resp.text
    assert "στίχος-1<" in resp.text  # the page is the whole book
    assert 'data-at="42"' in resp.text


# ---------------------------------------------------------------------------
# TOC
# ---------------------------------------------------------------------------


def test_toc_not_found(client):
    resp = client.get("/texts/9999/toc")
    assert resp.status_code == 404


def test_toc_returns_sections(client, db_text):
    _, text_id = db_text
    resp = client.get(f"/texts/{text_id}/toc")
    assert resp.status_code == 200
    # Our lines have refs 1.1-1.5, all in book "1"
    assert "1" in resp.text


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_not_found(client):
    resp = client.get("/texts/9999/search")
    assert resp.status_code == 404


def test_search_no_query(client, db_text):
    _, text_id = db_text
    resp = client.get(f"/texts/{text_id}/search")
    assert resp.status_code == 200


def test_search_with_query(client, db_text):
    _, text_id = db_text
    resp = client.get(f"/texts/{text_id}/search?q=Μῆνιν")
    assert resp.status_code == 200
    assert "Μῆνιν" in resp.text


def test_search_empty_query_string(client, db_text):
    _, text_id = db_text
    resp = client.get(f"/texts/{text_id}/search?q=")
    assert resp.status_code == 200
