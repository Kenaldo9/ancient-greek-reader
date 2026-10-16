from dataclasses import dataclass
from typing import List, Optional


@dataclass
class WordParse:
    """
    The parse of one word of a text, or of one part of it: a word the
    treebank splits (οὐδʼ = οὐ + δ̓) has a row per part. `token_index` counts
    the line's whitespace tokens from 0. A word without a treebank word has
    sentence_id None; a missing parse has lemma or postag None until a model
    supplies one (source "model"). node_id is its treebank node in the
    sentence, None for a word without one.
    """

    text_id: int
    line_number: int
    token_index: int
    part: int
    form: str
    lemma: Optional[str]
    postag: Optional[str]
    sentence_id: Optional[int]
    source: str = "treebank"
    model_used: Optional[str] = None
    node_id: Optional[int] = None

    @property
    def has_parse(self) -> bool:
        return bool(self.lemma and self.postag)


@dataclass
class TreebankSentence:
    """
    A treebank sentence as a range of the text's sequential line numbers,
    annotated by hand ("manual") or by a parser ("auto", in GLAUx).
    """

    text_id: int
    sentence_id: int
    first_line: int
    last_line: int
    annotation: str = "manual"


@dataclass
class TreebankSource:
    """
    Which treebank a text's words come from: its work key ("iliad",
    "herodotus"), its corpus ("perseus" or "glaux"), the file and its licence.
    """

    text_id: int
    work: str
    corpus: str
    filename: str
    licence: str
    imported_at: Optional[str] = None


@dataclass
class TreebankNode:
    """
    A node of a treebank sentence: a word, a punctuation mark, or an elliptic
    node for an understood word (artificial, form "[0]"). head is the node_id
    of the node it depends on, 0 for the sentence's root; relation is the
    treebank's (AGDT 2.1), e.g. "OBJ" or "PRED_CO".
    """

    text_id: int
    sentence_id: int
    node_id: int
    head: int
    relation: str
    form: str
    lemma: Optional[str] = None
    postag: Optional[str] = None
    artificial: bool = False


@dataclass
class LexiconEntry:
    """A dictionary entry: LSJ's, with its Beta Code key ("ba/llw")."""

    source: str
    key: str
    headword: str
    short_def: str
    entry_html: str
    id: Optional[int] = None


@dataclass
class LemmaEntry:
    """
    A lemma's dictionary entry and how it was found (matched_by), or a
    model's short identification when the dictionary lacks the lemma.
    """

    lemma: str
    entry: Optional[LexiconEntry] = None
    matched_by: Optional[str] = None
    model_def: Optional[str] = None
    model_used: Optional[str] = None


@dataclass
class TranslationUnit:
    """
    Consecutive whole treebank sentences, up to 10 lines (a longer sentence
    is a unit of its own): the span of one batch request and of one fluent
    translation, which a model's result fills in.
    """

    text_id: int
    first_line: int
    last_line: int
    fluent: Optional[str] = None
    model_used: Optional[str] = None

    @property
    def line_count(self) -> int:
        return self.last_line - self.first_line + 1


@dataclass
class LineTranslation:
    """A model's literal translation of one line."""

    text_id: int
    line_number: int
    literal: str
    model_used: str


@dataclass
class BookStudy:
    """
    One book's study data by a model, counted by line: the book's lines
    (line_start–line_end), those with a literal translation, those inside a
    saved translation unit, and the words with a gloss. A unit's results are
    saved together (save_study_result: its fluent translation, every line's
    literal one, every word's gloss and note), so a book whose every line is
    in a saved unit is complete as `greek study coverage` counts it.
    """

    number: str  # the reference's first part; "" for a text without books
    line_start: int
    line_end: int
    lines: int
    literal: int = 0
    translated: int = 0
    glossed: int = 0

    @property
    def state(self) -> str:
        """The book is "translated" (every line), "partly" (some study data) or "none"."""
        if self.lines and self.translated == self.lines == self.literal:
            return "translated"
        if self.translated or self.literal or self.glossed:
            return "partly"
        return "none"


@dataclass
class WordStudy:
    """
    A model's contextual gloss and note for one word (token_index counts the
    line's whitespace tokens from 0), and its remark when it disagrees with
    the treebank's parse. note_version is the note prompt that wrote the note:
    1 for the Phase 5 rule, 2 for the revised one.
    """

    text_id: int
    line_number: int
    token_index: int
    gloss: str
    note: str
    parse_check: Optional[str]
    model_used: str
    note_version: int = 1


@dataclass
class StudyBatch:
    """
    A submitted Message Batch: its API id, what it covers, its processing
    status as last seen, and its token usage and cost once collected. Its
    kind is "study" (glosses, notes and translations), "parts" (principal
    parts of verbs), "notes" (notes only, with the revised note prompt),
    "forms" (dictionary forms of lemmas) or "uses" (use in context). A
    pilot's results are only written to a review file. A forms or parts
    batch is made for a dialect, which its results are stored under.
    """

    id: str
    text_id: Optional[int]
    scope: str
    model: str
    status: str
    request_count: int
    submitted_at: str = ""
    ended_at: Optional[str] = None
    collected_at: Optional[str] = None
    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    kind: str = "study"
    effort: str = "medium"
    pilot: bool = False
    dialect: Optional[str] = None  # a forms or parts batch's


@dataclass
class StudyRequest:
    """One request of a batch: the lines it covers and, once collected, its outcome."""

    batch_id: str
    custom_id: str
    first_line: int
    last_line: int
    word_count: int
    status: str = "pending"  # pending, succeeded or failed
    failure: Optional[str] = None
    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class PartsRequest:
    """
    One request of a principal-parts batch: the verbs (treebank lemmas) it
    asked for, in order, and once collected how many were stored and why the
    others failed.
    """

    batch_id: str
    custom_id: str
    verbs: List[str]
    stored_count: int = 0
    status: str = "pending"  # pending, succeeded (all stored) or failed
    failure: Optional[str] = None
    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class LemmaRequest:
    """
    One request of a dictionary-forms batch: the lemmas it asked for, in
    order, and once collected how many were stored and why the others failed.
    """

    batch_id: str
    custom_id: str
    lemmas: List[str]
    stored_count: int = 0
    status: str = "pending"  # pending, succeeded (all stored) or failed
    failure: Optional[str] = None
    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class LemmaForms:
    """
    A model's dictionary form of a lemma in a dialect ("μῆνις, -ιος, ἡ",
    Homeric), a note on its forms in that dialect, an adjective's irregular
    comparison and a verb's construction ("" when there is nothing to say;
    construction None until asked for).
    """

    lemma: str
    model_used: str
    pos: str
    headword: str
    forms: str = ""
    comparison: str = ""
    construction: Optional[str] = None
    dialect: str = "homeric"


@dataclass
class WordUse:
    """
    A model's label for how a word works in its sentence ("dative of
    disadvantage: for the Achaeans"), made at an effort; extra when the
    request didn't mark the word and the model labelled it anyway.
    """

    text_id: int
    line_number: int
    token_index: int
    model_used: str
    use_label: str
    effort: str
    extra: bool = False


@dataclass
class Usage:
    """Token usage of one request or a batch, and its cost in US dollars."""

    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.output_tokens + other.output_tokens,
            self.cost_usd + other.cost_usd,
        )
