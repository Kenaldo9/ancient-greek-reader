"""
Accent-insensitive search. SQLite's unicode61 tokenizer folds case but not
Greek accents, breathings or iota subscripts, so each line also stores a
`plain` copy (see plain()) that the full-text index is built over, and search
queries are normalized the same way. Shared by the web reader and `greek search`.
"""

import re
import unicodedata
from typing import List, Optional, Tuple

# Elision marks and apostrophes, dropped so "μυρίʼ" is searchable as "μυρι"
_APOSTROPHES = "ʼ’‘'"

# A quoted phrase (closing quote optional) or a single word
_TERM = re.compile(r'"([^"]*)"?|(\S+)')


def plain(text: Optional[str]) -> Optional[str]:
    """
    Text without accents, breathings, iota subscripts, diaeresis or elision
    marks, in lowercase with final sigma as σ: 'Ἀχιλῆος' → 'αχιληοσ'.
    """
    if text is None:
        return None
    decomposed = unicodedata.normalize("NFD", text)
    kept = "".join(
        c
        for c in decomposed
        if c not in _APOSTROPHES and unicodedata.category(c) not in ("Mn", "Sk")
    )
    return kept.casefold()  # also turns ς into σ


def citation_form(word: str) -> str:
    """A word with a grave accent made acute, as it is cited: δὲ → δέ."""
    decomposed = unicodedata.normalize("NFD", word).replace("\u0300", "\u0301")
    return unicodedata.normalize("NFC", decomposed)


def _words(text: str) -> List[str]:
    """The searchable words of a string: plain, split on anything but letters and digits."""
    return re.findall(r"[^\W_]+", plain(text))


def parse_query(query: Optional[str]) -> List[Tuple[List[str], bool]]:
    """
    A search as (words, prefix) terms. A quoted phrase is one term of several
    words; a word ending in * also matches longer words that start with it.
    Punctuation is ignored, so no input can break the full-text query.
    """
    terms = []
    for phrase, token in _TERM.findall(query or ""):
        if phrase:
            words = _words(phrase)
            if words:
                terms.append((words, False))
            continue
        words = _words(token)
        terms.extend(([w], False) for w in words)
        if words and token.endswith("*"):
            terms[-1] = (terms[-1][0], True)
    return terms


def fts_query(query: Optional[str]) -> Optional[str]:
    """An FTS5 MATCH expression for a search (all terms must match), or None if it has no words."""
    parts = [
        '"' + " ".join(words) + '"' + ("*" if prefix else "")
        for words, prefix in parse_query(query)
    ]
    return " ".join(parts) or None


def highlight(content: str, query: Optional[str]) -> List[Tuple[str, bool]]:
    """
    `content` split into (text, matched) pieces. Each word matching a search
    term is a piece of its own, without its surrounding punctuation; the
    pieces join back into `content`.
    """
    exact, prefixes = set(), []
    for words, prefix in parse_query(query):
        exact.update(words[:-1] if prefix else words)
        if prefix:
            prefixes.append(words[-1])

    def matches(token: str) -> bool:
        return any(
            w in exact or any(w.startswith(p) for p in prefixes) for w in _words(token)
        )

    pieces: List[Tuple[str, bool]] = []

    def add(text: str, matched: bool):
        if not text:
            return
        if pieces and not matched and not pieces[-1][1]:
            pieces[-1] = (pieces[-1][0] + text, False)
        else:
            pieces.append((text, matched))

    for token in re.split(r"(\s+)", content):
        if token.strip() and matches(token):
            lead, core, trail = re.match(r"^(\W*)(.*?)(\W*)$", token, re.S).groups()
            add(lead, False)
            add(core, True)
            add(trail, False)
        else:
            add(token, False)
    return pieces


def excerpt(pieces: List[Tuple[str, bool]], width: int = 280) -> List[Tuple[str, bool]]:
    """
    Highlight pieces cut down to about `width` characters around the first
    match, at word boundaries, with "…" where text was left out. Short text
    is returned as it is.
    """
    text = "".join(t for t, _ in pieces)
    if len(text) <= width:
        return pieces
    first, first_end, pos = 0, 0, 0
    for t, matched in pieces:
        if matched:
            first, first_end = pos, pos + len(t)
            break
        pos += len(t)

    start = max(0, min(first - width // 3, len(text) - width))
    end = start + width
    if start > 0:
        space = text.find(" ", start, first)
        start = space + 1 if space >= 0 else start
    if end < len(text):
        space = text.rfind(" ", first_end, end)
        end = space if space >= 0 else end

    out: List[Tuple[str, bool]] = [("… ", False)] if start > 0 else []
    pos = 0
    for t, matched in pieces:
        lo, hi = max(pos, start), min(pos + len(t), end)
        if lo < hi:
            out.append((t[lo - pos : hi - pos], matched))
        pos += len(t)
    if end < len(text):
        out.append((" …", False))
    return out
