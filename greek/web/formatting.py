"""Small display helpers, registered as Jinja filters in templates_config.py."""

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from markupsafe import Markup, escape

_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _local(timestamp: str) -> Optional[datetime]:
    """A SQLite UTC timestamp ('2026-04-08 22:06:07') in local time; None if unparseable."""
    try:
        dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone()


def format_date(timestamp: Optional[str]) -> str:
    """'2026-04-08 22:06:07' (SQLite UTC) → '9 Apr 2026' in local time."""
    if not timestamp:
        return ""
    local = _local(timestamp)
    if local is None:
        return timestamp
    return f"{local.day} {_MONTHS[local.month - 1]} {local.year}"


def format_time(timestamp: Optional[str]) -> str:
    """'2026-04-08 14:51:07' (SQLite UTC) → '17:51' in local time."""
    if not timestamp:
        return ""
    local = _local(timestamp)
    return local.strftime("%H:%M") if local else timestamp


def count_noun(n: int, noun: str) -> str:
    """count_noun(15687, 'line') → '15,687 lines'; count_noun(1, 'text') → '1 text'."""
    return f"{n:,} {noun if n == 1 else noun + 's'}"


def ref_range(start: Optional[str], end: Optional[str]) -> str:
    """'1.1', '1.7' → '1.1–1.7'; a single reference is shown once."""
    if not end or start == end:
        return start or ""
    return f"{start}–{end}"


def number_ranges(numbers: List[str]) -> str:
    """Books in order as runs: ['1', '2', '3', '5'] → '1–3, 5' (other names as they are)."""
    runs: List[List[str]] = []
    for n in numbers:
        if runs and n.isdigit() and runs[-1][-1].isdigit():
            if int(n) == int(runs[-1][-1]) + 1:
                runs[-1].append(n)
                continue
        runs.append([n])
    return ", ".join(ref_range(run[0], run[-1]) for run in runs)


def line_unit(reference: Optional[str]) -> str:
    """Homer's references are book.line; prose texts use book.chapter.section."""
    if reference and reference.count(".") >= 2:
        return "section"
    return "line"


# Grammatical abbreviations for the condensed parsing ("acc. sg. fem.")
_ABBREVIATIONS = {
    "nominative": "nom.",
    "genitive": "gen.",
    "dative": "dat.",
    "accusative": "acc.",
    "vocative": "voc.",
    "singular": "sg.",
    "plural": "pl.",
    "dual": "du.",
    "masculine": "masc.",
    "feminine": "fem.",
    "neuter": "neut.",
    "common": "comm.",
    "present": "pres.",
    "imperfect": "impf.",
    "future": "fut.",
    "aorist": "aor.",
    "perfect": "perf.",
    "pluperfect": "plupf.",
    "future perfect": "fut. perf.",
    "indicative": "ind.",
    "subjunctive": "subj.",
    "optative": "opt.",
    "imperative": "imper.",
    "infinitive": "inf.",
    "participle": "ptcp.",
    "active": "act.",
    "middle": "mid.",
    "passive": "pass.",
    "middle-passive": "mid./pass.",
    "mediopassive": "mid./pass.",
    "comparative": "comp.",
    "superlative": "superl.",
}

_PERSONS = {
    "1": "1st",
    "2": "2nd",
    "3": "3rd",
    "first": "1st",
    "second": "2nd",
    "third": "3rd",
}

# Finite verbs read "3rd sg. aor. ind. act."; everything else (nouns,
# adjectives, participles, infinitives) reads "aor. mid. acc. sg. fem."
_FINITE_ORDER = ["person", "number", "tense", "mood", "voice"]
_NOMINAL_ORDER = ["tense", "voice", "mood", "case", "number", "gender", "degree"]
_ALL_KEYS = ["person", "number", "tense", "mood", "voice", "case", "gender", "degree"]


def _morph_value(key: str, value) -> str:
    text = str(value).strip()
    if key not in _ALL_KEYS:
        return text  # free text such as dialect_form keeps its case
    text = text.lower()
    if key == "person":
        return _PERSONS.get(text, text)
    return text


def _abbreviate(value: str) -> str:
    """'accusative' → 'acc.'; compound values like 'middle/passive' → 'mid./pass.'."""
    if value in _ABBREVIATIONS:
        return _ABBREVIATIONS[value]
    return "/".join(_ABBREVIATIONS.get(v, v) for v in value.split("/"))


def condensed_parse(pos: Optional[str], morphology: Optional[Dict]) -> str:
    """'participle', {tense: aorist, voice: middle, case: accusative, …} → 'participle · aor. mid. acc. sg. fem.'"""
    morphology = {k: v for k, v in (morphology or {}).items() if v not in (None, "")}
    order = _FINITE_ORDER if "person" in morphology else _NOMINAL_ORDER
    parts = []
    for key in order:
        if key not in morphology:
            continue
        value = _morph_value(key, morphology[key])
        if key == "mood" and value == (pos or "").lower():
            continue  # "participle · … ptcp." says it twice
        parts.append(value if key == "person" else _abbreviate(value))
    pos = (pos or "").strip()
    if pos and parts:
        return f"{pos} · {' '.join(parts)}"
    return pos or " ".join(parts)


def slash_breaks(value: Optional[str]) -> Markup:
    """A value that may wrap after a slash: 'middle/passive' → 'middle/<wbr>passive'."""
    return Markup(str(escape(value or "")).replace("/", "/<wbr>"))


# A run of Greek words: letters with their accents and elision marks, the
# words separated by spaces ("ἐκ Διός" in "+ ἐκ Διός, from Zeus")
_GREEK_LETTER = "\u0370-\u03ff\u1f00-\u1fff"
_GREEK_RUN = re.compile(
    f"[{_GREEK_LETTER}][{_GREEK_LETTER}\u0300-\u036fʼ’᾽]*"
    f"(?: [{_GREEK_LETTER}][{_GREEK_LETTER}\u0300-\u036fʼ’᾽]*)*"
)


def greek_spans(value: Optional[str]) -> Markup:
    """
    English text with its Greek marked for the Greek font: 'dative with
    ἐφίημι' → 'dative with <span lang="grc">ἐφίημι</span>'.
    """
    return Markup(
        _GREEK_RUN.sub(
            lambda m: f'<span lang="grc">{m.group(0)}</span>', str(escape(value or ""))
        )
    )


def parse_items(morphology: Optional[Dict]) -> List[Tuple[str, str]]:
    """Morphology as (label, full value) pairs in reading order, e.g. ('Tense', 'aorist')."""
    morphology = {k: v for k, v in (morphology or {}).items() if v not in (None, "")}
    order = _FINITE_ORDER if "person" in morphology else _NOMINAL_ORDER
    keys = [k for k in order if k in morphology]
    keys += [k for k in _ALL_KEYS if k in morphology and k not in keys]
    keys += sorted(k for k in morphology if k not in keys)
    return [
        (key.replace("_", " ").capitalize(), _morph_value(key, morphology[key]))
        for key in keys
    ]
