"""Saved-analysis and TOC pages remain usable after removing the old UI."""

from greek.db.repos.lines import insert_lines
from greek.db.repos.texts import insert_text


def test_toc_links_to_book_start_without_selecting_entire_book(client, db_text):
    _, text_id = db_text
    response = client.get(f"/texts/{text_id}/toc")
    assert response.status_code == 200
    assert 'class="page-title"' in response.text
    assert 'aria-label="Table of contents"' in response.text
    # by its reference, as the Library links it and the reader's canonical
    # link names it
    assert f'href="/texts/{text_id}/read?at=1.1"' in response.text
    assert "1.1–1.5" in response.text
    assert "5 lines" in response.text
    assert "?lines=1-5" not in response.text


def test_toc_empty_text_has_readable_empty_state(client, web_db):
    tid = insert_text(web_db, "Empty", None, "empty.txt", "/d/empty.txt", 0)
    response = client.get(f"/texts/{tid}/toc")
    assert response.status_code == 200
    assert "No structure information available" in response.text
    assert 'class="panel empty-state"' in response.text


def test_toc_prose_counts_sections_and_opens_each_book(client, web_db):
    tid = insert_text(web_db, "Histories", "Herodotus", "h.txt", "/d/h.txt", 3)
    insert_lines(
        web_db, tid, ["Ἡροδότου", "Περσέων", "Ἰοῦν"], ["1.1.0", "1.2.1", "2.1.1"]
    )
    html = client.get(f"/texts/{tid}/toc").text
    assert "2 sections" in html
    assert "1 section" in html
    assert "1.1.0–1.2.1" in html
    assert f'href="/texts/{tid}/read?at=2.1.1"' in html


def test_toc_without_references_uses_integer_range(client, web_db):
    tid = insert_text(web_db, "Plain", None, "p.txt", "/d/p.txt", 2)
    insert_lines(web_db, tid, ["λόγος", "ἔργον"])
    html = client.get(f"/texts/{tid}/toc").text
    assert "1–2" in html
    assert "2 lines" in html
    assert "None" not in html
