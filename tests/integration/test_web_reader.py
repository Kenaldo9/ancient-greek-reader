"""Integration tests for the reader: page, passage box, word strip, glosses, Go to.

Everything the reader shows is saved data; it makes no model calls.
"""

import pytest

from greek.db.repos.lines import insert_lines
from greek.db.repos.study import save_study_result
from greek.db.repos.texts import insert_text
from greek.models import LineTranslation, TranslationUnit, WordStudy
from greek.web.reader_data import MAX_SELECTION_LINES
from tests.integration.test_web_study import tag

# Book 1 has 45 lines, book 2 has 3; each book is one page. Every line has 2 words.
BOOK1 = [(f"1.{i}", f"λόγος{i} ἔργον{i}") for i in range(1, 46)]
BOOK2 = [(f"2.{i}", f"νόμος{i} φύσις{i}") for i in range(1, 4)]

OPUS = "claude-opus-5-5"


@pytest.fixture
def text_id(web_db):
    tid = insert_text(web_db, "Iliad", "Homer", "iliad.txt", "/d/iliad.txt", 48)
    insert_lines(
        web_db,
        tid,
        [c for _, c in BOOK1 + BOOK2],
        [r for r, _ in BOOK1 + BOOK2],
    )
    return tid


def _passage(start, end):
    return "\n".join(c for _, c in (BOOK1 + BOOK2)[start - 1 : end])


def _study(db, tid, start, end, model=OPUS):
    """The study data for lines start–end: one unit, literal lines, glosses."""
    unit = TranslationUnit(tid, start, end, f"Fluent {start}–{end}.", model)
    literal = [
        LineTranslation(tid, n, f"literal {n}", model) for n in range(start, end + 1)
    ]
    words = [
        WordStudy(
            text_id=tid,
            line_number=n,
            token_index=i,
            gloss=f"gloss {n}.{i}",
            note=f"note {n}.{i}" if i == 0 else "",
            parse_check=None,
            model_used=model,
        )
        for n in range(start, end + 1)
        for i in range(2)
    ]
    save_study_result(db, unit, literal, words, [])


# ---------------------------------------------------------------------------
# Reader page
# ---------------------------------------------------------------------------


def test_reader_page_has_all_panes(client, text_id):
    html = client.get(f"/texts/{text_id}/read").text
    for marker in (
        'class="reader-nav"',
        'id="text-pane"',
        'id="word-strip"',
        'id="passage-box"',
        "Select lines to study them",
        "Click a Greek word in the text",
        '<script src="/static/reader.js?v=',
    ):
        assert marker in html
    assert (
        f'href="/texts/{text_id}/read" data-resume="{text_id}" aria-current="page"'
        in html
    )
    assert 'value="Iliad 1.1"' in html  # Go to box
    for gone in ("model-select", "gloss-key", "bookmark-btn", "nav-bookmarks", "/ask"):
        assert gone not in html


def test_reader_drawer_has_room_for_the_ipad_header_tools(client, text_id):
    html = client.get(f"/texts/{text_id}/read").text
    # reader.js moves Go to into the drawer on an iPad and a phone, and a
    # phone's Interlinear; the slots are empty, so hidden, everywhere else
    assert '<div class="drawer-slot" data-slot="goto"></div>' in html
    assert '<div class="nav-card drawer-slot" data-slot="interlinear"></div>' in html
    assert 'data-slot="settings"' not in html
    assert '<a class="nav-item" href="/library">' in html
    for href in ("/bookmarks", "/history", "/verbs"):
        assert f'href="{href}"' not in html


def test_reader_study_panel_has_a_handle_for_a_phone(client, text_id):
    # on a phone the study panel is a sheet; its handle closes it (reader.js)
    html = client.get(f"/texts/{text_id}/read").text
    side = html.index(
        '<div class="reader-side" id="reader-side" aria-label="Study panel">'
    )
    handle = html.index(
        '<button type="button" class="sheet-handle" data-action="close-sheet"'
        ' aria-label="Close the study panel" title="Close"></button>'
    )
    assert side < handle < html.index('<div class="reader-tabs" role="tablist"')


def test_reader_shows_the_whole_book(client, text_id):
    html = client.get(f"/texts/{text_id}/read?at=1.1").text
    assert 'data-line="1"' in html and 'data-line="45"' in html
    assert 'data-line="46"' not in html  # 2.1 belongs to the next book
    # the line at the top of the text (reader.js follows the scroll) / the last
    assert (
        '<button type="button" class="pane-range ref" id="pane-range" data-end="1.45"'
        ' title="Go to a line">1.1 / 1.45</button>' in html
    )
    assert f'hx-get="/texts/{text_id}/read?at=2.1"' in html
    assert 'aria-label="Next: Book 2"' in html
    assert "Previous:" not in html  # nothing before 1.1
    assert "Continue to Book 2</a>" in html
    assert 'data-at="1"' in html


def test_reader_opened_mid_book_marks_that_line(client, text_id):
    html = client.get(f"/texts/{text_id}/read?at=1.42").text
    assert 'data-line="1"' in html and 'data-line="45"' in html
    assert 'data-at="42"' in html
    assert "Continue to Book 2</a>" in html


def test_reader_last_book_links_back_to_the_previous_book(client, text_id):
    html = client.get(f"/texts/{text_id}/read?at=2.1").text
    assert "· Book 2" in html
    assert f'hx-get="/texts/{text_id}/read?at=1.1"' in html
    assert 'aria-label="Previous: Book 1"' in html
    assert "Next:" not in html and "Continue to" not in html
    assert 'aria-label="Book 2" aria-current="true"' in html


def test_reader_marks_the_opened_line_from_a_selection_or_old_page_link(
    client, text_id
):
    assert 'data-at="3"' in client.get(f"/texts/{text_id}/read?lines=1.3-1.5").text
    html = client.get(f"/texts/{text_id}/read?page=2").text  # 40-line pages
    assert 'data-at="41"' in html and 'data-line="1"' in html


def test_reader_page_follows_the_position_as_it_scrolls(client, text_id):
    html = client.get(f"/texts/{text_id}/read?at=1.42").text
    assert 'data-end="1.45" title="Go to a line">1.42 / 1.45</button>' in html
    # the search link names the line reader.js keeps it at
    assert f'data-follows-position href="/texts/{text_id}/read?at=1.42&amp;q="' in html
    assert 'data-title="Iliad"' in html  # for the Go to box
    # no snapshot of a whole book in localStorage; Back loads the page again
    assert 'id="reader" data-text-id="' in html and 'hx-history="false"' in html


def _prose(web_db, chapters=4, sections=3, words=830):
    """A prose book of `chapters` × `sections` sections of ~5,000 characters."""
    refs = [f"1.{c}.{s}" for c in range(1, chapters + 1) for s in range(sections)]
    tid = insert_text(web_db, "Histories", "Herodotus", "h.txt", "/d/h.txt", len(refs))
    insert_lines(web_db, tid, [" ".join(["λόγος"] * words)] * len(refs), refs)
    return tid


def test_reader_splits_a_long_prose_book_at_a_chapter(client, web_db):
    tid = _prose(web_db)  # 12 sections, ~60,000 characters: two parts
    html = client.get(f"/texts/{tid}/read").text
    assert "text-pane is-prose" in html
    # a phone leaves out the part (style.css): the range is under it
    assert '· Book 1<span class="pane-title-part">, part 1 of 2</span>' in html
    assert 'data-line="6"' in html and 'data-line="7"' not in html
    assert 'data-end="1.2.2" title="Go to a line">1.1.0 / 1.2.2</button>' in html
    assert "Continue to Book 1, part 2 of 2</a>" in html
    assert "Select section 1.1.0" in html
    html = client.get(f"/texts/{tid}/read?at=1.3.1").text
    assert '· Book 1<span class="pane-title-part">, part 2 of 2</span>' in html
    assert 'data-line="7"' in html and 'data-line="6"' not in html
    assert 'aria-label="Previous: Book 1, part 1 of 2"' in html
    assert 'data-end="1.4.2" title="Go to a line">1.3.1 / 1.4.2</button>' in html


def test_reader_without_a_place_opens_at_the_start(client, text_id):
    # reader.js then continues where this browser last read (localStorage)
    html = client.get(f"/texts/{text_id}/read").text
    assert 'data-at="1"' in html
    assert 'value="Iliad 1.1"' in html


def test_reader_glosses_come_from_the_study_data(client, web_db, text_id):
    _study(web_db, text_id, 1, 2)
    html = client.get(f"/texts/{text_id}/read?at=1.1&interlinear=true").text
    assert 'class="line has-glosses" data-line="1"' in html
    assert '<span class="w-t">gloss 2.1</span>' in html
    # a line without study glosses keeps the Greek alone
    assert 'class="line" data-line="3"' in html
    assert "gloss-mark" not in html and "awaits-glosses" not in html


def test_reader_selection_shows_the_study_data(client, web_db, text_id):
    _study(web_db, text_id, 1, 2)
    html = client.get(f"/texts/{text_id}/read?lines=1.1-1.2").text
    assert 'data-start="1" data-end="2"' in html
    assert "passage-box is-study" in html
    assert f'<span class="ref">1.1–1.2</span>&nbsp;{tag("AI")}</h3>' in html
    assert "Fluent 1–2." in html
    assert '<span class="literal-text">literal 2</span>' in html
    assert "pb-ask" not in html and "Analyze" not in html


def test_interlinear_is_on_until_the_cookie_turns_it_off(client, web_db, text_id):
    _study(web_db, text_id, 1, 2)
    html = client.get(f"/texts/{text_id}/read").text  # a first visit
    assert 'class="reader interlinear-on"' in html
    assert 'id="interlinear-toggle" checked' in html
    client.cookies.set("interlinear", "1")
    assert 'class="reader interlinear-on"' in client.get(f"/texts/{text_id}/read").text
    html = client.get(f"/texts/{text_id}/read?interlinear=false").text
    assert 'class="reader"' in html
    client.cookies.set("interlinear", "0")  # reader.js, the switch turned off
    html = client.get(f"/texts/{text_id}/read").text
    assert 'class="reader"' in html
    assert 'id="interlinear-toggle">' in html
    html = client.get(f"/texts/{text_id}/read?interlinear=true").text
    assert 'class="reader interlinear-on"' in html


def test_a_book_without_study_data_turns_interlinear_off(client, web_db, text_id):
    _study(web_db, text_id, 1, 2)  # Book 1 in part, Book 2 none
    client.cookies.set("interlinear", "1")
    book1 = client.get(f"/texts/{text_id}/read", params={"at": "1.1"}).text
    assert 'id="interlinear-toggle" checked' in book1
    assert "No glosses yet" not in book1
    # the glosses are the study model's: its tag follows Interlinear
    control = book1[book1.index('id="interlinear-control"') :].split("</div>")[0]
    assert f"Interlinear {tag('AI')}\n" in control
    book2 = client.get(f"/texts/{text_id}/read", params={"at": "2.1"}).text
    control = book2[book2.index('id="interlinear-control"') :].split("</div>")[0]
    assert 'class="src' not in control  # no glosses, no tag
    assert "is-unavailable" in book2[: book2.index('id="interlinear-control"')][-60:]
    assert (
        '<input type="checkbox" id="interlinear-toggle" disabled '
        'aria-describedby="interlinear-note">' in control
    )
    assert 'id="interlinear-note">No glosses yet</span>' in control
    assert "checked" not in control
    # the cookie keeps the choice for the books that have glosses
    assert 'class="reader interlinear-on"' in book2


def test_pane_links_bring_the_books_interlinear_control(client, text_id):
    html = client.get(f"/texts/{text_id}/read").text
    assert 'id="interlinear-control"' in html
    oob = 'hx-select-oob="#book-nav,#goto,#interlinear-control"'
    assert oob in html  # the book grid, pager and search form
    assert 'hx-select-oob="#book-nav,#goto"' not in html


def test_reader_shows_goto_error(client, text_id):
    html = client.get(f"/texts/{text_id}/read?goto_error=Iliad+has+no+9.9").text
    assert 'role="alert">Iliad has no 9.9</span>' in html


# ---------------------------------------------------------------------------
# Go to
# ---------------------------------------------------------------------------


def _goto(client, **params):
    resp = client.get("/goto", params=params, follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"]


def test_goto_title_and_reference(client, text_id):
    assert _goto(client, q="Iliad 1.3") == f"/texts/{text_id}/read?at=1.3"


def test_goto_title_prefix_is_case_insensitive(client, text_id):
    assert _goto(client, q="ili 1.3") == f"/texts/{text_id}/read?at=1.3"


def test_goto_reference_in_current_text(client, text_id):
    assert _goto(client, q="1.44", text_id=text_id) == f"/texts/{text_id}/read?at=1.44"


def test_goto_bare_number_is_a_book(client, text_id):
    assert _goto(client, q="Iliad 2") == f"/texts/{text_id}/read?at=2.1"


def test_goto_title_only_resumes(client, text_id):
    assert _goto(client, q="Iliad") == f"/texts/{text_id}/read"


def test_goto_unknown_title(client, text_id):
    location = _goto(client, q="Aeneid 1.1", text_id=text_id, at="1.5")
    assert location.startswith(f"/texts/{text_id}/read?goto_error=No+text+called")
    assert location.endswith("&at=1.5")


def test_goto_unknown_reference(client, text_id):
    location = _goto(client, q="9.9", text_id=text_id)
    assert "goto_error=Iliad+has+no+9.9" in location


def test_goto_without_context_goes_to_library(client):
    assert _goto(client, q="1.1") == "/library"


# ---------------------------------------------------------------------------
# Passage box
# ---------------------------------------------------------------------------


def _passage_box(client, tid, lines=None, **params):
    if lines:
        params["lines"] = lines
    resp = client.get(f"/texts/{tid}/passage", params=params)
    assert resp.status_code == 200
    return resp.text


def test_passage_box_without_selection(client, text_id):
    assert "Select lines to study them" in _passage_box(client, text_id)


def test_passage_box_bad_range(client, text_id):
    assert "Invalid line range" in _passage_box(client, text_id, "abc")


def test_passage_box_range_without_lines(client, text_id):
    assert "No lines found" in _passage_box(client, text_id, "200-210")


def test_a_selection_longer_than_any_page_is_refused(client, web_db):
    count = MAX_SELECTION_LINES + 1
    tid = insert_text(web_db, "Long", "Homer", "long.txt", "/d/long.txt", count)
    insert_lines(
        web_db,
        tid,
        ["μῆνιν ἄειδε"] * count,
        [f"1.{n}" for n in range(1, count + 1)],
    )
    refused = f"Select at most {MAX_SELECTION_LINES:,} lines at a time."
    whole = f"1.1-1.{count}"
    # Either way round, by reference or by line number
    for lines in (whole, f"1.{count}-1.1", f"1-{count}"):
        html = _passage_box(client, tid, lines)
        assert refused in html and 'aria-label="Passage"' in html
    resp = client.get(f"/texts/{tid}/read", params={"lines": whole})
    assert resp.status_code == 400 and refused in resp.text
    # A whole page's worth is still a selection
    html = _passage_box(client, tid, f"1.2-1.{count}")
    assert refused not in html and f'aria-label="Passage 1.2–1.{count}"' in html
    resp = client.get(f"/texts/{tid}/read", params={"lines": f"1.1-1.{count - 1}"})
    assert resp.status_code == 200


def test_passage_box_without_study_data(client, text_id):
    html = _passage_box(client, text_id, "3-4")
    assert "passage-box is-none" in html
    assert "Translations of the Iliad are not yet available" in html
    assert "No translation is saved" not in html
    assert "Analyze" not in html and "pb-ask" not in html


def test_passage_box_in_a_book_without_study_data_names_the_book(
    client, web_db, text_id
):
    _study(web_db, text_id, 1, 2)
    html = _passage_box(client, text_id, "2.1-2.2")
    assert "Translations of Book 2 are not yet available" in html
    # without treebank words the box has no tabs, so no Literal tab to say it
    assert "has-tabs" not in html
    page = client.get(f"/texts/{text_id}/read", params={"lines": "2.1-2.2"}).text
    literal = page[page.index('id="reader-tab-literal"') :].split("</button>")[0]
    assert "disabled" in literal


def test_passage_box_single_line_wording(client, web_db, text_id):
    _study(web_db, text_id, 1, 2)  # Book 1 has study data, but not for 1.5
    html = _passage_box(client, text_id, "1.5-1.5")
    assert "No translation is saved for this line yet" in html
    assert "not yet available" not in html
    page = client.get(f"/texts/{text_id}/read", params={"lines": "1.5-1.5"}).text
    literal = page[page.index('id="reader-tab-literal"') :].split("</button>")[0]
    assert "disabled" in literal


def test_passage_box_study_data_covers_only_whole_selections(client, web_db, text_id):
    _study(web_db, text_id, 1, 2)
    assert "passage-box is-study" in _passage_box(client, text_id, "1-2")
    # line 3 has none, so the selection has no translation to show
    assert "passage-box is-none" in _passage_box(client, text_id, "2-3")


def test_passage_box_ignores_a_model_parameter(client, web_db, text_id):
    _study(web_db, text_id, 1, 2)
    html = _passage_box(client, text_id, "1-2", model="claude-sonnet-5-5")
    assert f"1.1–1.2</span>&nbsp;{tag('AI')}</h3>" in html  # Opus's
    assert "Sonnet" not in html


# ---------------------------------------------------------------------------
# Word strip
# ---------------------------------------------------------------------------


def _word(client, tid, line, i, **params):
    return client.get(f"/texts/{tid}/word", params={"line": line, "i": i, **params})


def test_word_with_nothing_saved(client, web_db):
    tid = insert_text(web_db, "Achilleid", None, "t.txt", "/d/t.txt", 1)
    insert_lines(web_db, tid, ["Ἀχιλλεύς."], ["1.1"])
    html = _word(client, tid, 1, 0).text
    assert '<div class="ws-greek" lang="grc">Ἀχιλλεύς</div>' in html  # no full stop
    # in place of a gloss and note: the text has none yet
    assert "ws-meaning" not in html
    assert "Glosses and notes for the Achilleid are not yet available." in html
    assert "No gloss or note is saved" not in html
    assert "/verbs" not in html


def test_word_without_study_data_in_a_book_with_some(client, web_db, text_id):
    _study(web_db, text_id, 1, 2)
    html = _word(client, text_id, 3, 0).text
    assert '<div class="ws-meaning">—</div>' in html
    assert "No gloss or note is saved for this word yet." in html
    other_book = _word(client, text_id, 46, 0).text  # 2.1
    assert "Glosses and notes for Book 2 are not yet available." in other_book


def test_word_out_of_range(client, text_id):
    assert _word(client, text_id, 1, 9).status_code == 404
    assert _word(client, 9999, 1, 0).status_code == 404
