"""
The Perseus treebank's 9-character morphology codes (AGDT 2.1 postags), read
into the part of speech and morphology the app already shows for analyses:
'v3saia---' → ('verb', {'person': '3', 'number': 'singular', 'tense':
'aorist', 'mood': 'indicative', 'voice': 'active'}), and written back from
them for parses a model supplies.
"""

from typing import Dict, List, Mapping, Optional, Tuple

_POS = {
    "n": "noun",
    "v": "verb",
    "a": "adjective",
    "d": "adverb",
    "l": "article",
    "g": "particle",
    "c": "conjunction",
    "r": "preposition",
    "p": "pronoun",
    "m": "numeral",
    "i": "interjection",
    "e": "exclamation",
    "x": "irregular",
}

# GLAUx's own part-of-speech codes, read as the Perseus treebank's: "b" is a
# coordinating conjunction (καί, δέ, τε, ἀλλά), which Perseus tags "c"
_POS_ALIASES = {"b": "c"}

# Positions 2–9 of a postag; "-" means not applicable
_FIELDS = [
    ("person", {"1": "1", "2": "2", "3": "3"}),
    ("number", {"s": "singular", "p": "plural", "d": "dual"}),
    (
        "tense",
        {
            "p": "present",
            "i": "imperfect",
            "r": "perfect",
            "l": "pluperfect",
            "t": "future perfect",
            "f": "future",
            "a": "aorist",
        },
    ),
    (
        "mood",
        {
            "i": "indicative",
            "s": "subjunctive",
            "o": "optative",
            "n": "infinitive",
            "m": "imperative",
            "p": "participle",
        },
    ),
    ("voice", {"a": "active", "p": "passive", "m": "middle", "e": "middle/passive"}),
    ("gender", {"m": "masculine", "f": "feminine", "n": "neuter"}),
    (
        "case",
        {
            "n": "nominative",
            "g": "genitive",
            "d": "dative",
            "a": "accusative",
            "v": "vocative",
            "l": "locative",
        },
    ),
    ("degree", {"c": "comparative", "s": "superlative"}),
]


def decode(postag: Optional[str]) -> Tuple[str, Dict[str, str]]:
    """
    A postag's part of speech and morphology. Participles and infinitives
    read as such rather than "verb", as in the app's analyses. Unknown or
    missing codes are left out; a missing postag gives ("", {}).
    """
    if not postag:
        return "", {}
    code = postag.ljust(9, "-")
    morphology = {}
    for (name, values), letter in zip(_FIELDS, code[1:9]):
        if letter in values:
            morphology[name] = values[letter]
    pos = _POS.get(_POS_ALIASES.get(code[0], code[0]), "")
    if pos == "verb" and morphology.get("mood") in ("participle", "infinitive"):
        pos = morphology["mood"]
    return pos, morphology


# Parts of speech and morphology values a postag can hold, for a model's
# parse to choose from: {"pos": [...], "person": [...], ...}
VALUES: Dict[str, List[str]] = {
    "pos": list(_POS.values()),
    **{name: list(values.values()) for name, values in _FIELDS},
}


def encode(pos: str, morphology: Mapping[str, Optional[str]]) -> Optional[str]:
    """
    The postag for a part of speech and morphology, the inverse of decode():
    ('verb', {'person': '3', 'number': 'singular', 'tense': 'aorist', 'mood':
    'indicative', 'voice': 'active'}) → 'v3saia---'. "participle" and
    "infinitive" are verbs with that mood. Values a postag cannot hold are
    left as "-"; None if the part of speech is unknown.
    """
    if pos in ("participle", "infinitive"):
        morphology = {**morphology, "mood": pos}
        pos = "verb"
    pos_code = {name: code for code, name in _POS.items()}.get(pos)
    if pos_code is None:
        return None
    codes = [pos_code]
    for name, values in _FIELDS:
        letters = {value: letter for letter, value in values.items()}
        codes.append(letters.get(morphology.get(name) or "", "-"))
    return "".join(codes)
