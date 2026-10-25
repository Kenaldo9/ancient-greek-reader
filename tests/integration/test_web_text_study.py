"""What each text has: book_study (each book's study data, counted as `greek
study coverage` counts it), the Library's labels and book chips, and the
About page with its sources and licences.

The fixture is the Iliad treebank excerpt: Book 1 (lines 1–5, one unit of 24
words) and Book 2 (lines 6–7, one unit of 7 words). Study data is saved
directly, as a collected batch leaves it; no model calls are made.
"""

import re

import pytest

from greek.api.study import MODEL
from greek.cli.study_cmd import _select_units, _words_per_line
from greek.db.repos.lines import insert_lines
from greek.db.repos.parses import list_word_parses, save_tb_source
from greek.db.repos.study import book_study, covered_units
from greek.db.repos.texts import insert_text
from greek.models import BookStudy, TreebankSource
from greek.web.reader_data import TextStudy
from tests.integration.test_notes_batch import _save_study
from tests.integration.test_word_details import _import_excerpt


@pytest.fixture
def iliad(web_db):
    return _import_excerpt(web_db)


def _states(db, text_id):
    return [(b.number, b.state) for b in book_study(db, text_id, MODEL)]


# --- book_study -------------------------------------------------------------------


def test_a_text_without_study_data(iliad):
    db, text_id = iliad
    assert book_study(db, text_id, MODEL) == [
        BookStudy("1", 1, 5, 5),
        BookStudy("2", 6, 7, 2),
    ]
    assert _states(db, text_id) == [("1", "none"), ("2", "none")]


def test_a_book_with_its_units_saved_is_translated(iliad):
    db, text_id = iliad
    _save_study(db, text_id, [(1, 5)])
    book1, book2 = book_study(db, text_id, MODEL)
    # lines, literal lines, lines in a saved unit, glossed words (as coverage: 24)
    assert (book1.lines, book1.literal, book1.translated, book1.glossed) == (
        5,
        5,
        5,
        24,
    )
    assert book1.state == "translated" and book2.state == "none"


def test_another_models_study_data_does_not_count(iliad):
    db, text_id = iliad
    _save_study(db, text_id)
    assert _states(db, text_id) == [("1", "translated"), ("2", "translated")]
    assert _states_of_model(db, text_id, "claude-sonnet-5-5") == [
        ("1", "none"),
        ("2", "none"),
    ]


def _states_of_model(db, text_id, model):
    return [(b.number, b.state) for b in book_study(db, text_id, model)]


def test_a_unit_without_a_literal_line_leaves_its_book_partly_translated(iliad):
    db, text_id = iliad
    _save_study(db, text_id)
    db.execute("DELETE FROM line_translations WHERE line_number=7")
    assert _states(db, text_id) == [("1", "translated"), ("2", "partly")]


def test_glosses_alone_make_a_book_partly_translated(iliad):
    db, text_id = iliad
    _save_study(db, text_id)
    db.execute("DELETE FROM translation_units WHERE first_line=6")
    db.execute("DELETE FROM line_translations WHERE line_number >= 6")
    book2 = book_study(db, text_id, MODEL)[1]
    assert (book2.translated, book2.literal, book2.glossed) == (0, 0, 7)
    assert book2.state == "partly"


def test_a_text_without_books_counts_as_one(web_db):
    text_id = insert_text(web_db, "Fragment", None, "f.txt", "/d/f.txt", 2)
    insert_lines(web_db, text_id, ["ἄνδρα μοι", "ἔννεπε"])
    assert book_study(web_db, text_id, MODEL) == [BookStudy("", 1, 2, 2)]


@pytest.mark.parametrize(
    "saved, deleted",
    [
        ([], None),
        ([(1, 5)], None),
        ([(6, 7)], None),
        ([(1, 5), (6, 7)], None),
        ([(1, 5), (6, 7)], "DELETE FROM line_translations WHERE line_number=2"),
    ],
)
def test_book_study_agrees_with_greek_study_coverage(iliad, saved, deleted):
    """A book is translated when coverage counts every one of its units."""
    db, text_id = iliad
    _save_study(db, text_id, saved)
    if deleted:
        db.execute(deleted)
    chosen = _select_units(db, text_id, None, (), None)
    lines = list(chosen.by_number.values())
    counts = _words_per_line(lines, list_word_parses(db, text_id))
    covered = covered_units(db, chosen.units, counts, MODEL)
    for book in book_study(db, text_id, MODEL):
        units = [
            u for u in chosen.units if book.line_start <= u.first_line <= book.line_end
        ]
        complete = all((u.first_line, u.last_line) in covered for u in units)
        assert (book.state == "translated") == complete, book
        words = sum(counts[n] for n in range(book.line_start, book.line_end + 1))
        if complete:
            assert book.glossed == words


# --- TextStudy: labels --------------------------------------------------------------


def _text(*states, treebank=True, numbers=None):
    """A TextStudy whose books (of 10 lines) have these states."""
    books = []
    for i, state in enumerate(states):
        number = numbers[i] if numbers else str(i + 1)
        first = 10 * i + 1
        done = {"translated": 10, "partly": 3, "none": 0}[state]
        books.append(BookStudy(number, first, first + 9, 10, done, done, 7 * done))
    return TextStudy("the Odyssey", books, treebank)


@pytest.mark.parametrize(
    "states, label",
    [
        (["translated"] * 3, "Complete"),
        (["none"] * 3, "Grammar and dictionary · translations coming"),
        (
            ["translated", "translated", "translated", "none"],
            "Books 1–3 with translations",
        ),
        (["translated", "none", "translated"], "Books 1, 3 with translations"),
        (["translated", "partly", "none"], "Book 1 with translations · Book 2 in part"),
        (["none", "partly", "partly"], "Books 2–3 in part"),
    ],
)
def test_a_texts_label(states, label):
    assert _text(*states).label == label


def test_a_text_without_a_treebank_has_the_greek_text_only():
    assert _text("none", treebank=False).label == "Greek text only"
    assert _text("partly", treebank=False, numbers=[""]).label == "Translations in part"


def test_the_key_lists_the_states_a_text_has_in_order():
    assert _text("none", "translated").states == ["translated", "none"]
    assert _text("partly", "partly").states == ["partly"]


def test_what_a_note_says_is_missing():
    # the whole text has none: the note names the text
    assert _text("none", "none").missing(15) == "the Odyssey"
    # one book has none: the note names that book
    text = _text("translated", "none", "partly")
    assert text.missing(5) is None
    assert text.missing(15) == "Book 2"
    assert text.missing(25) is None  # a book in part says it per line


# --- the Library -------------------------------------------------------------------


def _card(html, title):
    start = html.index(f">{title}</h2>")
    return html[start : html.index("</article>", start)]


def test_library_labels_a_text_without_study_data(client, iliad):
    html = client.get("/library").text
    card = _card(html, "Iliad")
    assert (
        '<p class="text-status is-none">Grammar and dictionary · translations coming</p>'
        in card
    )
    assert card.count('<a class="is-none" href=') == 2
    assert 'aria-label="Book 1, 1.1–1.5, grammar and dictionary"' in card
    key = card[card.index('<ul class="book-key"') :].split("</ul>")[0]
    assert re.findall(r"</span>([^<]+)</li>", key) == ["Grammar and dictionary"]


def test_library_marks_the_books_with_translations(client, iliad):
    db, text_id = iliad
    _save_study(db, text_id, [(1, 5)])
    card = _card(client.get("/library").text, "Iliad")
    assert '<p class="text-status is-partly">Book 1 with translations</p>' in card
    assert (
        f'<a class="is-translated" href="/texts/{text_id}/read?at=1.1" '
        'aria-label="Book 1, 1.1–1.5, with translations, glosses and notes">1</a>'
        in card
    )
    assert f'<a class="is-none" href="/texts/{text_id}/read?at=2.1"' in card
    key = card[card.index('<ul class="book-key"') :].split("</ul>")[0]
    assert re.findall(r"</span>([^<]+)</li>", key) == [
        "With translations, glosses and notes",
        "Grammar and dictionary",
    ]


def test_library_calls_a_text_with_every_book_translated_complete(client, iliad):
    db, text_id = iliad
    _save_study(db, text_id)
    card = _card(client.get("/library").text, "Iliad")
    assert '<p class="text-status is-translated">Complete</p>' in card
    assert card.count('<a class="is-translated"') == 2


def test_library_without_a_treebank_has_the_greek_text_only(client, db_text):
    card = _card(client.get("/library").text, "Iliad")
    assert '<p class="text-status is-none">Greek text only</p>' in card
    assert 'aria-label="Book 1, 1.1–1.5, Greek text"' in card


# --- About -----------------------------------------------------------------------


def test_about_names_each_source_and_licence(client):
    html = client.get("/about").text
    for source, licence in [
        ("canonical-greekLit", "CC BY-SA 4.0"),
        ("Ancient Greek Dependency Treebank 2.1", "CC BY-SA 3.0"),
        ("GLAUx corpus", "CC BY-SA 4.0"),
        ("PROIEL treebank", "CC BY-NC-SA 3.0"),
        ("Vanessa Gorman's treebanks", "CC BY-NC-SA 4.0"),
        ("A Greek-English Lexicon", "CC BY-SA 4.0"),
        ("IBM Plex Mono", "SIL Open Font License 1.1"),
    ]:
        assert source in html and licence in html
    assert "written by Claude Opus 5.5, Anthropic's model" in html
    assert "<strong>They can be wrong.</strong>" in html
    assert 'href="https://github.com/Kenaldo9/ancient-greek-reader"' in html
    assert 'href="https://github.com/Kenaldo9/ancient-greek-reader/releases"' in html
    assert '<a href="/about" aria-current="page">About</a>' in html


def test_about_says_where_to_report_a_mistake(client):
    html = client.get("/about").text
    assert 'href="https://github.com/Kenaldo9/ancient-greek-reader/issues"' in html
    assert '<a href="mailto:greek@kenaldo.dev">greek@kenaldo.dev</a>' in html
    assert "no promise of a reply" in html


def test_about_says_what_each_text_has(client, iliad, web_db):
    db, text_id = iliad
    _save_study(db, text_id)
    hdt = insert_text(web_db, "Histories", "Herodotus", "hdt.txt", "/d/hdt.txt", 1)
    insert_lines(web_db, hdt, ["Ἡροδότου"], ["1.0.0"])
    save_tb_source(
        web_db,
        TreebankSource(hdt, "herodotus", "glaux", "glaux-0016-001.xml", "CC BY-SA 4.0"),
    )
    web_db.execute(
        "INSERT INTO tb_sentences(text_id, sentence_id, first_line, last_line, "
        "annotation) VALUES (?,1,1,1,'manual'), (?,2,1,1,'auto'), (?,3,1,1,'auto')",
        (hdt, hdt, hdt),
    )
    html = client.get("/about").text
    rows = {
        re.search(r'<th scope="row">([^<]+)', row)[1]: row
        for row in html.split("<tr>")[2:]
    }
    assert "The Perseus treebank, by hand" in rows["Iliad"]
    assert "<td>Every book</td>" in rows["Iliad"]
    assert (
        "GLAUx: 33% of the sentences by hand, from the PROIEL treebank; the rest "
        "by GLAUx's parser" in rows["Histories"]
    )
    assert "<td>Not yet</td>" in rows["Histories"]


@pytest.mark.parametrize("path", ["/library", "/about", "/texts/{text_id}/read"])
def test_every_page_links_about(client, db_text, path):
    _, text_id = db_text
    html = client.get(path.format(text_id=text_id)).text
    nav = html.split('<nav class="main-nav" aria-label="Main">')[1].split("</nav>")[0]
    assert 'href="/about"' in nav


def test_the_readers_drawer_links_about(client, db_text):
    _, text_id = db_text
    html = client.get(f"/texts/{text_id}/read").text
    drawer = html.split('<div class="drawer-pages">')[1].split("</div>")[0]
    assert '<a class="nav-item" href="/about">About</a>' in drawer
