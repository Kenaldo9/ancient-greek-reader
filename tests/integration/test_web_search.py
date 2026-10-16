"""Integration tests for search in the reader's text pane (accents and capitals
ignored), the word strip's "Find in" link, and the old search URL."""

from urllib.parse import quote

import pytest

from greek.db.repos.lines import insert_lines
from greek.db.repos.texts import insert_text


@pytest.fixture
def iliad(web_db):
    tid = insert_text(web_db, "Iliad", "Homer", "il.txt", "/d/il.txt", 5)
    insert_lines(
        web_db,
        tid,
        [
            "μῆνιν ἄειδε θεὰ Πηληϊάδεω Ἀχιλῆος",
            "οὐλομένην, ἣ μυρί᾽ Ἀχαιοῖς ἄλγε᾽ ἔθηκε,",
            "Μῆνιν Ἀπόλλωνος ἑκατηβελέταο ἄνακτος·",
            "μῆνιν ἀλευάμενος ἑκατηβόλου Ἀπόλλωνος.",
            "ἄλλο τι",
        ],
        ["1.1", "1.2", "1.75", "5.444", "5.445"],
    )
    return tid


def _search(client, tid, q, **params):
    return client.get(f"/texts/{tid}/read", params={"q": q, **params}).text


def test_search_results_replace_the_lines(client, iliad):
    html = _search(client, iliad, "μηνιν", at="1.2")
    assert 'id="text-lines"' not in html
    assert '<input type="search" name="q" value="μηνιν"' in html
    assert "3 lines in the Iliad" in html
    assert "Accents and capitals ignored" in html
    assert html.index("Book 1</h2>") < html.index("Book 5</h2>")
    assert "<mark>μῆνιν</mark> ἄειδε" in html and "<mark>Μῆνιν</mark> Ἀπόλλωνος" in html
    assert f'href="/texts/{iliad}/read?at=1.75&amp;hit=1.75"' in html


def test_search_back_link_returns_to_the_page(client, iliad):
    html = _search(client, iliad, "μηνιν", at="1.2")
    assert "Back to 1.2" in html
    assert f'href="/texts/{iliad}/read?at=1.2"' in html
    assert 'name="at" value="1.2"' in html  # kept when searching again


def test_search_marks_hit_at_current_position(client, iliad):
    html = _search(client, iliad, "μηνιν", at="1.75")
    current = html.split('class="search-hit is-current"')[1].split("</a>")[0]
    assert '<span class="ref">1.75</span>' in current


def test_search_empty_query_shows_how_to_search(client, iliad):
    html = _search(client, iliad, "")
    assert "Type a word or a phrase" in html
    assert "autofocus" in html


def test_search_no_results(client, iliad):
    html = _search(client, iliad, "ζζζ")
    assert "No lines in the Iliad contain “ζζζ”" in html


def test_search_prose_counts_sections(client, web_db):
    tid = insert_text(web_db, "Histories", "Herodotus", "h.txt", "/d/h.txt", 2)
    insert_lines(
        web_db, tid, ["Ἡροδότου Ἁλικαρνησσέος", "Περσέων μέν"], ["1.1.0", "1.1.1"]
    )
    assert "1 section in the Histories" in _search(client, tid, "ηροδοτου")
    assert "No sections in the Histories contain" in _search(client, tid, "ζζζ")


def test_search_long_section_is_cut_around_the_match(client, web_db):
    tid = insert_text(web_db, "Histories", "Herodotus", "h.txt", "/d/h.txt", 1)
    words = [f"λόγος{i}" for i in range(80)]
    insert_lines(
        web_db, tid, [" ".join(words[:40] + ["μῆνιν"] + words[40:])], ["1.1.0"]
    )
    html = _search(client, tid, "μηνιν")
    assert "<mark>μῆνιν</mark>" in html
    assert "… λόγος" in html and "λόγος0 " not in html


def test_search_says_when_results_are_cut(client, iliad, monkeypatch):
    monkeypatch.setattr("greek.web.reader_data.SEARCH_LIMIT", 2)
    html = _search(client, iliad, "μηνιν")
    assert html.count('class="search-hit') == 2
    assert "Showing the first 2 of 3" in html


def test_search_does_not_move_the_reading_position(client, iliad):
    # reader.js saves the position from the text's lines; results have none
    html = _search(client, iliad, "μηνιν", at="1.75")
    assert 'id="text-lines"' not in html


def test_search_input_is_never_an_error(client, iliad):
    for q in ['"', "AND", "(μηνιν", "-", "a:b"]:
        assert client.get(f"/texts/{iliad}/read", params={"q": q}).status_code == 200


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


def test_reader_has_search_button(client, iliad):
    html = client.get(f"/texts/{iliad}/read?at=1.2").text
    assert f'href="/texts/{iliad}/read?at=1.2&amp;q="' in html
    assert 'aria-label="Search in Iliad"' in html


def test_reader_marks_the_line_opened_from_a_result(client, iliad):
    html = client.get(f"/texts/{iliad}/read?at=1.75&hit=1.75").text
    line = html.split('data-line="3"')[0].rsplit("<div", 1)[1]
    assert "is-hit" in line
    assert html.count("is-hit") == 1


def test_word_strip_finds_the_word_in_the_text(client, iliad):
    html = client.get(f"/texts/{iliad}/word", params={"line": 3, "i": 0}).text
    assert "Find in Iliad · 3 lines" in html
    assert f'href="/texts/{iliad}/read?at=1.75&amp;q={quote("Μῆνιν")}"' in html


# ---------------------------------------------------------------------------
# Old search URL
# ---------------------------------------------------------------------------


def test_old_search_url_redirects_to_reader(client, iliad):
    resp = client.get(f"/texts/{iliad}/search?q=μῆνιν", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/texts/{iliad}/read?q={quote('μῆνιν')}"
