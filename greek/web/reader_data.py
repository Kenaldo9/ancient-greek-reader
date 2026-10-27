"""DB-backed view data shared by the reader, passage box and word strip routes."""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import quote

from fastapi import Request

from greek.api.study import MODEL as STUDY_MODEL
from greek.db.repos.lexicon import get_lemma_entries
from greek.db.repos.lines import (
    book_span,
    get_lines,
    line_lengths,
    list_books,
    search_lines,
)
from greek.db.repos.parses import (
    get_tb_source,
    has_word_parses,
    lemma_frequency,
    list_sentence_nodes,
    list_tb_nodes,
    list_tb_sentences_between,
    list_word_parses,
    node_positions,
    sentence_annotations,
)
from greek.db.repos.study import (
    book_study,
    get_lemma_forms,
    get_word_use,
    list_line_translations,
    list_translation_units,
    list_word_study,
    saved_word_uses,
    study_line_glosses,
)
from greek.db.repos.verbs import get_verb_parts, list_verb_parts
from greek.display.analysis import _PUNCT_STRIP
from greek.api.works import DIALECTS, work_profile
from greek.fetch.treebank import WORKS
from greek.models import (
    BookStudy,
    LemmaEntry,
    LemmaForms,
    LexiconEntry,
    Line,
    TextMeta,
    TreebankSource,
    WordParse,
    WordStudy,
    WordUse,
)
from greek.postag import decode
from greek.search import excerpt, highlight, plain
from greek.syntax import OutlineRow, Role, SentenceSyntax, build_syntax, text_syntax
from greek.web.formatting import condensed_parse, number_ranges, parse_items
from greek.web.reading import split_parts, tokens

INTERLINEAR_COOKIE = "interlinear"
SEARCH_LIMIT = 200  # results shown; the total is always counted
MAX_STUDY_WORDS = 600  # rows of "Grammar" for one selection
# Lines one selection may have: a whole page (the longest, Iliad 5, has 909),
# but not a whole text's translations in one request
MAX_SELECTION_LINES = 1000


def selection_too_long(start: int, end: int) -> Optional[str]:
    """Why the selection of lines [start, end] is refused, or None."""
    if end - start + 1 > MAX_SELECTION_LINES:
        return f"Select at most {MAX_SELECTION_LINES:,} lines at a time."
    return None


def interlinear_on(request: Request, explicit: Optional[bool] = None) -> bool:
    if explicit is not None:
        return explicit
    return request.cookies.get(INTERLINEAR_COOKIE) == "1"


def ref(line: Line) -> str:
    return line.reference or str(line.line_number)


def references(
    conn: sqlite3.Connection, text_id: int, numbers: Iterable[Optional[int]]
) -> Dict[int, str]:
    """line_number → reference (or the number itself when a line has none)."""
    wanted = sorted({n for n in numbers if n is not None})
    if not wanted:
        return {}
    placeholders = ",".join("?" * len(wanted))
    rows = conn.execute(
        f"SELECT line_number, reference FROM lines WHERE text_id=? AND line_number IN ({placeholders})",
        (text_id, *wanted),
    ).fetchall()
    found = {r["line_number"]: r["reference"] or str(r["line_number"]) for r in rows}
    return {n: found.get(n, str(n)) for n in wanted}


def reader_path(text_id: int, at: str) -> str:
    """The reader opened at reference `at`, as the Library and the contents link a book."""
    return f"/texts/{text_id}/read?at={quote(at, safe='.')}"


def page_starts(conn: sqlite3.Connection, text_id: int) -> List[str]:
    """
    The reference each of a text's pages starts at, in order: each book, or
    each part of a book long enough to be split (reading.split_parts), or
    the whole text from its first line when it has no books, as the reader
    pages it (find_page).
    """
    spans = [(b.line_start, b.line_end) for b in list_books(conn, text_id)]
    if not spans:
        first, last = conn.execute(
            "SELECT MIN(line_number), MAX(line_number) FROM lines WHERE text_id=?",
            (text_id,),
        ).fetchone()
        spans = [(first, last)] if first is not None else []
    starts = [
        start
        for lo, hi in spans
        for start in (split_parts(line_lengths(conn, text_id, lo, hi)) or [lo])
    ]
    refs = references(conn, text_id, starts)
    return [refs[start] for start in starts]


def word_study_at(
    conn: sqlite3.Connection, text_id: int, start: int, end: int
) -> Dict[Tuple[int, int], WordStudy]:
    """(line, token) → the study data's gloss and note, for the words of [start, end]."""
    return {
        (w.line_number, w.token_index): w
        for w in list_word_study(conn, text_id, start, end, STUDY_MODEL)
    }


def study_glosses(
    conn: sqlite3.Connection,
    text_id: int,
    contents: Dict[int, str],
    start: int,
    end: int,
) -> Dict[int, List[str]]:
    """line_number → the study data's gloss of each token, for fully glossed lines."""
    return study_line_glosses(conn, text_id, contents, start, end, STUDY_MODEL)


@dataclass(frozen=True)
class GlossSource:
    """The study data a word's gloss comes from, by its model."""

    model: str


# ------------------------------------------------------- treebank words


@dataclass(frozen=True)
class TreebankCredit:
    """
    How the reader credits a word's treebank: its tag ("Perseus", "GLAUx",
    "GLAUx auto"), the tag's tooltip, and whether GLAUx's parser rather than
    a person annotated the word's sentence.
    """

    label: str
    title: str
    automatic: bool = False


PERSEUS_CREDIT = TreebankCredit(
    "Perseus",
    "Ancient Greek Dependency Treebank 2.1, Perseus Digital Library (CC BY-SA 3.0)",
)
# GLAUx's credit, which About names too
GLAUX = "GLAUx corpus, KU Leuven (CC BY-SA 4.0)"


def treebank_credit(
    source: Optional[TreebankSource], annotation: str = "manual"
) -> TreebankCredit:
    """
    The credit for a word in a sentence annotated "manual" or "auto", for a
    text whose treebank is `source`. A text imported before its treebank
    was recorded is the Perseus treebank's.
    """
    if source is None or source.corpus != "glaux":
        return PERSEUS_CREDIT
    if annotation == "auto":
        return TreebankCredit(
            "GLAUx auto",
            f"{GLAUX}. This sentence was annotated by GLAUx's parser, which "
            "gets about 97% of lemmas and parses right, but only about 80% of "
            "the syntax.",
            automatic=True,
        )
    work = WORKS.get(source.work)
    hand = f", from {work.hand} (CC BY-NC-SA)" if work else ""
    return TreebankCredit(
        "GLAUx", f"{GLAUX}. This sentence was annotated by hand{hand}."
    )


def work_name(text: TextMeta, source: Optional[TreebankSource]) -> str:
    """How labels name a text: "the Iliad", "Herodotus" (else "the <title>")."""
    work = WORKS.get(source.work) if source else None
    return work.name if work else f"the {text.title}"


# ------------------------------------------------------------ what a text has


@dataclass
class TextStudy:
    """
    What a text has: its study data book by book (book_study) and whether it
    has treebank data. The Library's labels and book chips, and the reader's
    notes in a book without study data, which name the text as `name` does
    ("the Odyssey", "Herodotus").
    """

    name: str
    books: List[BookStudy]
    treebank: bool

    def book_at(self, line_number: int) -> Optional[BookStudy]:
        return next(
            (b for b in self.books if b.line_start <= line_number <= b.line_end),
            None,
        )

    def state_of(self, number: str) -> str:
        """A book's state ("translated", "partly", "none") by its number."""
        book = next((b for b in self.books if b.number == number), None)
        return book.state if book else "none"

    @property
    def state(self) -> str:
        """The text is "translated" when every book is, "none" when none has study data, else "partly"."""
        states = {b.state for b in self.books}
        if states == {"translated"}:
            return "translated"
        return "partly" if states - {"none"} else "none"

    @property
    def states(self) -> List[str]:
        """The book states the text has, in the key's order."""
        found = {b.state for b in self.books}
        return [s for s in ("translated", "partly", "none") if s in found]

    @property
    def label(self) -> str:
        """'Complete', 'Books 1–3 with translations', or what the text has without them."""
        state = self.state
        if state == "translated":
            return "Complete"
        if state == "none":
            if not self.treebank:
                return "Greek text only"
            return "Grammar and dictionary · translations coming"
        parts = []
        for which, words in (
            ("translated", "with translations"),
            ("partly", "in part"),
        ):
            numbers = [b.number for b in self.books if b.state == which]
            if not numbers:
                continue
            if numbers == [""]:  # a text without books, partly translated
                parts.append("Translations in part")
                continue
            noun = "Book" if len(numbers) == 1 else "Books"
            parts.append(f"{noun} {number_ranges(numbers)} {words}")
        return " · ".join(parts)

    def missing(self, line_number: int) -> Optional[str]:
        """
        What a note says has no study data yet, for a line: the text ("the
        Odyssey") when none of it has any, the line's book ("Book 5") when
        only that book has none; None when the book has some.
        """
        book = self.book_at(line_number)
        if book is not None and book.state != "none":
            return None
        if self.state == "none" or book is None or not book.number:
            return self.name
        return f"Book {book.number}"


def text_study(conn: sqlite3.Connection, text: TextMeta) -> TextStudy:
    """What a text has (TextStudy), from saved data."""
    return TextStudy(
        work_name(text, get_tb_source(conn, text.id)),
        book_study(conn, text.id, STUDY_MODEL),
        has_word_parses(conn, text.id, 1, text.line_count),
    )


@dataclass
class WordPart:
    """
    One treebank word: a whole word of the text, or one part of a word the
    treebank splits (οὐ of οὐδʼ), with its lemma's dictionary entry.
    """

    form: str
    lemma: Optional[str]
    pos: str
    morphology: Dict[str, str]
    source: str  # "treebank", or "model" for a parse the treebank lacks
    model_used: Optional[str]
    entry: Optional[LemmaEntry]

    @property
    def parsed(self) -> bool:
        return bool(self.lemma and self.pos)


@dataclass
class StudyWord:
    """A word of the text with everything saved about it, for any selection."""

    line_number: int
    index: int  # token position within its line
    token: str
    parts: List[WordPart]
    gloss: str = ""  # its meaning here, from the study data
    note: str = ""
    check: str = ""  # the study model's remark on the treebank parse
    studied: Optional[str] = None  # the study model, when gloss and note are its
    gloss_source: Optional[GlossSource] = None  # where `gloss` comes from
    # word details: role and use for the word strip and Grammar, the rest
    # for the word strip only (study_word)
    role: Optional[Role] = None  # its role in its treebank sentence
    frequency: Optional["Frequency"] = None  # how often its lemma occurs
    forms: Dict[str, LemmaForms] = field(default_factory=dict)  # by lemma
    use: Optional[WordUse] = None  # how it works in its sentence, a model's label
    credit: TreebankCredit = PERSEUS_CREDIT  # its treebank sentence's source
    dialect: str = ""  # its work's, which forms and principal parts prefer

    def other_dialect(self, form: Optional[LemmaForms]) -> str:
        """
        The dialect a saved form was made in ("Homeric"), when it isn't the
        work's own (a Herodotus word before Herodotus' forms are made); "".
        """
        if form is None or not form.dialect or form.dialect == self.dialect:
            return ""
        return DIALECTS.get(form.dialect, form.dialect)

    @property
    def form(self) -> str:
        return self.token.strip(_PUNCT_STRIP) or self.token

    @property
    def parsed(self) -> bool:
        return bool(self.parts) and all(p.parsed for p in self.parts)

    @property
    def lemma(self) -> str:
        """οὐδʼ → "οὐ + δέ"."""
        return " + ".join(p.lemma for p in self.parts if p.lemma)

    @property
    def pos(self) -> str:
        return " + ".join(p.pos for p in self.parts if p.pos)

    @property
    def parse_by(self) -> Optional[str]:
        """The model behind a parse the treebank lacks; None for the treebank's own."""
        if self.parsed:
            return next((p.model_used for p in self.parts if p.source == "model"), None)
        return None

    @property
    def parse_text(self) -> str:
        """The condensed parsing, from the treebank."""
        if self.parsed:
            return " + ".join(condensed_parse(p.pos, p.morphology) for p in self.parts)
        return ""

    @property
    def parse_summary(self) -> str:
        """The treebank parsing on one line: "2nd person · singular · present · …"."""
        return " + ".join(
            " · ".join(
                f"{value} person" if key == "Person" else value
                for key, value in parse_items(p.morphology)
            )
            or p.pos
            for p in self.parts
            if p.parsed
        )

    @property
    def dictionary(self) -> List[Tuple[str, Optional[LemmaEntry]]]:
        """(lemma, its entry) for each lemma of the word, once."""
        seen: Dict[str, Optional[LemmaEntry]] = {}
        for p in self.parts:
            if p.parsed and p.lemma not in seen:
                seen[p.lemma] = p.entry
        return list(seen.items())

    @property
    def headwords(self) -> Dict[str, LemmaForms]:
        """
        The saved dictionary forms of its noun and adjective lemmas, by lemma;
        a verb's row holds only its construction, and LSJ's headword stays.
        """
        return {lemma: f for lemma, f in self.forms.items() if f.pos != "verb"}

    @property
    def constructions(self) -> List[LemmaForms]:
        """The saved constructions of its verb lemmas ("+ dat., obey"), in order."""
        return [
            f
            for lemma, _ in self.dictionary
            for f in [self.forms.get(lemma)]
            if f is not None and f.pos == "verb" and f.construction
        ]

    @property
    def dictionary_form(self) -> str:
        """The saved dictionary form of a one-lemma word ("μῆνις, -ιος, ἡ"), or ""."""
        lemmas = [lemma for lemma, _ in self.dictionary]
        form = self.headwords.get(lemmas[0]) if len(lemmas) == 1 else None
        return form.headword if form else ""

    @property
    def lsj_entries(self) -> List[LexiconEntry]:
        """The full dictionary entries of its lemmas, once each."""
        return [e.entry for _, e in self.dictionary if e and e.entry]

    @property
    def short_defs(self) -> str:
        """
        The dictionary meanings of its lemmas: LSJ's, else a model's. A split
        word gives the first meaning of each part ("not + but").
        """
        defs = [
            e.entry.short_def if e.entry else e.model_def
            for _, e in self.dictionary
            if e and (e.entry or e.model_def)
        ]
        if len(defs) > 1:
            defs = [d.split(";")[0].strip() for d in defs]
        return " + ".join(defs)


# Principal parts as the word strip labels them, by part number
PART_LABELS = {
    1: "Present",
    2: "Future",
    3: "Aorist",
    4: "Perfect",
    5: "Perfect mid./pass.",
    6: "Aorist passive",
}


@dataclass
class PrincipalPart:
    name: str
    form: str
    note: str = ""


@dataclass
class PrincipalParts:
    """
    A verb's saved principal parts, for the word strip, and the number of the
    part the clicked form comes from (current; None when unknown).
    """

    verb: str
    model_used: str
    parts: List[PrincipalPart]
    current: Optional[int] = None
    other_dialect: str = ""  # the parts' dialect ("Homeric") if not the text's

    @property
    def summary(self) -> str:
        """ "ἀείδω, ἀείσομαι, ἤεισα, —, ᾖσμαι, ᾔσθην"."""
        return ", ".join(p.form for p in self.parts)


# The principal part a form comes from, by tense, then by voice: the part
# first tried, and the one tried next when that part is empty ("—"), as for a
# deponent whose aorist fills only one of the two aorist slots.
_PRESENT, _FUTURE, _AORIST, _PERFECT, _PERFECT_MP, _AORIST_PASSIVE = range(1, 7)
_SLOTS = {
    "present": {None: (_PRESENT, None)},
    "imperfect": {None: (_PRESENT, None)},
    "future": {"passive": (_AORIST_PASSIVE, _FUTURE), None: (_FUTURE, None)},
    "aorist": {"passive": (_AORIST_PASSIVE, _AORIST), None: (_AORIST, _AORIST_PASSIVE)},
    "perfect": {"active": (_PERFECT, _PERFECT_MP), None: (_PERFECT_MP, _PERFECT)},
    "pluperfect": {"active": (_PERFECT, _PERFECT_MP), None: (_PERFECT_MP, _PERFECT)},
    "future perfect": {None: (_PERFECT_MP, None)},
}


def principal_part_slot(
    morphology: Dict[str, str], parts: Sequence[PrincipalPart], lemma: str = ""
) -> Optional[int]:
    """
    The number (1–6) of the principal part a verb form comes from, from its
    tense and voice: present and imperfect from the present, the aorist
    passive from the sixth part, and so on. A middle form fills the slot its
    saved parts give it (a deponent's aorist is the third part or the sixth,
    whichever is filled). A suppletive lemma's form (εἶπον) whose slot is
    empty comes from the part that is the lemma. None when no part fits.
    """

    def filled(number: Optional[int]) -> bool:
        return (
            number is not None
            and number <= len(parts)
            and parts[number - 1].form.strip() not in ("", "—", "-")
        )

    slots = _SLOTS.get(morphology.get("tense", ""))
    if slots:
        voice = morphology.get("voice")
        for number in slots.get(voice, slots[None]):
            if filled(number):
                return number
    key = plain(lemma)
    for number, part in enumerate(parts, 1):
        if key and filled(number) and plain(part.form) == key:
            return number
    return None


@dataclass
class Frequency:
    """How often a lemma occurs in a text and in one of its books."""

    total: int
    in_book: int
    book: str
    title: str
    name: str = ""  # how labels name the text ("Herodotus"); else "the <title>"

    @property
    def only_here(self) -> bool:
        return self.total == 1

    @property
    def work(self) -> str:
        return self.name or f"the {self.title}"


def word_frequency(
    conn: sqlite3.Connection,
    text: TextMeta,
    line_number: int,
    lemma: str,
    name: str = "",
) -> Optional[Frequency]:
    """
    How many words of the text, and of the line's book, have the lemma; None
    for a word without a lemma or a line without a book. name is how labels
    name the text (work_name()).
    """
    span = book_span(conn, text.id, line_number) if lemma else None
    if span is None:
        return None
    book, first, last = span
    total, in_book = lemma_frequency(conn, text.id, lemma, first, last)
    return Frequency(total, in_book, book, text.title, name) if total else None


def saved_principal_parts(
    conn: sqlite3.Connection,
    verb: str,
    morphology: Optional[Dict[str, str]] = None,
    dialect: Optional[str] = None,
) -> Optional[PrincipalParts]:
    """
    A verb's saved principal parts: the study model's in the text's dialect
    (made in a batch), else its parts in another dialect, else the newest by
    any model. With a form's morphology, current is the part the form comes
    from.
    """
    if not verb:
        return None
    saved = get_verb_parts(conn, verb, STUDY_MODEL, dialect) if dialect else None
    if saved is None:
        saved = get_verb_parts(conn, verb, STUDY_MODEL)
    if saved is None:
        others = list_verb_parts(conn, verb)
        saved = others[0] if others else None
    if saved is None or not saved.parts:
        return None
    parts = []
    for n, p in enumerate(saved.parts, 1):
        try:
            number = int(p.get("part"))
        except (TypeError, ValueError):
            number = n
        parts.append(
            PrincipalPart(
                name=PART_LABELS.get(number) or str(p.get("name") or ""),
                form=str(p.get("form") or "—"),
                note=str(p.get("notes") or ""),
            )
        )
    current = principal_part_slot(morphology, parts, verb) if morphology else None
    other = ""
    if dialect and saved.dialect and saved.dialect != dialect:
        other = DIALECTS.get(saved.dialect, saved.dialect)
    return PrincipalParts(saved.verb, saved.model_used, parts, current, other)


@dataclass
class StudyLine:
    line: Line
    words: List[StudyWord]


@dataclass
class WordsSources:
    """Where the meanings, notes and parsings of a Grammar table come from."""

    glosses: List[GlossSource]  # the saved meanings', in order of first use
    lsj: bool  # some meanings are LSJ's short definitions (shown in grey)
    defined_by: List[str]  # models whose definitions stand in for LSJ's
    parsed_by: List[str]  # models that parsed words the treebank lacks
    credits: List[TreebankCredit] = field(default_factory=list)  # the treebank's
    noted_by: List[str] = field(default_factory=list)  # models of the notes

    @property
    def treebank(self) -> Optional[TreebankCredit]:
        """
        The treebank's tag for the legend: its one credit, or for a GLAUx
        selection with sentences its parser annotated, plain GLAUx (those
        words then carry GLAUx auto); None when no word has its parse.
        """
        if not self.credits:
            return None
        return next((c for c in self.credits if not c.automatic), self.credits[0])


def words_sources(study: Sequence[StudyLine]) -> WordsSources:
    glosses: List[GlossSource] = []
    defined_by: List[str] = []
    parsed_by: List[str] = []
    noted_by: List[str] = []
    credits: List[TreebankCredit] = []
    lsj = False
    for w in (w for sl in study for w in sl.words):
        if w.parsed and w.parse_by is None and w.credit not in credits:
            credits.append(w.credit)
        if w.gloss:
            if w.gloss_source and w.gloss_source not in glosses:
                glosses.append(w.gloss_source)
        elif w.short_defs:
            for _, e in w.dictionary:
                if e and e.entry:
                    lsj = True
                elif e and e.model_def and e.model_used not in defined_by:
                    defined_by.append(e.model_used)
        if w.note and w.studied and w.studied not in noted_by:
            noted_by.append(w.studied)
        if w.parse_text and w.parse_by and w.parse_by not in parsed_by:
            parsed_by.append(w.parse_by)
    return WordsSources(glosses, lsj, defined_by, parsed_by, credits, noted_by)


@dataclass(frozen=True)
class LegendItem:
    """
    One source in Grammar's legend and what it gives the words ("meanings,
    notes, uses"): a study model's (tagged AI), a treebank's (its credit),
    or with neither, LSJ's.
    """

    gives: str
    model: Optional[str] = None
    credit: Optional[TreebankCredit] = None


def _parts(
    conn: sqlite3.Connection, parses: Sequence[WordParse], with_html: bool
) -> Dict[Tuple[int, int], List[WordPart]]:
    """(line, token) → its WordParts, with dictionary entries looked up."""
    entries = get_lemma_entries(
        conn, [p.lemma for p in parses if p.has_parse], with_html=with_html
    )
    parts: Dict[Tuple[int, int], List[WordPart]] = defaultdict(list)
    for p in parses:
        pos, morphology = decode(p.postag) if p.has_parse else ("", {})
        parts[(p.line_number, p.token_index)].append(
            WordPart(
                form=p.form,
                lemma=p.lemma,
                pos=pos,
                morphology=morphology,
                source=p.source,
                model_used=p.model_used,
                entry=entries.get(p.lemma) if p.has_parse else None,
            )
        )
    return parts


def sentence_syntax(
    conn: sqlite3.Connection, text_id: int, sentence_id: int
) -> SentenceSyntax:
    """
    A treebank sentence's tree, with where each node's word is in the text.
    A node the treebank left untagged takes a model's parse of its word.
    """
    nodes = list_sentence_nodes(conn, text_id, sentence_id)
    positions = node_positions(conn, text_id, sentence_id)
    numbers = [n for n, _ in positions.values()]
    if not numbers:
        return SentenceSyntax(nodes, {})
    first, last = min(numbers), max(numbers)
    lines = {ln.line_number: ln for ln in get_lines(conn, text_id, first, last)}
    parses = [
        p
        for p in list_word_parses(conn, text_id, first, last)
        if p.sentence_id == sentence_id
    ]
    return build_syntax(nodes, parses, lines)


def word_role(conn: sqlite3.Connection, parses: Sequence[WordParse]) -> Optional[Role]:
    """
    A word's role in its treebank sentence, from its parses (the parts of a
    split word in order: the first part with a role); None for a word
    without a treebank node or a role.
    """
    for p in parses:
        if p.sentence_id is None or p.node_id is None:
            continue
        role = sentence_syntax(conn, p.text_id, p.sentence_id).role(p.node_id)
        if role is not None:
            return role
    return None


def _credits(
    conn: sqlite3.Connection,
    source: Optional[TreebankSource],
    parses: Sequence[WordParse],
) -> Dict[Tuple[int, int], TreebankCredit]:
    """
    (line, token) → the credit for the word's treebank sentence (its first
    part's with a sentence); words without one are left out.
    """
    if source is None or source.corpus != "glaux":
        return {
            (p.line_number, p.token_index): PERSEUS_CREDIT
            for p in parses
            if p.sentence_id is not None
        }
    annotations = sentence_annotations(
        conn,
        source.text_id,
        [p.sentence_id for p in parses if p.sentence_id is not None],
    )
    found: Dict[Tuple[int, int], TreebankCredit] = {}
    for p in parses:
        if p.sentence_id is not None:
            found.setdefault(
                (p.line_number, p.token_index),
                treebank_credit(source, annotations.get(p.sentence_id, "manual")),
            )
    return found


def _with_study(word: StudyWord, studied: Optional[WordStudy]) -> StudyWord:
    """The word with the study data's gloss, note and parse remark."""
    if studied is not None:
        word.gloss, word.note = studied.gloss, studied.note
        word.check = studied.parse_check or ""
        word.studied = studied.model_used
        word.gloss_source = GlossSource(studied.model_used)
    return word


def study_word(
    conn: sqlite3.Connection, text: TextMeta, line_number: int, index: int, token: str
) -> Optional[StudyWord]:
    """
    One word with its treebank parse, full dictionary entries and study
    data, for the word strip, with its role in the sentence, its lemma's
    frequency (a word the treebank splits has none), its lemmas' saved
    dictionary forms and constructions, and its saved use label; None for a
    text without treebank data.
    """
    parses = [
        p
        for p in list_word_parses(conn, text.id, line_number, line_number)
        if p.token_index == index
    ]
    if not parses:
        return None
    parts = _parts(conn, parses, with_html=True)
    word = StudyWord(line_number, index, token, parts[(line_number, index)])
    source = get_tb_source(conn, text.id)
    word.credit = _credits(conn, source, parses).get(
        (line_number, index), PERSEUS_CREDIT
    )
    word.dialect = work_profile(source.work if source else None).dialect
    word.role = word_role(conn, parses)
    if len(parses) == 1 and parses[0].lemma:
        word.frequency = word_frequency(
            conn, text, line_number, parses[0].lemma, work_name(text, source)
        )
    word.forms = get_lemma_forms(
        conn, [lemma for lemma, _ in word.dictionary], STUDY_MODEL, word.dialect
    )
    word.use = get_word_use(conn, text.id, line_number, index, STUDY_MODEL)
    studied = word_study_at(conn, text.id, line_number, line_number)
    return _with_study(word, studied.get((line_number, index)))


def study_lines(
    conn: sqlite3.Connection,
    text_id: int,
    lines: Sequence[Line],
    whole: bool = False,
) -> Tuple[List[StudyLine], int]:
    """
    Every word of the lines with its treebank parse, short dictionary
    meanings, and the study data's meaning and note, grouped by line; and
    how many words were left out to keep a long selection to MAX_STUDY_WORDS
    (none when whole). No lines for a text without treebank data.
    """
    if not lines:
        return [], 0
    kept, total = [], 0
    for ln in lines:
        count = len(tokens(ln.content))
        if kept and not whole and total + count > MAX_STUDY_WORDS:
            break
        kept.append(ln)
        total += count
    left_out = sum(len(tokens(ln.content)) for ln in lines[len(kept) :])
    parses = list_word_parses(conn, text_id, kept[0].line_number, kept[-1].line_number)
    if not parses:
        return [], 0
    parts = _parts(conn, parses, with_html=False)
    credits = _credits(conn, get_tb_source(conn, text_id), parses)
    studied = word_study_at(conn, text_id, kept[0].line_number, kept[-1].line_number)
    result = []
    for ln in kept:
        words = []
        for i, token in enumerate(tokens(ln.content)):
            word = StudyWord(
                line_number=ln.line_number,
                index=i,
                token=token,
                parts=parts.get((ln.line_number, i), []),
                credit=credits.get((ln.line_number, i), PERSEUS_CREDIT),
            )
            words.append(_with_study(word, studied.get((ln.line_number, i))))
        result.append(StudyLine(ln, words))
    return result, left_out


# --------------------------------------------------------------- passage box


@dataclass
class FluentUnit:
    """The study data's fluent translation of a translation unit, with its range."""

    ref_start: str
    ref_end: str
    fluent: str


@dataclass
class LiteralLine:
    """The study data's literal translation of a line."""

    line: Line
    literal: str


@dataclass
class SentenceOutline:
    """
    A treebank sentence the selection touches, for Grammar's Structure
    view: its range, its treebank's credit, its words as printed (each with
    whether it is in the selection), its outline, and the keys of the rows
    whose words are all outside the selection (dimmed).
    """

    sentence_id: int
    ref_start: str
    ref_end: str
    credit: TreebankCredit
    text: List[Tuple[str, bool]]
    rows: List[OutlineRow]
    outside: Set[int] = field(default_factory=set)


@dataclass
class PassageView:
    """Everything the passage box needs for one selection."""

    text: TextMeta
    start: int
    end: int
    ref_start: str
    ref_end: str
    lines: List[Line]
    state: str  # "study" when the study data translates every line, else "none"
    study: List[StudyLine] = field(
        default_factory=list
    )  # every word, from the treebank
    study_left_out: int = 0  # words of a long selection not in `study`
    # The study data (state "study"): fluent translations of the units
    # overlapping the selection and a literal translation of every line
    fluent_units: List[FluentUnit] = field(default_factory=list)
    literal_lines: List[LiteralLine] = field(default_factory=list)
    study_model: Optional[str] = None
    # The structure of the treebank sentences `study` touches (Structure),
    # and the models whose use labels Grammar shows
    outline: List[SentenceOutline] = field(default_factory=list)
    use_models: List[str] = field(default_factory=list)
    # State "none" in a book without study data: what the Fluent and
    # Literal tabs say has no translations yet ("the Odyssey", "Book 5")
    missing: Optional[str] = None

    @property
    def lines_arg(self) -> str:
        return f"{self.start}-{self.end}"

    @property
    def study_sources(self) -> WordsSources:
        """Where the Grammar table's meanings and parsings come from."""
        return words_sources(self.study)

    @property
    def legend(self) -> List[LegendItem]:
        """
        Grammar's sources, only those its words have: "AI meanings, notes,
        uses · Perseus parsing, roles · LSJ meanings in grey". A parse the
        treebank lacks carries its own AI tag in its row, as does GLAUx auto
        on a word its parser annotated when the legend says GLAUx.
        """
        sources = self.study_sources
        made: Dict[str, List[str]] = {}  # model → what it gives
        for model, gives in (
            *((s.model, "meanings") for s in sources.glosses),
            *((m, "meanings LSJ lacks") for m in sources.defined_by),
            *((m, "notes") for m in sources.noted_by),
            *((m, "uses") for m in self.use_models),
        ):
            given = made.setdefault(model, [])
            if gives not in given and not (
                gives.startswith("meanings ") and "meanings" in given
            ):
                given.append(gives)
        items = [LegendItem(", ".join(g), model=m) for m, g in made.items()]
        if sources.treebank:
            gives = "parsing, roles" if self.outline else "parsing"
            items.append(LegendItem(gives, credit=sources.treebank))
        if sources.lsj:
            grey = "meanings in grey" if sources.glosses else "meanings"
            items.append(LegendItem(grey))
        return items

    @property
    def tabbed(self) -> bool:
        """Fluent / Literal / Grammar tabs: for saved translations or treebank words."""
        return self.has_literal or bool(self.study)

    @property
    def has_literal(self) -> bool:
        """The study data's translations to show."""
        return self.state == "study"

    @property
    def literal_tab(self) -> bool:
        """Literal is a tab: for the translations, or to say there are none yet."""
        return self.has_literal or bool(self.missing and self.study)

    @property
    def word_total(self) -> int:
        """The number in "Grammar (N)"."""
        return sum(len(sl.words) for sl in self.study) + self.study_left_out


def study_passage(
    conn: sqlite3.Connection, text_id: int, lines: Sequence[Line]
) -> Optional[Tuple[List[FluentUnit], List[LiteralLine]]]:
    """
    The study data's fluent translations of the translation units that
    overlap the lines, and the literal translation of each line; None
    unless every line has both.
    """
    start, end = lines[0].line_number, lines[-1].line_number
    literal = {
        t.line_number: t.literal
        for t in list_line_translations(conn, text_id, start, end, STUDY_MODEL)
    }
    units = list_translation_units(conn, text_id, start, end, STUDY_MODEL)
    covered = {n for u in units for n in range(u.first_line, u.last_line + 1)}
    if any(
        ln.line_number not in literal or ln.line_number not in covered for ln in lines
    ):
        return None
    refs = references(
        conn, text_id, [n for u in units for n in (u.first_line, u.last_line)]
    )
    return (
        [FluentUnit(refs[u.first_line], refs[u.last_line], u.fluent) for u in units],
        [LiteralLine(ln, literal[ln.line_number]) for ln in lines],
    )


def passage_view(
    conn: sqlite3.Connection, text: TextMeta, start: int, end: int
) -> Optional[PassageView]:
    """
    The passage box for lines [start, end]: the study data's translations
    when they cover every line, and the treebank's words with the study
    data's meanings and notes. None if the range has no lines.
    """
    lines = get_lines(conn, text.id, start, end)
    if not lines:
        return None
    start, end = lines[0].line_number, lines[-1].line_number
    view = PassageView(
        text=text,
        start=start,
        end=end,
        ref_start=ref(lines[0]),
        ref_end=ref(lines[-1]),
        lines=lines,
        state="none",
    )
    study = study_passage(conn, text.id, lines)
    if study:
        view.state = "study"
        view.fluent_units, view.literal_lines = study
        view.study_model = STUDY_MODEL
    else:
        view.missing = text_study(conn, text).missing(start)
    if has_word_parses(conn, text.id, start, end):
        view.study, view.study_left_out = study_lines(conn, text.id, lines)
    _add_structure(conn, view)
    return view


def _add_structure(conn: sqlite3.Connection, view: PassageView) -> None:
    """
    The structure of a passage's treebank words, read from the saved trees:
    each word's role and use label (Grammar, In order), and the outline of
    every treebank sentence its words touch (Structure). A sentence shows
    whole; its words outside the selection get the meanings Grammar would
    give them.
    """
    if not view.study:
        return
    text_id = view.text.id
    start, end = view.study[0].line.line_number, view.study[-1].line.line_number
    sentences = list_tb_sentences_between(conn, text_id, start, end)
    if not sentences:
        return
    lo = min([start] + [s.first_line for s in sentences])
    hi = max([end] + [s.last_line for s in sentences])
    ids = {s.sentence_id for s in sentences}
    lines = {ln.line_number: ln for ln in get_lines(conn, text_id, lo, hi)}
    parses = [
        p for p in list_word_parses(conn, text_id, lo, hi) if p.sentence_id in ids
    ]
    trees = text_syntax(list_tb_nodes(conn, text_id, ids), parses, lines)
    uses = saved_word_uses(conn, text_id, lo, hi, STUDY_MODEL)

    words = {(w.line_number, w.index): w for sl in view.study for w in sl.words}
    parts: Dict[Tuple[int, int], List[WordParse]] = defaultdict(list)
    for p in parses:
        parts[(p.line_number, p.token_index)].append(p)
    shown_uses: List[WordUse] = []
    for place, w in words.items():
        w.use = uses.get(place)
        if w.use:
            shown_uses.append(w.use)
        for p in parts.get(place, []):  # a split word: its first part with a role
            tree = trees.get(p.sentence_id)
            w.role = tree.role(p.node_id) if tree and p.node_id is not None else None
            if w.role:
                break

    words.update(_words_around(conn, view, lines, start, end))
    source = get_tb_source(conn, text_id)
    for s in sentences:
        tree = trees.get(s.sentence_id)
        rows = tree.outline() if tree and tree.words else []
        if not rows:
            continue
        outline = SentenceOutline(
            sentence_id=s.sentence_id,
            ref_start=ref(lines[s.first_line]),
            ref_end=ref(lines[s.last_line]),
            credit=treebank_credit(source, s.annotation),
            text=_sentence_text(tree, lines, view.start, view.end),
            rows=rows,
        )
        for row in (r for top in rows for r in (top, *top.descendants())):
            row.gloss, row.lsj = _row_gloss(row, words)
            use = uses.get(row.word.place) if row.word else None
            if use:
                row.use = use.use_label
                shown_uses.append(use)
            places = row.places
            if places and all(not view.start <= n <= view.end for n, _ in places):
                outline.outside.add(row.key)
        view.outline.append(outline)
    view.use_models = list(dict.fromkeys(u.model_used for u in shown_uses))


def _words_around(
    conn: sqlite3.Connection,
    view: PassageView,
    lines: Dict[int, Line],
    start: int,
    end: int,
) -> Dict[Tuple[int, int], StudyWord]:
    """The words of the lines outside start–end, with their saved meanings."""
    found: Dict[Tuple[int, int], StudyWord] = {}
    for lo, hi in ((min(lines), start - 1), (end + 1, max(lines))):
        around = [lines[n] for n in range(lo, hi + 1) if n in lines]
        if not around:
            continue
        study, _ = study_lines(conn, view.text.id, around, whole=True)
        found.update({(w.line_number, w.index): w for sl in study for w in sl.words})
    return found


def _sentence_text(
    tree: SentenceSyntax, lines: Dict[int, Line], start: int, end: int
) -> List[Tuple[str, bool]]:
    """
    A sentence's words as printed, from its first word to its last, each
    with whether its line is in the selection start–end.
    """
    first = min((n, i) for n, i, _, _ in tree.words.values())
    last = max((n, i) for n, i, _, _ in tree.words.values())
    text = []
    for n in range(first[0], last[0] + 1):
        if n not in lines:
            continue
        for i, token in enumerate(tokens(lines[n].content)):
            if first <= (n, i) <= last:
                text.append((token, start <= n <= end))
    return text


def _dictionary_sense(definition: str) -> str:
    """
    A dictionary meaning's first sense ("from out of" of "from out of; from;
    …"), or "" for a cross-reference ("Σικελίδας l.c.]").
    """
    if any("\u0370" <= ch <= "\u1fff" or ch in "[]" for ch in definition):
        return ""
    return definition.split(";")[0].split(",")[0].strip()


def _row_gloss(
    row: OutlineRow, words: Dict[Tuple[int, int], StudyWord]
) -> Tuple[str, bool]:
    """
    An outline row's meaning: its words' saved glosses ("from" and "which"
    of ἐξ οὗ), else the dictionary's first sense; "the" for the article,
    whose LSJ entry begins "the following". lsj when the meaning shown is
    only a dictionary's (grey).
    """
    found, lsj = [], False
    for place in row.places:
        w = words.get(place)
        if w is None:
            continue
        if w.gloss:
            found.append(w.gloss)
            continue
        if len(w.parts) == 1 and w.parts[0].pos == "article":
            sense = "the"
        else:
            sense = _dictionary_sense(w.short_defs)
        if sense:
            found.append(sense)
            lsj = True
    return " ".join(dict.fromkeys(found)), lsj and len(found) == 1


# -------------------------------------------------------------------- search


@dataclass
class SearchHit:
    line: Line
    ref: str
    book: Optional[str]  # the part of the reference before the first dot
    pieces: List[Tuple[str, bool]]  # the line's text as (text, matched)


@dataclass
class SearchView:
    """Search results shown in the text pane in place of the lines."""

    query: str
    hits: List[SearchHit]
    total: int


def search_view(conn: sqlite3.Connection, text_id: int, query: str) -> SearchView:
    query = query.strip()
    lines, total = search_lines(conn, text_id, query, SEARCH_LIMIT)
    hits = [
        SearchHit(
            line=ln,
            ref=ref(ln),
            book=ln.reference.split(".")[0]
            if ln.reference and "." in ln.reference
            else None,
            pieces=excerpt(highlight(ln.content, query)),
        )
        for ln in lines
    ]
    return SearchView(query=query, hits=hits, total=total)
