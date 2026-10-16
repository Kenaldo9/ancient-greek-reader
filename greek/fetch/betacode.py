"""
Beta Code to Unicode Greek, for the Perseus LSJ, where all Greek (keys,
headwords, quotations) is written in Beta Code: "a)/lgos" → "ἄλγος",
"*)axilleu/s" → "Ἀχιλλεύς".

Letters are lowercase ASCII (uppercase is read the same way); "*" marks a
capital, with its breathing and accent written between "*" and the letter.
Perseus sometimes puts them before the "*" or after the letter instead, and
both are accepted. ^ and _ (short and long vowel) and ? (underdot) are
dropped; anything else that is not Beta Code passes through unchanged.
"""

import unicodedata

_LETTERS = dict(zip("abgdezhqiklmncoprstufxywv", "αβγδεζηθικλμνξοπρστυφχψωϝ"))
_MARKS = {
    ")": "̓",  # smooth breathing
    "(": "̔",  # rough breathing
    "/": "́",  # acute
    "\\": "̀",  # grave
    "=": "͂",  # circumflex
    "+": "̈",  # diaeresis
    "|": "ͅ",  # iota subscript
}
# Breathing and diaeresis go before the accent, iota subscript last, so that
# NFC composes them into one character (ΐ is ι + diaeresis + acute).
_MARK_ORDER = {")": 0, "(": 0, "+": 1, "/": 2, "\\": 2, "=": 2, "|": 3}
_DROPPED = "^_?"
_PUNCTUATION = {"'": "ʼ", ":": "·"}
_SIGMAS = {"1": "σ", "2": "ς", "3": "ϲ"}


def _is_letter(ch: str) -> bool:
    return ch.lower() in _LETTERS


def _marks(text: str, i: int):
    """The diacritic codes starting at text[i], and the index after them."""
    j = i
    while j < len(text) and text[j] in _MARKS:
        j += 1
    return list(text[i:j]), j


def _with_marks(letter: str, marks) -> str:
    return letter + "".join(_MARKS[m] for m in sorted(marks, key=_MARK_ORDER.get))


def beta_to_unicode(text: str) -> str:
    """Convert Beta Code to NFC Unicode Greek, with final sigma where a word ends."""
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        marks, j = _marks(text, i)
        capital = j < n and text[j] == "*"
        if ch == "*" or (marks and capital):
            # a capital: *[marks]letter[marks], or marks written before the *
            before = marks
            more, k = _marks(text, j + 1 if capital else i + 1)
            if k < n and _is_letter(text[k]):
                after, end = _marks(text, k + 1)
                letter = _LETTERS[text[k].lower()].upper()
                out.append(_with_marks(letter, before + more + after))
                i = end
                continue
        if _is_letter(ch):
            letter = _LETTERS[ch.lower()]
            after, end = _marks(text, i + 1)
            if letter == "σ":
                if end < n and text[end] in _SIGMAS:
                    letter, end = _SIGMAS[text[end]], end + 1
                elif not (end < n and (_is_letter(text[end]) or text[end] in "'-")):
                    letter = "ς"
            out.append(_with_marks(letter, after))
            i = end
            continue
        if ch not in _DROPPED:
            out.append(_PUNCTUATION.get(ch, ch))
        i += 1
    return unicodedata.normalize("NFC", "".join(out))
