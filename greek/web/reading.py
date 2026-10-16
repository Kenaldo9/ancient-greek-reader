"""
Reader helpers with no DB or web dependencies: book pages, word tokens and
Go to queries.
"""

import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from greek.models import Book

# A page is a whole book. A longer book (Herodotus 1 has 181,137 characters;
# the Iliad's longest book 38,806) is split into parts at chapter starts.
PAGE_CHARS = 40_000
# Old ?page= links counted pages of 40 lines
LEGACY_PAGE_SIZE = 40


@dataclass
class PageRef:
    start: int  # first line_number of the page
    book: Optional[Book]
    part: int  # 1-based
    parts: int  # parts of its book (1 when the book is one page)

    @property
    def label(self) -> str:
        """'Book 5', 'Book 1, part 2 of 5', or 'Part 2 of 3' in a text without books."""
        if self.book is None:
            return f"Part {self.part} of {self.parts}"
        if self.parts == 1:
            return f"Book {self.book.number}"
        return f"Book {self.book.number}, part {self.part} of {self.parts}"


@dataclass
class Page(PageRef):
    end: int  # last line_number on the page
    prev: Optional[PageRef]
    next: Optional[PageRef]

    @property
    def prev_start(self) -> Optional[int]:
        return self.prev.start if self.prev else None

    @property
    def next_start(self) -> Optional[int]:
        return self.next.start if self.next else None


def _chapter(reference: Optional[str]) -> Optional[str]:
    """'1.5.0' → '5'; None for references without chapters ('5.412')."""
    levels = (reference or "").split(".")
    return levels[1] if len(levels) >= 3 else None


def split_parts(
    rows: Sequence[Tuple[int, Optional[str], int]], budget: int = PAGE_CHARS
) -> List[int]:
    """
    The first line_number of each part of a span, from its rows'
    (line_number, reference, length) in order: one part when the span fits
    the budget, else as many parts as it needs, each starting at the chapter
    start nearest an equal share of the characters. Where references have no
    chapters, any line can start a part.
    """
    if not rows:
        return []
    total = sum(length for _, _, length in rows)
    count = -(-total // budget)
    if count <= 1:
        return [rows[0][0]]
    offsets, at = [], 0
    for _, _, length in rows:
        offsets.append(at)
        at += length
    chapters = [_chapter(reference) for _, reference, _ in rows]
    candidates = [
        i
        for i in range(1, len(rows))
        if chapters[i] is None or chapters[i] != chapters[i - 1]
    ]
    starts = [0]
    for k in range(1, count):
        later = [i for i in candidates if i > starts[-1]]
        if not later:
            break
        share = total * k / count
        starts.append(min(later, key=lambda i: abs(offsets[i] - share)))
    return [rows[i][0] for i in starts]


def find_page(
    books: Sequence[Book],
    line_number: int,
    line_count: int,
    parts_of: Callable[[int, int], List[int]],
) -> Page:
    """
    The page containing `line_number`: its book, or the part of a long book,
    with the pages before and after it. A text without books is one span from
    line 1. `parts_of(lo, hi)` gives the first line of each part of the span
    [lo, hi] (see split_parts).
    """
    spans = [(b.line_start, b.line_end, b) for b in books] or [(1, line_count, None)]
    index = next(
        (i for i, (lo, hi, _) in enumerate(spans) if lo <= line_number <= hi),
        None,
    )
    if index is None:
        # Past the end (or in a gap between books): an empty page there
        return Page(line_number, None, 1, 1, line_number, None, None)

    def starts_of(i: int) -> List[int]:
        lo, hi, _ = spans[i]
        return parts_of(lo, hi) or [lo]

    lo, hi, book = spans[index]
    starts = starts_of(index)
    part = max((i for i, s in enumerate(starts) if s <= line_number), default=0)
    end = starts[part + 1] - 1 if part + 1 < len(starts) else hi

    if part > 0:
        prev = PageRef(starts[part - 1], book, part, len(starts))
    elif index > 0:
        before = starts_of(index - 1)
        prev = PageRef(before[-1], spans[index - 1][2], len(before), len(before))
    else:
        prev = None

    if part + 1 < len(starts):
        nxt = PageRef(starts[part + 1], book, part + 2, len(starts))
    elif index + 1 < len(spans):
        after = starts_of(index + 1)
        nxt = PageRef(after[0], spans[index + 1][2], 1, len(after))
    else:
        nxt = None

    return Page(starts[part], book, part + 1, len(starts), end, prev, nxt)


def tokens(content: str) -> List[str]:
    return content.split()


_GOTO = re.compile(r"^(?P<title>.*?)\s*(?P<ref>\d+(?:\.\d+)*)?$")


def parse_goto(query: str) -> Tuple[str, Optional[str]]:
    """'Iliad 1.42' → ('Iliad', '1.42'); '1.42' → ('', '1.42'); 'Odyssey' → ('Odyssey', None)."""
    m = _GOTO.match(query.strip())
    return m["title"].strip(), m["ref"]
