"""
Message Batches requests for the saved study data of a treebank text: a fluent
translation per translation unit, a literal translation per line, and a gloss
and note per word, from Claude Opus 5.5; and notes-only requests, which redo
the notes of saved units with the revised note rule. This module builds the
requests and reads their results; `greek study batch` and `greek study notes`
(greek/cli/study_cmd.py) submit and collect them. Nothing here calls the API
except batch_client().
"""

import json
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from greek.api.prompts import GLOSS_RULE
from greek.api.works import ILIAD, WorkProfile
from greek.display.analysis import _PUNCT_STRIP
from greek.models import Line, TranslationUnit, TreebankSentence, Usage, WordParse
from greek.postag import VALUES, decode, encode
from greek.search import plain

MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_TOKENS = 32000
MAX_UNIT_LINES = 10
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Message Batches prices (half the standard ones), US dollars per million
# tokens, as of 2026-10-03. The system prompt is cached for an hour, because a
# batch can take longer than the default five minutes.
BATCH_PRICES = {
    "claude-opus-5-5": {
        "input": 2.00,
        "cache_write_1h": 4.00,
        "cache_write_5m": 2.50,
        "cache_read": 0.10,
        "output": 10.00,
    },
}

# Output tokens per word until a collected batch measures them: the plan's
# estimate from the 2026-10-03 eval (140 per word for a full analysis, less
# the grammar fields).
DEFAULT_OUTPUT_PER_WORD = 83.0

# Output tokens per word of a notes-only request until a notes batch is
# collected: the plan's estimate for the revised rule (about $0.75 of output
# per 1,000 words; notes four to five times longer for interesting words).
DEFAULT_NOTES_OUTPUT_PER_WORD = 75.0

# For a local input estimate, calibrated on the eval's requests to Opus 5.5:
# about 1.3 tokens per Greek character, and 2.7 characters per token otherwise.
_GREEK_TOKENS_PER_CHAR = 1.3
_OTHER_CHARS_PER_TOKEN = 2.7


def batch_client():
    """An Anthropic client for the Message Batches API."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    return anthropic.Anthropic(api_key=api_key)


# --- Translation units ------------------------------------------------------


def _book(line: Line) -> str:
    return (line.reference or "").split(".")[0]


def _chapter(line: Line) -> str:
    return ".".join((line.reference or "").split(".")[:2])


def _word_count(line: Line) -> int:
    return len(line.content.split())


def group_units(
    text_id: int,
    lines: Sequence[Line],
    sentences: Sequence[TreebankSentence],
    max_lines: Optional[int] = MAX_UNIT_LINES,
    max_words: Optional[int] = None,
) -> List[TranslationUnit]:
    """
    A text's translation units, in order: every line belongs to exactly one.
    Treebank sentences that share a line are merged; then, within a book,
    consecutive sentences are grouped while the unit stays within max_lines
    (a longer sentence is a unit of its own). A line in no sentence (one the
    treebank omits) joins the preceding sentence, or the following one at
    the start of a book; a book without sentences is cut into max_lines.

    With max_words (prose, whose lines are sections), sentences are grouped
    within a chapter while the unit stays within max_words words instead: a
    longer section, or sections that share a sentence, make a unit alone.
    """
    spans: List[List[int]] = []
    for s in sorted(sentences, key=lambda s: (s.first_line, s.last_line)):
        if spans and s.first_line <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], s.last_line)
        else:
            spans.append([s.first_line, s.last_line])
    span_of: Dict[int, int] = {}
    for i, (first, last) in enumerate(spans):
        for n in range(first, last + 1):
            span_of[n] = i

    books: Dict[str, List[Line]] = defaultdict(list)
    for line in sorted(lines, key=lambda line: line.line_number):
        books[_book(line)].append(line)
    by_number = {line.line_number: line for line in lines}

    def too_big(group: List[int], block: List[int]) -> bool:
        if max_words is None:
            return len(group) + len(block) > max_lines
        if _chapter(by_number[block[0]]) != _chapter(by_number[group[-1]]):
            return True
        words = sum(_word_count(by_number[n]) for n in group + block)
        return words > max_words

    units: List[TranslationUnit] = []
    for book_lines in books.values():
        # Blocks of lines that must stay together: a sentence span (cut at
        # the book's edges) with the uncovered lines after it.
        blocks: List[List[int]] = []
        leading: List[int] = []
        current = None
        for line in book_lines:
            span = span_of.get(line.line_number)
            if span is None:
                (blocks[-1] if blocks else leading).append(line.line_number)
            elif blocks and span == current:
                blocks[-1].append(line.line_number)
            else:
                blocks.append([line.line_number])
            current = span if span is not None else current
        if leading and blocks:
            blocks[0][:0] = leading
        elif leading:
            blocks = [[n] for n in leading]

        group: List[int] = []
        for block in blocks:
            if group and too_big(group, block):
                units.append(TranslationUnit(text_id, group[0], group[-1]))
                group = []
            group += block
        if group:
            units.append(TranslationUnit(text_id, group[0], group[-1]))
    return units


def custom_id(unit: TranslationUnit) -> str:
    """A request's id: text 12, lines 1–7 → "12_1-7" (the API allows [A-Za-z0-9_-])."""
    return f"{unit.text_id}_{unit.first_line}-{unit.last_line}"


_CUSTOM_ID = re.compile(r"^(\d+)_(\d+)-(\d+)$")


def parse_custom_id(value: str) -> Optional[Tuple[int, int, int]]:
    """(text_id, first_line, last_line) of a custom_id; None if it isn't one."""
    match = _CUSTOM_ID.match(value.strip())
    if not match:
        return None
    text_id, first, last = (int(g) for g in match.groups())
    return (text_id, first, last) if first <= last else None


# --- Words and the prompt ---------------------------------------------------


@dataclass
class StudyWord:
    """
    A word of a unit: a whitespace token of its line, as printed (without
    punctuation), with the treebank parse of each of its parts; whether a
    lemma of it appears here for the first time in its book; whether its
    sentence was parsed automatically (GLAUx's parser, not by hand); for a
    notes-only or uses request, its saved gloss; and for a uses request, its
    role in the treebank's tree and, for a word to label, what kind it is
    (greek/api/uses.py).
    """

    line_number: int
    token_index: int
    reference: str
    form: str
    parts: List[WordParse] = field(default_factory=list)
    first_in_book: bool = False
    gloss: Optional[str] = None
    role: str = ""
    target: Optional[str] = None
    automatic: bool = False

    @property
    def needs_parse(self) -> bool:
        """No treebank word, or a part without a lemma or parse."""
        return not self.parts or not all(p.has_parse for p in self.parts)

    @property
    def in_sentence(self) -> bool:
        """Whether a part of the word is in a treebank sentence."""
        return any(p.sentence_id is not None for p in self.parts)


def is_word(form: str, parts: Sequence[WordParse]) -> bool:
    """
    Whether a token is a word to study: not an editorial mark or stray
    punctuation (no Greek letter: †, ], 〉), nor a speaker's label that the
    treebank lacks (Greek capitals only: ΑΘ., ΜΗΛ. in Thucydides' Melian
    dialogue). The Iliad and the Odyssey have neither.
    """
    letters = [ch for ch in form if _is_greek(ch) and ch.isalpha()]
    if not letters:
        return False
    if len(letters) > 1 and not any(ch.islower() for ch in letters):
        return any(p.lemma for p in parts)
    return True


def study_words(
    lines: Sequence[Line],
    parses: Sequence[WordParse],
    first_in_book: Optional[Set[Tuple[int, int]]] = None,
    automatic: Optional[Set[int]] = None,
) -> List[StudyWord]:
    """
    Every word of the lines in order (is_word(): editorial marks and
    speakers' labels are left out), with its treebank parses, marked when
    its (line_number, token_index) is in first_in_book, and as parsed
    automatically when a part's sentence_id is in automatic.
    """
    parts: Dict[Tuple[int, int], List[WordParse]] = defaultdict(list)
    for p in sorted(parses, key=lambda p: (p.line_number, p.token_index, p.part)):
        parts[(p.line_number, p.token_index)].append(p)
    words = []
    for line in lines:
        for i, token in enumerate(line.content.split()):
            form = token.strip(_PUNCT_STRIP) or token
            word_parts = parts.get((line.line_number, i), [])
            if not is_word(form, word_parts):
                continue
            words.append(
                StudyWord(
                    line_number=line.line_number,
                    token_index=i,
                    reference=line.reference or str(line.line_number),
                    form=form,
                    parts=word_parts,
                    first_in_book=(line.line_number, i) in (first_in_book or ()),
                    automatic=bool(automatic)
                    and any(p.sentence_id in automatic for p in word_parts),
                )
            )
    return words


def _lemma_key(lemma: str) -> str:
    """A lemma without its homograph digit, in NFC."""
    return unicodedata.normalize("NFC", lemma).rstrip("0123456789")


def first_in_book(words: Sequence[StudyWord]) -> Set[Tuple[int, int]]:
    """
    The (line_number, token_index) of the words, given in text order, that
    bring a lemma into their book for the first time; for a word of several
    parts, any part's lemma. Words without a lemma are never marked.
    """
    seen: Dict[str, Set[str]] = defaultdict(set)
    marked = set()
    for word in words:
        lemmas = seen[word.reference.split(".")[0]]
        new = {_lemma_key(p.lemma) for p in word.parts if p.lemma} - lemmas
        if new:
            lemmas |= new
            marked.add((word.line_number, word.token_index))
    return marked


# Notes from this rule are stored with note_version 2 (Phase 5's were 1). It is
# shared by the study request and the notes-only request; each work fills in
# what makes a word worth a closer look (WorkProfile.note_topics).
NOTE_VERSION = 2
_NOTE_RULE = (
    "what a reader should know about this word here. Let the length follow how "
    "much there is to say: a common word with nothing particular here gets a few "
    "words or one short sentence; a word worth a closer look gets up to four or "
    "five sentences. Words worth a closer look include {topics}; and what a "
    "translation can't carry over. Do not restate the parse or repeat the gloss. "
    'A word marked "first in this book" gets its background here; later in the '
    "book, say only what is particular to this place."
)


def note_rule(work: WorkProfile) -> str:
    return _NOTE_RULE.format(topics=work.note_topics)


def first_in_book_rule(work: WorkProfile) -> str:
    return (
        'A word marked "first in this book" is the first appearance of its lemma '
        f"in this book of {work.reading}."
    )


NOTE_RULE = note_rule(ILIAD)
_SPLIT_WORD = (
    'A word the treebank divides is shown with both parts ("οὐδʼ: οὐ, adverb + '
    'δέ, particle"); it is still one word.'
)
# GLAUx calls δέ a conjunction, as a prose request shows it
_SPLIT_WORD_PROSE = _SPLIT_WORD.replace("δέ, particle", "δέ, conjunction")


def split_word_rule(work: WorkProfile) -> str:
    return _SPLIT_WORD if work.verse else _SPLIT_WORD_PROSE


_AUTOMATIC = "automatic parse"
# Prose only: GLAUx's lemmas 98.8%, morphology 97.2% and syntax about 80%
# right where its parser annotated a sentence.
AUTOMATIC_RULE = (
    f'Words under a heading marked "({_AUTOMATIC})", and words marked '
    f'"{_AUTOMATIC}", were lemmatized and parsed by GLAUx\'s parser, not by '
    "hand: about one lemma and three parses in a hundred are wrong there."
)
_NOT_WORDS = {
    True: "punctuation is not a word",
    False: (
        "punctuation, editorial marks and speakers' names (ΑΘ., ΜΗΛ.) are not words"
    ),
}
_PARSE_RULE = (
    'only for a word marked "no treebank parse": its lemma (the dictionary '
    'form) and full parse. Use "-" for each field that does not apply to the '
    'word. A participle is pos "verb" with mood "participle"; an infinitive '
    'is pos "verb" with mood "infinitive".'
)
_REPEAT_WORD = '- "word": the numbered word, exactly as given.\n'

_VERSE_STUDY = """You are an expert in Ancient Greek philology and Homeric epic. You write study aids for a reader working through {reading} in Greek: translations of each passage, and a gloss and a note for every word.

Each request gives a passage of whole sentences: its lines, labelled book.line, and then every word of the passage in order, numbered from 1, with its lemma and parse from the Perseus Ancient Greek Dependency Treebank. {split} A word marked "no treebank parse" has none. {first}

Return a JSON object with these fields.

"fluent": an accurate, natural English translation of the whole passage, in prose.

"literal": a literal translation with exactly one string per Greek line, in order. Follow the Greek word order and constructions as closely as English allows, and keep each Greek line's words in that line's string, even when a sentence runs on into the next line.

"words": exactly one object per numbered word, in order, so there are as many objects as numbered words. Never skip, merge, split or add a word; {not_words}. Each object has:
{word}- "gloss": {gloss}
- "note": {note}
- "parse_check": only when the treebank's lemma or parse for this word is wrong in this context. Say briefly what it should be and why, e.g. "imperative, not indicative: addressed to the Muses". Leave it out when the treebank is right, and for differences of convention: particle or conjunction (δέ, τε), adjective or pronoun (αὐτός, ὅσος), an epic or Attic spelling of a lemma, and "middle/passive" for a form that is middle or passive.
- "parse": {parse}"""

_PROSE_STUDY = """You are an expert in Ancient Greek philology and Greek historiography. You write study aids for a reader working through {reading} in Greek: translations of each passage, and a gloss and a note for every word.

Each request gives a passage of whole sentences: its sections, labelled book.chapter.section, and then every word of the passage in order, numbered from 1, with its lemma and parse from the GLAUx corpus. {split} A word marked "no treebank parse" has none. {automatic} {first}

Return a JSON object with these fields.

"fluent": an accurate, natural English translation of the whole passage.

"literal": a literal translation with exactly one string per section, in order. Follow the Greek word order and constructions as closely as English allows, and keep each section's words in that section's string, even when a sentence runs on into the next section.

"words": exactly one object per numbered word, in order, so there are as many objects as numbered words. Never skip, merge, split or add a word; {not_words}. Each object has:
{word}- "gloss": {gloss}
- "note": {note}
- "parse_check": only when the treebank's lemma or parse for this word is wrong in this context. Say briefly what it should be and why, e.g. "participle, not indicative: agrees with τοὺς Φοίνικας". Look hardest at words parsed automatically. Leave it out when the treebank is right, and for differences of convention: particle or conjunction (δέ, τε, καί), adjective or pronoun (αὐτός, ὅσος), {convention}, a suppletive form filed under its verb (εἶπον under λέγω, εἶδον under ὁράω, ᾖσαν under ἔρχομαι), the relative pronoun filed under ὁ, and "middle/passive" for a form that is middle or passive. A lemma that is not a Greek word (a name spelt letter by letter, such as τηεριμενες for Θηριμένης) is wrong: give the right one.
- "parse": {parse}"""


def study_system_prompt(work: WorkProfile, repeat_words: bool = False) -> str:
    """
    The system prompt of a work's study requests (the Iliad's is the one its
    Books 1–7 were made with). With repeat_words, each word object first
    repeats its word, as a notes answer does, so a skipped word fails where
    it happens.
    """
    fields = dict(
        reading=work.reading,
        split=split_word_rule(work),
        first=first_in_book_rule(work),
        not_words=_NOT_WORDS[work.verse],
        word=_REPEAT_WORD if repeat_words else "",
        gloss=GLOSS_RULE,
        note=note_rule(work),
        parse=_PARSE_RULE,
    )
    if work.verse:
        return _VERSE_STUDY.format(**fields)
    return _PROSE_STUDY.format(
        **fields, automatic=AUTOMATIC_RULE, convention=work.lemma_convention
    )


STUDY_SYSTEM_PROMPT = study_system_prompt(ILIAD)


_PERSON = {"1": "1st", "2": "2nd", "3": "3rd"}


def parse_label(postag: Optional[str]) -> str:
    """'v3saia---' → 'verb 3rd singular aorist indicative active'."""
    pos, morphology = decode(postag)
    values = [
        _PERSON.get(value, value) if name == "person" else value
        for name, value in morphology.items()
        if not (name == "mood" and value == pos)
    ]
    return " ".join([pos, *values]).strip()


def _word_line(number: int, word: StudyWord, automatic: bool = False) -> str:
    """
    '3 θεὰ: θεά, noun singular feminine vocative | gloss: goddess | first in
    this book'; with automatic, "| automatic parse" after the parse.
    """
    if word.needs_parse:
        fields = ["no treebank parse"]
    else:
        fields = [" + ".join(f"{p.lemma}, {parse_label(p.postag)}" for p in word.parts)]
    if automatic:
        fields.append(_AUTOMATIC)
    if word.gloss is not None:
        fields.append(f"gloss: {word.gloss}")
    if word.first_in_book:
        fields.append("first in this book")
    return f"{number} {word.form}: {' | '.join(fields)}"


def _automatic_references(words: Sequence[StudyWord]) -> Set[str]:
    """
    The references whose words in a treebank sentence were all parsed
    automatically (a prose section of GLAUx's parser); their heading says
    so, and their words aren't marked one by one.
    """
    seen: Dict[str, bool] = {}
    for w in words:
        if w.in_sentence:
            seen[w.reference] = seen.get(w.reference, True) and w.automatic
    return {reference for reference, automatic in seen.items() if automatic}


def numbered_words(words: Sequence[StudyWord], word_line=_word_line) -> List[str]:
    """
    The words, numbered from 1, under a [book.line] heading for each line: a
    heading "[2.1.1] (automatic parse)" for a section parsed automatically
    throughout, otherwise "automatic parse" on each such word.
    """
    out = []
    reference = None
    whole = _automatic_references(words)
    for number, word in enumerate(words, 1):
        if word.reference != reference:
            reference = word.reference
            mark = f" ({_AUTOMATIC})" if reference in whole else ""
            out.append(f"[{reference}]{mark}")
        out.append(word_line(number, word, word.automatic and reference not in whole))
    return out


def _passage_lines(work: WorkProfile, lines: Sequence[Line]) -> Tuple[str, List[str]]:
    """The passage's span ("Iliad 1.1–1.7") and its lines, labelled."""
    first = lines[0].reference or str(lines[0].line_number)
    last = lines[-1].reference or str(lines[-1].line_number)
    span = first if first == last else f"{first}–{last}"
    return f"{work.title} {span}", [
        f"{line.reference or line.line_number} {line.content}" for line in lines
    ]


def _strings(work: WorkProfile, n: int) -> str:
    """ "7 strings"; a prose unit of one section asks for "1 string"."""
    return f"{n} string" if n == 1 and not work.verse else f"{n} strings"


def _lines_heading(work: WorkProfile) -> str:
    return "Lines:" if work.verse else "Sections:"


def build_study_prompt(
    work: WorkProfile, lines: Sequence[Line], words: Sequence[StudyWord]
) -> str:
    """The user message for one unit: its lines, then its words with their parses."""
    passage, labelled = _passage_lines(work, lines)
    return "\n".join(
        [
            f"{passage}: {work.count(len(lines))}, {len(words)} words. "
            f'Give "literal" {_strings(work, len(lines))} and "words" {len(words)} '
            "objects.",
            "",
            _lines_heading(work),
            *labelled,
            "",
            "Words:",
            *numbered_words(words),
        ]
    )


def _parse_schema() -> Dict[str, Any]:
    properties: Dict[str, Any] = {"lemma": {"type": "string"}}
    for name, values in VALUES.items():
        enum = values if name == "pos" else ["-", *values]
        properties[name] = {"type": "string", "enum": enum}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _study_schema(repeat_words: bool) -> Dict[str, Any]:
    word: Dict[str, Any] = {"word": {"type": "string"}} if repeat_words else {}
    return {
        "type": "object",
        "properties": {
            "fluent": {"type": "string"},
            "literal": {"type": "array", "items": {"type": "string"}},
            "words": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        **word,
                        "gloss": {"type": "string"},
                        "note": {"type": "string"},
                        "parse_check": {"type": "string"},
                        "parse": _parse_schema(),
                    },
                    "required": [*word, "gloss", "note"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["fluent", "literal", "words"],
        "additionalProperties": False,
    }


# One schema for every request (two with --repeat-words), so the API compiles
# it once. Counts can't be expressed in it; check_output() checks them.
STUDY_SCHEMA: Dict[str, Any] = _study_schema(False)
STUDY_SCHEMA_WORDS: Dict[str, Any] = _study_schema(True)


def _request(
    cid: str, system: str, schema: Dict[str, Any], content: str, effort: str
) -> Dict[str, Any]:
    return {
        "custom_id": cid,
        "params": {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            # Thinking is always on for Opus 5.5; effort is set explicitly.
            "output_config": {
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            "messages": [{"role": "user", "content": content}],
        },
    }


def build_request(
    work: WorkProfile,
    unit: TranslationUnit,
    lines: Sequence[Line],
    words: Sequence[StudyWord],
    effort: str = EFFORT,
    repeat_words: bool = False,
) -> Dict[str, Any]:
    """One Message Batches request ({custom_id, params}) for a unit."""
    return _request(
        custom_id(unit),
        study_system_prompt(work, repeat_words),
        STUDY_SCHEMA_WORDS if repeat_words else STUDY_SCHEMA,
        build_study_prompt(work, lines, words),
        effort,
    )


# --- Notes-only requests ------------------------------------------------------

_VERSE_NOTES = """You are an expert in Ancient Greek philology and Homeric epic. You write study notes for a reader working through {reading} in Greek: a note for every word.

Each request gives a passage of whole sentences: its lines, labelled book.line, an English translation of the passage, and then every word of the passage in order, numbered from 1, with its lemma and parse from the Perseus Ancient Greek Dependency Treebank and the gloss the reader sees with it. {split} {first}

Return a JSON object with one field, "notes": exactly one object per numbered word, in order, so there are as many objects as numbered words. Never skip, merge, split or add a word; {not_words}. Each object has:
- "word": the numbered word, exactly as given.
- "note": {note}"""

_PROSE_NOTES = """You are an expert in Ancient Greek philology and Greek historiography. You write study notes for a reader working through {reading} in Greek: a note for every word.

Each request gives a passage of whole sentences: its sections, labelled book.chapter.section, an English translation of the passage, and then every word of the passage in order, numbered from 1, with its lemma and parse from the GLAUx corpus and the gloss the reader sees with it. {split} {first}

Return a JSON object with one field, "notes": exactly one object per numbered word, in order, so there are as many objects as numbered words. Never skip, merge, split or add a word; {not_words}. Each object has:
- "word": the numbered word, exactly as given.
- "note": {note}"""


def notes_system_prompt(work: WorkProfile) -> str:
    """The system prompt of a work's notes-only requests."""
    template = _VERSE_NOTES if work.verse else _PROSE_NOTES
    return template.format(
        reading=work.reading,
        split=split_word_rule(work),
        first=first_in_book_rule(work),
        not_words=_NOT_WORDS[work.verse],
        note=note_rule(work),
    )


NOTES_SYSTEM_PROMPT = notes_system_prompt(ILIAD)

# One schema for every request; check_notes() checks the count and the words.
NOTES_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "word": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["word", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["notes"],
    "additionalProperties": False,
}


def build_notes_prompt(
    work: WorkProfile, lines: Sequence[Line], fluent: str, words: Sequence[StudyWord]
) -> str:
    """
    The user message for a unit's notes: its lines, its saved fluent
    translation, then its words with their parses, saved glosses and
    first-in-book marks.
    """
    passage, labelled = _passage_lines(work, lines)
    return "\n".join(
        [
            f"{passage}: {work.count(len(lines))}, {len(words)} words. "
            f'Give "notes" {len(words)} objects.',
            "",
            _lines_heading(work),
            *labelled,
            "",
            "Translation:",
            fluent,
            "",
            "Words:",
            *numbered_words(words),
        ]
    )


def build_notes_request(
    work: WorkProfile,
    unit: TranslationUnit,
    lines: Sequence[Line],
    fluent: str,
    words: Sequence[StudyWord],
    effort: str = EFFORT,
) -> Dict[str, Any]:
    """One Message Batches request ({custom_id, params}) for a unit's notes."""
    return _request(
        custom_id(unit),
        notes_system_prompt(work),
        NOTES_SCHEMA,
        build_notes_prompt(work, lines, fluent, words),
        effort,
    )


# --- Estimates and costs ----------------------------------------------------


def _is_greek(ch: str) -> bool:
    return "Ͱ" <= ch <= "Ͽ" or "ἀ" <= ch <= "῿"


def estimate_tokens(text: str) -> int:
    """A local estimate of a text's input tokens (no API call)."""
    text = unicodedata.normalize("NFC", text)
    greek = sum(1 for ch in text if _is_greek(ch))
    return round(
        greek * _GREEK_TOKENS_PER_CHAR + (len(text) - greek) / _OTHER_CHARS_PER_TOKEN
    )


def estimate_input_tokens(request: Mapping[str, Any]) -> int:
    """
    A request's estimated input tokens: system prompt, user message and
    schema. The cost estimate prices them all as uncached input; in a batch
    the system prompt is written to the cache (2× input) or read from it
    (0.05×), and cache hits are best-effort.
    """
    params = request["params"]
    text = "".join(block["text"] for block in params["system"])
    text += "".join(m["content"] for m in params["messages"])
    text += json.dumps(params["output_config"]["format"]["schema"])
    return estimate_tokens(text)


def usage_of(message_usage: Any, model: str = MODEL) -> Usage:
    """A batch result's usage, priced at batch rates."""
    prices = BATCH_PRICES[model]
    input_tokens = message_usage.input_tokens or 0
    output_tokens = message_usage.output_tokens or 0
    cache_read = message_usage.cache_read_input_tokens or 0
    cache_write = message_usage.cache_creation_input_tokens or 0
    breakdown = getattr(message_usage, "cache_creation", None)
    if breakdown is not None:
        write_5m = breakdown.ephemeral_5m_input_tokens or 0
        write_1h = breakdown.ephemeral_1h_input_tokens or 0
    else:
        write_5m, write_1h = 0, cache_write
    cost = (
        input_tokens * prices["input"]
        + write_1h * prices["cache_write_1h"]
        + write_5m * prices["cache_write_5m"]
        + cache_read * prices["cache_read"]
        + output_tokens * prices["output"]
    ) / 1_000_000
    return Usage(input_tokens, cache_write, cache_read, output_tokens, cost)


@dataclass
class Estimate:
    requests: int
    lines: int
    words: int
    input_tokens: int
    output_tokens: int
    output_per_word: float
    model: str = MODEL

    @property
    def input_cost(self) -> float:
        return self.input_tokens * BATCH_PRICES[self.model]["input"] / 1_000_000

    @property
    def output_cost(self) -> float:
        return self.output_tokens * BATCH_PRICES[self.model]["output"] / 1_000_000

    @property
    def cost(self) -> float:
        return self.input_cost + self.output_cost


def estimate(
    requests: Sequence[Mapping[str, Any]],
    line_count: int,
    word_count: int,
    output_per_word: float = DEFAULT_OUTPUT_PER_WORD,
) -> Estimate:
    """The estimated tokens and batch cost of some requests."""
    return Estimate(
        requests=len(requests),
        lines=line_count,
        words=word_count,
        input_tokens=sum(estimate_input_tokens(r) for r in requests),
        output_tokens=round(word_count * output_per_word),
        output_per_word=output_per_word,
    )


# --- Reading results --------------------------------------------------------


@dataclass
class WordOutput:
    """A word's gloss and note, and a model parse for a word the treebank lacks."""

    gloss: str
    note: str
    parse_check: Optional[str] = None
    lemma: Optional[str] = None
    postag: Optional[str] = None


@dataclass
class StudyOutput:
    fluent: str
    literal: List[str]
    words: List[WordOutput]


@dataclass
class Outcome:
    """A batch result: the checked output, or why it failed; and its usage."""

    custom_id: str
    usage: Usage
    output: Optional[StudyOutput] = None
    failure: Optional[str] = None


def _text(value: Any, what: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or not (allow_empty or value.strip()):
        raise ValueError(f"{what} is missing or empty")
    return value.strip()


def _model_parse(value: Any, number: int) -> Tuple[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"word {number} has no parse")
    lemma = _text(value.get("lemma"), f"word {number}'s lemma")
    morphology = {}
    for name, values in VALUES.items():
        if name == "pos":
            continue
        v = value.get(name, "-")
        if v != "-" and v not in values:
            raise ValueError(f"word {number}'s parse has {name} {v!r}")
        if v != "-":
            morphology[name] = v
    postag = encode(value.get("pos", ""), morphology)
    if postag is None:
        raise ValueError(f"word {number}'s parse has pos {value.get('pos')!r}")
    return lemma, postag


def check_output(data: Any, words: Sequence[StudyWord], line_count: int) -> StudyOutput:
    """
    A result's JSON checked against its unit: one literal line per line, one
    word object per word with a gloss, and a parse for every word that
    needs one; a word object that repeats its word (--repeat-words) must
    repeat the numbered one. Raises ValueError saying what is wrong.
    """
    if not isinstance(data, dict):
        raise ValueError("the output is not a JSON object")
    fluent = _text(data.get("fluent"), "fluent")
    literal = data.get("literal")
    if not isinstance(literal, list):
        raise ValueError("literal is missing")
    if len(literal) != line_count:
        raise ValueError(
            f"wrong count: {len(literal)} literal lines for {line_count} lines"
        )
    literal = [_text(s, f"literal line {i}") for i, s in enumerate(literal, 1)]
    items = data.get("words")
    if not isinstance(items, list):
        raise ValueError("words is missing")
    if len(items) != len(words):
        raise ValueError(
            f"wrong count: {len(items)} word objects for {len(words)} words"
        )
    out = []
    for number, (item, word) in enumerate(zip(items, words), 1):
        if not isinstance(item, dict):
            raise ValueError(f"word {number} is not an object")
        if "word" in item:
            _check_word(_text(item["word"], f"word {number}'s word"), word, number)
        result = WordOutput(
            gloss=_text(item.get("gloss"), f"word {number}'s gloss"),
            note=_text(item.get("note"), f"word {number}'s note", allow_empty=True),
            parse_check=(item.get("parse_check") or "").strip() or None,
        )
        if word.needs_parse:
            try:
                result.lemma, result.postag = _model_parse(item.get("parse"), number)
            except ValueError as e:
                raise ValueError(f"{e} ({word.reference} {word.form})") from None
        out.append(result)
    return StudyOutput(fluent=fluent, literal=literal, words=out)


def _result_json(entry: Any, model: str) -> Tuple[Usage, Any, Optional[str]]:
    """
    A batch result's usage and JSON output, or why it failed: an API error,
    expiry, cancellation, a refusal, truncation at max_tokens, or output that
    isn't JSON. A failed message is still billed, so its usage is kept.
    """
    result = entry.result
    if result.type == "errored":
        error = result.error.error
        return Usage(), None, f"error: {error.type}: {error.message}"
    if result.type in ("expired", "canceled"):
        return Usage(), None, result.type
    message = result.message
    usage = usage_of(message.usage, model)
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None)
        return usage, None, f"refusal ({category})" if category else "refusal"
    if message.stop_reason == "max_tokens":
        return usage, None, "truncated at max_tokens"
    text = "".join(b.text for b in message.content if b.type == "text")
    try:
        return usage, json.loads(text), None
    except json.JSONDecodeError:
        return usage, None, "the output is not valid JSON"


def read_result(
    entry: Any, words: Sequence[StudyWord], line_count: int, model: str = MODEL
) -> Outcome:
    """
    One entry of a batch's results, checked against its unit. Failures are
    those of _result_json() and wrong counts.
    """
    usage, data, failure = _result_json(entry, model)
    if failure:
        return Outcome(entry.custom_id, usage, failure=failure)
    try:
        output = check_output(data, words, line_count)
    except ValueError as e:
        return Outcome(entry.custom_id, usage, failure=str(e))
    return Outcome(entry.custom_id, usage, output=output)


def same_word(given: str, form: str) -> bool:
    """
    Whether a word the model repeated is the numbered form: equal in NFC, or
    else without accents, breathings and elision marks (plain()).
    """
    given = unicodedata.normalize("NFC", given.strip().strip(_PUNCT_STRIP))
    form = unicodedata.normalize("NFC", form)
    return given == form or plain(given) == plain(form)


def _check_word(given: str, word: StudyWord, number: int, what: str = "word") -> None:
    """
    That a repeated word is the numbered one: its form, or the lemma of a
    part of it, which the model sometimes gives instead.
    """
    lemmas = [_lemma_key(p.lemma) for p in word.parts if p.lemma]
    if not any(same_word(given, form) for form in [word.form, *lemmas]):
        raise ValueError(
            f"{what} {number} is for {given}, not {word.form} ({word.reference})"
        )


def check_notes(data: Any, words: Sequence[StudyWord]) -> List[str]:
    """
    A notes result's JSON checked against its unit: one note per word, each
    repeating its word (or the lemma of a part of it, which the model
    sometimes gives instead). Raises ValueError saying what is wrong, so a
    skipped word fails the request where it happens.
    """
    if not isinstance(data, dict):
        raise ValueError("the output is not a JSON object")
    items = data.get("notes")
    if not isinstance(items, list):
        raise ValueError("notes is missing")
    if len(items) != len(words):
        raise ValueError(f"wrong count: {len(items)} notes for {len(words)} words")
    notes = []
    for number, (item, word) in enumerate(zip(items, words), 1):
        if not isinstance(item, dict):
            raise ValueError(f"note {number} is not an object")
        _check_word(
            _text(item.get("word"), f"note {number}'s word"), word, number, "note"
        )
        notes.append(_text(item.get("note"), f"note {number}", allow_empty=True))
    return notes


@dataclass
class NotesOutcome:
    """A notes result: one note per word, or why it failed; and its usage."""

    custom_id: str
    usage: Usage
    notes: Optional[List[str]] = None
    failure: Optional[str] = None


def read_notes_result(
    entry: Any, words: Sequence[StudyWord], model: str = MODEL
) -> NotesOutcome:
    """One entry of a notes batch's results, checked against its unit's words."""
    usage, data, failure = _result_json(entry, model)
    if failure:
        return NotesOutcome(entry.custom_id, usage, failure=failure)
    try:
        notes = check_notes(data, words)
    except ValueError as e:
        return NotesOutcome(entry.custom_id, usage, failure=str(e))
    return NotesOutcome(entry.custom_id, usage, notes=notes)
