"""
Treebanks: download a work's treebank, parse its words, and align them with
an imported text, so each word of the text gets the treebank's lemma and
morphology. Two corpora are read: the Perseus Ancient Greek Dependency
Treebank (AGDT 2.1, CC BY-SA 3.0: the Iliad and the Odyssey) and GLAUx
(CC BY-SA 4.0: Herodotus and Thucydides), which uses the same format and
relations with a few differences:

- a word cites its book in div_book (div_section is the app's prose
  reference, "1.1.1"), not in cite;
- each sentence says whether it was annotated by hand (analysis="manual")
  or by GLAUx's parser ("auto");
- coordinating conjunctions have the part of speech "b", and elliptic nodes
  "z" with the form and lemma "E" (read here as Perseus's "[0]");
- its Greek is NFD (read here as NFC, as everything else).

The treebank and the app's text are the same edition, but they differ in a
few places: the treebank splits some words (app οὐδʼ = treebank οὐ + δ̓), omits
a few bracketed lines, and has a couple of variant readings. Citations are
sometimes a line off, so words are aligned per book, not per line.

Every node of the treebank is also read: words, punctuation and the elliptic
nodes it adds for understood words, with the head each depends on and its
relation, from which greek/syntax.py reads a word's role.
"""

import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from greek.display.analysis import _PUNCT_STRIP
from greek.fetch.catalog import fetch_url
from greek.models import Line, TreebankNode, TreebankSentence, WordParse

TREEBANK_BASE = (
    "https://raw.githubusercontent.com/PerseusDL/treebank_data/master/v2.1/Greek/texts"
)
GLAUX_BASE = "https://raw.githubusercontent.com/alekkeersmaekers/glaux/main/xml"


@dataclass(frozen=True)
class TreebankWork:
    """
    A treebank the app can align with a text: its corpus ("perseus" or
    "glaux"), its file, its licence, how labels name the work ("12× in the
    Iliad", "12× in Herodotus") and who annotated its sentences by hand.
    """

    key: str
    corpus: str
    filename: str
    name: str
    licence: str
    hand: str

    @property
    def url(self) -> str:
        base = TREEBANK_BASE if self.corpus == "perseus" else GLAUX_BASE
        return f"{base}/{self.filename}"

    @property
    def local_name(self) -> str:
        """The download's name in data/sources/ (GLAUx's files are numbered)."""
        return self.filename if self.corpus == "perseus" else f"glaux-{self.filename}"


_PERSEUS = "CC BY-SA 3.0"
WORKS: Mapping[str, TreebankWork] = {
    w.key: w
    for w in (
        TreebankWork(
            "iliad",
            "perseus",
            "tlg0012.tlg001.perseus-grc1.tb.xml",
            "the Iliad",
            _PERSEUS,
            "the Perseus treebank",
        ),
        TreebankWork(
            "odyssey",
            "perseus",
            "tlg0012.tlg002.perseus-grc1.tb.xml",
            "the Odyssey",
            _PERSEUS,
            "the Perseus treebank",
        ),
        TreebankWork(
            "herodotus",
            "glaux",
            "0016-001.xml",
            "Herodotus",
            "CC BY-SA 4.0; PROIEL annotation CC BY-NC-SA",
            "the PROIEL treebank",
        ),
        TreebankWork(
            "thucydides",
            "glaux",
            "0003-001.xml",
            "Thucydides",
            "CC BY-SA 4.0; Gorman annotation CC BY-NC-SA",
            "Vanessa Gorman's prose treebanks",
        ),
    )
}
TREEBANK_FILES = {key: w.filename for key, w in WORKS.items()}

# Below this share of aligned words the treebank is for a different text, and
# the import stops before writing anything. The Iliad aligns 99.97% of its
# words with its treebank; the Odyssey aligns 3.3% with the Iliad's.
MIN_ALIGNED_SHARE = 0.8
# Largest run of differing words (app words × treebank words) to align word by
# word; the Iliad's longest is 22 × 0 (three lines the treebank omits).
_MAX_RUN_CELLS = 10_000


@dataclass
class TbWord:
    """A word of the treebank (punctuation and placeholders are skipped)."""

    sentence_id: int
    book: str
    form: str
    lemma: Optional[str]
    postag: Optional[str]
    node_id: Optional[int] = None


@dataclass
class Treebank:
    """
    A treebank's words (for alignment) and all of its nodes, in document
    order. The nodes' text_id is 0 until they are saved for a text.
    """

    words: List[TbWord] = field(default_factory=list)
    nodes: List[TreebankNode] = field(default_factory=list)
    # sentence_id → "auto" for a sentence GLAUx's parser annotated; every
    # other sentence was annotated by hand
    auto: Dict[int, str] = field(default_factory=dict)

    def annotation(self, sentence_id: int) -> str:
        return self.auto.get(sentence_id, "manual")


@dataclass
class Alignment:
    """Rows to store for a text, and counts for the import report."""

    parses: List[WordParse] = field(default_factory=list)
    sentences: List[TreebankSentence] = field(default_factory=list)
    words: int = 0  # words (whitespace tokens) of the text
    identical: int = 0  # words matching one treebank word
    split: int = 0  # words matching several treebank words
    unmatched: List[Tuple[str, str]] = field(default_factory=list)  # (ref, word)
    untagged: List[Tuple[str, str]] = field(default_factory=list)  # (ref, form)
    unused: int = 0  # treebank words not aligned to any word of the text

    @property
    def aligned_share(self) -> float:
        return (self.identical + self.split) / self.words if self.words else 0.0


def bare(s: str) -> str:
    """
    Letters only, lowercased: no accents, breathings, elision marks or
    punctuation. Modifier letters (category Lm) are dropped too, because the
    app writes elision with ʼ (U+02BC), which is one.
    """
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(ch for ch in s if unicodedata.category(ch) in ("Ll", "Lu", "Lo"))


def download_treebank(work: str, sources_dir: Path, refresh: bool = False) -> Path:
    """
    The local copy of a work's treebank in sources_dir, downloaded first if
    it is missing or refresh is set. Raises urllib.error.URLError on network
    failure.
    """
    w = WORKS[work]
    dest = sources_dir / w.local_name
    if refresh or not dest.exists():
        sources_dir.mkdir(parents=True, exist_ok=True)
        data = fetch_url(w.url, timeout=120)
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(data)
        partial.replace(dest)
    return dest


def _nfc(value: Optional[str]) -> Optional[str]:
    return unicodedata.normalize("NFC", value) if value else None


def read_treebank(path: Path) -> Treebank:
    """
    The treebank's words and nodes in document order, from a Perseus or a
    GLAUx file. Words leave out punctuation (pos "u") and the elliptic
    placeholder nodes for omitted words (no citation in Perseus, pos "z" in
    GLAUx); nodes keep every one. An empty lemma, or a postag of dashes
    only, is None.
    """
    treebank = Treebank()
    for sentence in ET.parse(path).getroot().iter("sentence"):
        sentence_id = int(sentence.get("id"))
        if sentence.get("analysis") == "auto":
            treebank.auto[sentence_id] = "auto"
        for w in sentence.iter("word"):
            node_id = int(w.get("id"))
            form = _nfc(w.get("form")) or ""
            lemma = _nfc(w.get("lemma"))
            postag = w.get("postag") or ""
            postag = postag if postag.strip("-") else None
            if (postag or "").startswith("z"):  # GLAUx's elliptic node, "E"
                form, lemma, postag = "[0]", None, None
            treebank.nodes.append(
                TreebankNode(
                    text_id=0,
                    sentence_id=sentence_id,
                    node_id=node_id,
                    head=int(w.get("head") or 0),
                    relation=w.get("relation") or "",
                    form=form,
                    lemma=lemma,
                    postag=postag,
                    artificial=w.get("artificial") is not None,
                )
            )
            cite = w.get("cite") or ""
            if cite:
                book = cite.rsplit(":", 1)[1].split(".")[0]
            elif w.get("artificial") is None:
                book = w.get("div_book") or ""
            else:
                book = ""
            if not book or (postag or "").startswith("u"):
                continue
            treebank.words.append(
                TbWord(
                    sentence_id=sentence_id,
                    book=book,
                    form=form,
                    lemma=lemma,
                    postag=postag,
                    node_id=node_id,
                )
            )
    return treebank


def parse_treebank(path: Path) -> List[TbWord]:
    """The treebank's words in document order (see read_treebank)."""
    return read_treebank(path).words


def _align_run(
    app: Sequence[str], tb: Sequence[str]
) -> List[Optional[Tuple[int, int]]]:
    """
    Align a run of differing words (bare forms): for each app word, the
    (start, end) range of treebank words whose letters spell it, one word or
    several for a split word, or None. Leaves as few words unmatched as
    possible, so a split word next to an omitted line or a variant reading
    still matches. A run too long to be a few differing words (a treebank for
    another text) is left unmatched.
    """
    n, m = len(app), len(tb)
    if n * m == 0 or n * m > _MAX_RUN_CELLS:
        return [None] * n
    # left[i][j]: fewest words left unmatched aligning app[i:] with tb[j:]
    left = [[(n - i) + (m - j) for j in range(m + 1)] for i in range(n + 1)]
    step: List[List[object]] = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            options = []
            letters = ""
            for k in range(j, m):
                letters += tb[k]
                if len(letters) >= len(app[i]):
                    if letters == app[i]:
                        options.append((left[i + 1][k + 1], k + 1))
                    break
            options += [(1 + left[i + 1][j], "app"), (1 + left[i][j + 1], "tb")]
            left[i][j], step[i][j] = min(options, key=lambda o: o[0])
    ranges: List[Optional[Tuple[int, int]]] = []
    i = j = 0
    while i < n:
        move = step[i][j] if j < m else "app"
        if move == "tb":
            j += 1
        elif move == "app":
            ranges.append(None)
            i += 1
        else:
            ranges.append((j, move))
            i, j = i + 1, move
    return ranges


def align_text(
    text_id: int,
    lines: Sequence[Line],
    words: Sequence[TbWord],
    annotations: Optional[Mapping[int, str]] = None,
) -> Alignment:
    """
    Align a text's words with the treebank's, book by book: words whose
    letters match go together, and within a run of differing words an app
    word matches the treebank words that spell it (a split word). Every word
    of the text gets at least one WordParse; words without a treebank word
    get one without a parse. annotations gives a sentence's "auto" (GLAUx's
    parser); every other sentence is "manual".
    """
    annotations = annotations or {}
    result = Alignment()
    references = {line.line_number: line.reference or "" for line in lines}
    app_books: Dict[str, List[Tuple[int, int, str]]] = defaultdict(list)
    for line in lines:
        book = (line.reference or "").split(".")[0]
        for i, token in enumerate(line.content.split()):
            app_books[book].append((line.line_number, i, token))
    tb_books: Dict[str, List[TbWord]] = defaultdict(list)
    for w in words:
        tb_books[w.book].append(w)

    def matched(app_word: Tuple[int, int, str], parts: Sequence[TbWord]) -> None:
        line_number, token_index, _ = app_word
        for part, w in enumerate(parts):
            parse = WordParse(
                text_id=text_id,
                line_number=line_number,
                token_index=token_index,
                part=part,
                form=w.form,
                lemma=w.lemma,
                postag=w.postag,
                sentence_id=w.sentence_id,
                node_id=w.node_id,
            )
            result.parses.append(parse)
            if not parse.has_parse:
                result.untagged.append((references[line_number], w.form))
        if len(parts) == 1:
            result.identical += 1
        else:
            result.split += 1

    def unmatched(app_word: Tuple[int, int, str]) -> None:
        line_number, token_index, token = app_word
        result.parses.append(
            WordParse(
                text_id=text_id,
                line_number=line_number,
                token_index=token_index,
                part=0,
                form=token.strip(_PUNCT_STRIP) or token,
                lemma=None,
                postag=None,
                sentence_id=None,
            )
        )
        result.unmatched.append((references[line_number], token))

    for book, app in app_books.items():
        tb = tb_books.pop(book, [])
        a = [bare(t) for _, _, t in app]
        b = [bare(w.form) for w in tb]
        result.words += len(app)
        matcher = SequenceMatcher(a=a, b=b, autojunk=False)
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                for k in range(i2 - i1):
                    matched(app[i1 + k], [tb[j1 + k]])
                continue
            used = 0
            for app_word, span in zip(app[i1:i2], _align_run(a[i1:i2], b[j1:j2])):
                if span is None:
                    unmatched(app_word)
                else:
                    matched(app_word, tb[j1 + span[0] : j1 + span[1]])
                    used += span[1] - span[0]
            result.unused += (j2 - j1) - used
    result.unused += sum(len(rest) for rest in tb_books.values())

    spans: Dict[int, List[int]] = {}
    for p in result.parses:
        if p.sentence_id is not None:
            span = spans.setdefault(p.sentence_id, [p.line_number, p.line_number])
            span[0] = min(span[0], p.line_number)
            span[1] = max(span[1], p.line_number)
    result.sentences = sorted(
        (
            TreebankSentence(text_id, sid, first, last, annotations.get(sid, "manual"))
            for sid, (first, last) in spans.items()
        ),
        key=lambda s: (s.first_line, s.last_line, s.sentence_id),
    )
    result.parses.sort(key=lambda p: (p.line_number, p.token_index, p.part))
    return result
