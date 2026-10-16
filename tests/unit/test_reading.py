from greek.models import Book
from greek.web.reading import (
    find_page,
    parse_goto,
    split_parts,
    tokens,
)

# Book 1 = lines 1–95, book 2 = lines 96–100
BOOKS = [
    Book("1", "1.1", "1.95", 1, 95, 95),
    Book("2", "2.1", "2.5", 96, 100, 5),
]


# --- find_page ---


def _whole(lo, hi):
    return [lo]


def _parts(by_span):
    """parts_of for find_page: the given part starts per span, else one part."""
    return lambda lo, hi: by_span.get((lo, hi), [lo])


def test_find_page_is_the_whole_book():
    page = find_page(BOOKS, 57, 100, _whole)
    assert (page.start, page.end, page.book.number) == (1, 95, "1")
    assert (page.part, page.parts, page.label) == (1, 1, "Book 1")
    assert page.prev is None
    assert (page.next.start, page.next.label) == (96, "Book 2")
    assert page.next_start == 96


def test_find_page_last_book_has_no_next():
    page = find_page(BOOKS, 96, 100, _whole)
    assert (page.start, page.end, page.book.number) == (96, 100, "2")
    assert (page.prev.start, page.prev.label) == (1, "Book 1")
    assert page.next is None


def test_find_page_in_a_split_book():
    page = find_page(BOOKS, 57, 100, _parts({(1, 95): [1, 41, 81]}))
    assert (page.start, page.end, page.label) == (41, 80, "Book 1, part 2 of 3")
    assert (page.prev.start, page.prev.label) == (1, "Book 1, part 1 of 3")
    assert (page.next.start, page.next.label) == (81, "Book 1, part 3 of 3")


def test_find_page_last_part_continues_into_next_book():
    page = find_page(BOOKS, 90, 100, _parts({(1, 95): [1, 41, 81]}))
    assert (page.start, page.end) == (81, 95)  # never spans two books
    assert (page.next.start, page.next.label) == (96, "Book 2")


def test_find_page_book_start_goes_back_to_last_part_of_previous_book():
    page = find_page(BOOKS, 96, 100, _parts({(1, 95): [1, 41, 81]}))
    assert (page.prev.start, page.prev.label) == (81, "Book 1, part 3 of 3")


def test_find_page_next_book_counts_its_parts():
    page = find_page(BOOKS, 10, 100, _parts({(96, 100): [96, 98]}))
    assert page.next.label == "Book 2, part 1 of 2"


def test_find_page_without_books_is_the_whole_text():
    page = find_page([], 45, 100, _whole)
    assert (page.start, page.end, page.book) == (1, 100, None)
    assert (page.prev, page.next) == (None, None)


def test_find_page_without_books_in_parts():
    page = find_page([], 45, 100, _parts({(1, 100): [1, 51]}))
    assert (page.start, page.end, page.label) == (1, 50, "Part 1 of 2")
    assert page.next.label == "Part 2 of 2"


def test_find_page_past_the_end_is_empty():
    page = find_page(BOOKS, 500, 100, _whole)
    assert page.book is None
    assert (page.prev_start, page.next_start) == (None, None)


# --- split_parts ---


def test_split_parts_a_book_within_the_budget_is_one_part():
    rows = [(n, f"5.{n}", 40) for n in range(1, 910)]
    assert split_parts(rows) == [1]


def test_split_parts_poetry_splits_at_any_line_into_equal_shares():
    rows = [(n, f"5.{n}", 1000) for n in range(1, 101)]  # 100,000 characters
    assert split_parts(rows) == [1, 34, 68]


def test_split_parts_prose_parts_start_at_chapters():
    # 20 chapters of 5 sections, 1,000 characters each: 3 parts of the budget
    rows = [(n, f"1.{(n - 1) // 5 + 1}.{(n - 1) % 5}", 1000) for n in range(1, 101)]
    starts = split_parts(rows)
    assert starts == [1, 36, 66]
    assert [rows[n - 1][1] for n in starts] == ["1.1.0", "1.8.0", "1.14.0"]


def test_split_parts_one_long_chapter_cannot_be_split():
    rows = [(n, f"1.1.{n}", 1000) for n in range(1, 101)]
    assert split_parts(rows) == [1]


def test_split_parts_no_rows():
    assert split_parts([]) == []


# --- tokens ---


def test_tokens_split_on_whitespace():
    assert tokens("μῆνιν ἄειδε  θεὰ\n") == ["μῆνιν", "ἄειδε", "θεὰ"]


# --- parse_goto ---


def test_parse_goto_title_and_reference():
    assert parse_goto("Iliad 1.42") == ("Iliad", "1.42")


def test_parse_goto_reference_only():
    assert parse_goto(" 1.1.0 ") == ("", "1.1.0")


def test_parse_goto_title_only():
    assert parse_goto("History of the Peloponnesian War") == (
        "History of the Peloponnesian War",
        None,
    )


def test_parse_goto_book_number():
    assert parse_goto("Odyssey 5") == ("Odyssey", "5")
