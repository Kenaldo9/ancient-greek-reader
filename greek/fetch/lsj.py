"""
Liddell–Scott–Jones Greek-English Lexicon (Perseus encoding, CC BY-SA 4.0):
download the 27 XML files, find the entry for each treebank lemma, and build
a short definition and an HTML rendering of the full entry.

Reading is two passes over the files: a light index of every entry
(headword, spellings, forms listed in it, "= X" and "v. X" references) to
resolve lemmas, then short definitions and HTML for the matched entries.
"""

import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from greek.fetch.betacode import beta_to_unicode
from greek.fetch.catalog import fetch_url
from greek.fetch.treebank import bare
from greek.models.study import LexiconEntry

LSJ_BASE = (
    "https://raw.githubusercontent.com/PerseusDL/lexica/master/"
    "CTS_XML_TEI/perseus/pdllex/grc/lsj"
)
LSJ_FILES = [f"grc.lsj.perseus-eng{i}.xml" for i in range(1, 28)]

# Treebank lemmas that are a different headword in LSJ, where no spelling
# rule finds it: a superlative under its positive, an impersonal under its
# verb, a spelling LSJ does not list.
LEMMA_ALIASES = {
    "μάλιστα": "μάλα",
    "εἷος": "ἕως",
    "ἀντίον": "ἄντην",  # the adverb, not ἀντίον "part of a loom"
    "Ποσειδεών": "Ποσειδῶν",  # the treebank's lemma for Ποσειδάων
    "ἐπέοικε": "ἔπεοικα",
    "ἐμπίπλημι": "ἐμπίμπλημι",
    "ὁμοκλέω": "ὁμοκλάω",
    # GLAUx's prose lemmas (Herodotus, Thucydides)
    "εἴωθα": "ἔθω",  # the perfect, under its present
    "σέβω": "σέβομαι",  # the active, under the middle
    "ἀνέρομαι": "ἀνείρομαι",  # Attic, under the Ionic
    "ἐπισταμένως": "ἐπίσταμαι",  # the adverb, under its verb
}

# Spelling variants of the Homeric text, tried in order on letters-only keys
# when nothing else matches: Ionic η for Attic ᾱ (ἀμβροσίη → ἀμβροσία), ει
# for ι (οἰκτείρω → οἰκτίρω), ου for ο (μοῦνος → μόνος), and doubled
# consonants (ὅππως → ὅπως).
SPELLING_RULES = [
    ("η", "α"),
    ("ει", "ι"),
    ("ι", "ει"),
    ("ου", "ο"),
    ("σσ", "σ"),
    ("λλ", "λ"),
    ("ππ", "π"),
    ("ττ", "τ"),
]

# An entry without English equivalents counts as a cross-reference "= X" (or
# "v. X") only when that comes within this many letters of its start (ἀρνός,
# ὁ, late nom., = ἀρήν); later ones belong to a sense or to a merged entry.
_XREF_WITHIN = 20
# A form further on in an entry points elsewhere when "v. X" or "= X" follows
# within this many letters (ἠέλιος, ὁ, v. ἥλιος).
_FORM_XREF_WITHIN = 6

# Short definitions: at most this many meanings and about this many characters
_SHORT_ITEMS = 4
_SHORT_CHARS = 70
_FALLBACK_CHARS = 90

# Language labels before a cognate, which <tr> also marks: "Skt. nar-",
# "Lat. ab", "Skt. Relat. yas". E. is English (Euripides only in citations).
_LANGUAGE_LABEL = re.compile(
    r"\b(?:Skt|Lat|Goth|Lith|Lett|OHG|MHG|OE|ON|OSlav|Oslav|Slav|Umbr|Osc|Arm|"
    r"Ir|OIr|Alb|Avest|Germ|Engl|E|Gk|Phryg|Hitt|Toch|Icel|AS|Celt|Lyd|Cret)"
    r"\.(?:\s+[A-Za-z]+\.)?[\s,]*$"
)
# An aside on etymology: "(cf. Skt. …", "(Cf. …", "(perh. cf. …"
_ETYMOLOGY = re.compile(r"\(\s*(?:perh\.\s*|prob\.\s*)?(?:cf|Cf|v)\.")
# LSJ ends the preamble of forms and etymology with ":—" before the meaning
_PREAMBLE_END = re.compile(r":\s*—")

_GREEK_TAGS = {"foreign", "quote", "itype", "gen", "ref", "etym", "pron"}
_ITALIC_TAGS = {"gram", "tns", "mood", "pos", "per", "number", "subc", "lbl"}


def lemma_key(word: str) -> str:
    """Letters only, final sigma as σ: how lemmas and headwords are compared."""
    return bare(word).replace("ς", "σ")


def _accent_key(word: str) -> str:
    """A word without diaeresis, iota subscript or case, grave as acute."""
    decomposed = unicodedata.normalize("NFD", word)
    for mark, replacement in (("\u0308", ""), ("\u0345", ""), ("\u0300", "\u0301")):
        decomposed = decomposed.replace(mark, replacement)
    return unicodedata.normalize("NFC", decomposed).casefold()


def _clean(text: str) -> str:
    return " ".join(text.split())


def _capitalized(word: str) -> bool:
    first = next((ch for ch in word if ch.isalpha()), "")
    return first.isupper()


def _strip_homograph(key: str) -> str:
    return re.sub(r"\d+$", "", key)


def download_lsj(sources_dir: Path, refresh: bool = False) -> List[Path]:
    """
    The 27 LSJ files in sources_dir/lsj, downloading those missing (all of
    them if refresh is set). Raises urllib.error.URLError on network failure.
    """
    folder = sources_dir / "lsj"
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for filename in LSJ_FILES:
        dest = folder / filename
        if refresh or not dest.exists():
            data = fetch_url(f"{LSJ_BASE}/{filename}", timeout=120)
            partial = dest.with_name(dest.name + ".part")
            partial.write_bytes(data)
            partial.replace(dest)
        paths.append(dest)
    return paths


def _entries(paths: Iterable[Path]) -> Iterator[ET.Element]:
    """Every <entryFree> element, in file order; each is cleared after use."""
    for path in paths:
        for _, el in ET.iterparse(path):
            if el.tag == "entryFree":
                yield el
                el.clear()


# --- Pass 1: index ------------------------------------------------------------


@dataclass
class LsjHead:
    """What lemma resolution needs to know about an entry."""

    entry_id: str  # LSJ's id, e.g. "n19282"
    key: str  # Beta Code key, e.g. "ba/llw" (with homograph digit if any)
    headword: str  # Unicode, without homograph digit or vowel length
    homograph: int
    type: str  # "main" or "gloss" (a lexicographer's gloss)
    order: int  # position in the lexicon
    variants: List[str] = field(default_factory=list)  # spellings in the head
    # Forms listed further on, as (form, see): a middle or derived form of
    # this entry (see None), or a small entry Perseus merged into this one
    # that points elsewhere ("ἠέλιος, ὁ, v. ἥλιος": see "ἥλιος").
    forms: List[Tuple[str, Optional[str]]] = field(default_factory=list)
    xref: Optional[str] = None  # X, for an entry that starts "= X" or "v. X"


def _walk(node: ET.Element) -> Iterator[Tuple[str, str]]:
    """
    An entry in document order: ("orth", form), ("sense", ""), ("xref", X)
    for "= X" or "v. X", and ("text", text) outside citations.
    """
    if node.tag == "xr":
        lbl, ref = node.find("lbl"), node.find("ref")
        if (
            lbl is not None
            and ref is not None
            and (lbl.text or "").strip() in ("=", "v.")
        ):
            yield "xref", ref.text or ""
            return
    if node.tag == "orth":
        yield "orth", node.text or ""
        return
    if node.tag == "bibl":
        return
    if node.tag == "sense":
        yield "sense", ""
    if node.text:
        yield "text", node.text
    for child in node:
        yield from _walk(child)
        if child.tail:
            yield "text", child.tail


def _first_word(beta: str) -> Optional[str]:
    words = beta_to_unicode(beta).split()
    return words[0].strip(",.;·") or None if words else None


def _scan(el: ET.Element) -> Tuple[List[str], list, Optional[str]]:
    """An entry's head spellings, later forms, and its own cross-reference."""
    has_tr = el.find(".//tr") is not None
    variants: List[str] = []
    forms: List[Tuple[str, Optional[str]]] = []
    xref: Optional[str] = None
    xref_checked = False
    letters = 0  # since the start of the entry
    pending: Optional[list] = None  # [form, letters since it]
    orths = 0
    in_head = True
    for kind, value in _walk(el):
        if kind == "sense":
            in_head = False
        elif kind == "orth":
            if pending:
                forms.append((pending[0], None))
                pending = None
            orths += 1
            form = beta_to_unicode(value).strip(" ,.;:·")
            if orths == 1 or not form:
                continue  # the headword itself
            if in_head:
                variants.append(form)
            else:
                pending = [form, 0]
        elif kind == "xref":
            if pending and pending[1] <= _FORM_XREF_WITHIN:
                forms.append((pending[0], _first_word(value)))
                pending = None
            elif not xref_checked:
                xref_checked = True
                if not has_tr and letters <= _XREF_WITHIN:
                    xref = _first_word(value)
        else:
            count = sum(ch.isalpha() for ch in value)
            letters += count
            if pending:
                pending[1] += count
                if pending[1] > _FORM_XREF_WITHIN:
                    forms.append((pending[0], None))
                    pending = None
    if pending:
        forms.append((pending[0], None))
    headword = beta_to_unicode(_strip_homograph(el.get("key") or ""))
    variants = [v for v in dict.fromkeys(variants) if v != headword]
    return variants, forms, xref


def read_heads(paths: Iterable[Path]) -> List[LsjHead]:
    heads = []
    for order, el in enumerate(_entries(paths)):
        key = el.get("key") or ""
        digits = re.search(r"(\d+)$", key)
        variants, forms, xref = _scan(el)
        heads.append(
            LsjHead(
                entry_id=el.get("id") or "",
                key=key,
                headword=beta_to_unicode(_strip_homograph(key)),
                homograph=int(digits.group(1)) if digits else 0,
                type=el.get("type") or "main",
                order=order,
                variants=variants,
                forms=forms,
                xref=xref,
            )
        )
    return heads


@dataclass
class LemmaMatch:
    lemma: str
    head: Optional[LsjHead]
    matched_by: Optional[str]  # how the entry was found; None if not found


class LsjIndex:
    def __init__(self, heads: Sequence[LsjHead]):
        self.exact: Dict[str, List[LsjHead]] = defaultdict(list)
        self.by_key: Dict[str, List[LsjHead]] = defaultdict(list)
        self.by_variant: Dict[str, List[LsjHead]] = defaultdict(list)
        self.by_form: Dict[str, List[Tuple[LsjHead, Optional[str]]]] = defaultdict(list)
        for h in heads:
            self.exact[h.headword].append(h)
            self.by_key[lemma_key(h.headword)].append(h)
            for v in h.variants:
                self.by_variant[lemma_key(v)].append(h)
            for form, see in h.forms:
                self.by_form[lemma_key(form)].append((h, see))

    @staticmethod
    def best(candidates: Sequence[LsjHead], word: str) -> Optional[LsjHead]:
        """
        The likeliest entry for a word: the same accents first (ἄνεῳ for
        ἄνεω, not ἀνέω), then a main entry over a gloss, then an entry of its
        own over one pointing elsewhere (δύω "sink", not δύω "v. δύο"), then
        the first homograph. A capitalized word (a name) only matches a
        capitalized headword: Ἑλένη is not ἑλένη, a torch.
        """
        if _capitalized(word):
            candidates = [h for h in candidates if _capitalized(h.headword)]
        if not candidates:
            return None
        accents = _accent_key(word)
        return min(
            candidates,
            key=lambda h: (
                h.headword != word,
                _accent_key(h.headword) != accents,
                h.type != "main",
                h.xref is not None,
                h.homograph,
                h.order,
            ),
        )

    def headword(self, word: str) -> Optional[LsjHead]:
        """The entry whose headword is word, with or without the same accents."""
        return self.best(
            self.exact.get(word) or self.by_key.get(lemma_key(word), []), word
        )

    def variant(self, word: str) -> Optional[LsjHead]:
        """The entry listing word as a spelling of its headword (μίγνυμι)."""
        return self.best(self.by_variant.get(lemma_key(word), []), word)

    def form(self, word: str) -> Optional[LsjHead]:
        """
        The entry listing word further on (χαρίζομαι under χαρίζω), or the
        one such a listing points to (ἠέλιος, v. ἥλιος).
        """
        targets = []
        for head, see in self.by_form.get(lemma_key(word), []):
            target = self.headword(see) if see else head
            if target is not None:
                targets.append(target)
        return self.best(targets, word)

    def follow(self, head: LsjHead, word: str) -> LsjHead:
        """
        The entry a "= X" or "v. X" entry refers to (up to three steps), else
        head. A name only leads to a capitalized headword.
        """
        seen = {head.entry_id}
        while head.xref:
            target = self.headword(head.xref)
            if target is None or target.entry_id in seen or len(seen) > 3:
                break
            if _capitalized(word) and not _capitalized(target.headword):
                break
            seen.add(target.entry_id)
            head = target
        return head


def _spelling_variants(key: str) -> Iterator[str]:
    for old, new in SPELLING_RULES:
        if old in key:
            yield key.replace(old, new)
            # a single change, for words where only one spot differs
            for i in range(len(key)):
                if key.startswith(old, i):
                    yield key[:i] + new + key[i + len(old) :]


def resolve_lemma(lemma: str, index: LsjIndex) -> LemmaMatch:
    """
    Find a treebank lemma's LSJ entry, trying in order: an alias, the
    headword (accents may differ), a spelling in an entry's head, a form
    listed further on in an entry, a spelling rule (not for names). An entry
    that only refers "= X" or "v. X" is replaced by X's entry.
    """
    word = unicodedata.normalize("NFC", _strip_homograph(lemma))
    found, how = None, None
    for how, find in (
        (
            "alias",
            lambda w: index.headword(LEMMA_ALIASES[w]) if w in LEMMA_ALIASES else None,
        ),
        ("headword", index.headword),
        ("variant", index.variant),
        ("form", index.form),
    ):
        found = find(word)
        if found is not None:
            break
    if found is None and not _capitalized(word):
        for candidate in _spelling_variants(lemma_key(word)):
            found = index.best(index.by_key.get(candidate, []), word)
            if found is not None:
                how = "spelling"
                break
    if found is None:
        return LemmaMatch(lemma, None, None)
    target = index.follow(found, word)
    return LemmaMatch(lemma, target, "cross-reference" if target is not found else how)


# --- Pass 2: definitions and HTML -----------------------------------------------


def _useful_meaning(text: str) -> Optional[str]:
    """
    An English equivalent fit for a short definition, or None for the
    noise <tr> also marks: stems (nar-), cognates with accents (vácas,
    nāvis), abbreviations and citations (Il.Parv., NT).
    """
    text = _clean(text).strip(" ,;:")
    text = text.rstrip(".") if text.count(".") == 1 else text
    if len(text) < 2 or text.startswith("-") or text.endswith("-") or "." in text:
        return None
    if text.count("(") != text.count(")") or "[" in text or "]" in text:
        return None
    if text.isupper():  # abbreviations: NT
        return None
    if any(ch.isalpha() and not ch.isascii() for ch in text):
        return None
    return text


def _meanings(el: ET.Element) -> List[Tuple[int, str]]:
    """
    An entry's English equivalents as (major sense, text), in order, without
    the cognates <tr> also marks: after a language label (Lat. ab, Skt.
    nar-), right after "(" (εἰμί (sum)), inside an etymology aside
    "(cf. …)", or in the preamble of forms that LSJ ends with ":—" before
    the first meaning, which follows it directly (…:—son).
    """
    found: List[Tuple[int, int, int, str]] = []  # (group, block, position, text)
    cuts: List[int] = []  # positions of ":—" + meaning in the head or first sense
    state = {"group": 0, "block": 0, "pos": 0, "prev": "", "asides": [], "dash": False}

    def text(value: str, greek: bool) -> None:
        value = re.sub(r"\s+", " ", value)
        if not greek:  # in Beta Code, ( and ) are breathings
            for i, ch in enumerate(value):
                if ch == "(":
                    state["asides"].append(bool(_ETYMOLOGY.match(value, i)))
                elif ch == ")" and state["asides"]:
                    state["asides"].pop()
            if _PREAMBLE_END.search(state["prev"][-2:] + value):
                state["dash"] = state["block"] <= 1
            elif any(ch.isalpha() for ch in value):
                state["dash"] = False
        state["prev"] = (state["prev"] + value)[-80:]
        state["pos"] += 1

    def walk(node: ET.Element, greek: bool) -> None:
        for child in node:
            child_greek = greek or child.get("lang") == "greek"
            if child.tag == "sense":
                state["block"] += 1
                if int(child.get("level") or 1) <= 2:
                    state["group"] += 1
            if child.tag == "tr":
                meaning = "".join(child.itertext())
                if state["dash"]:
                    cuts.append(state["pos"] - 1)
                    state["dash"] = False
                prev = state["prev"]
                if not (
                    any(state["asides"])
                    or _LANGUAGE_LABEL.search(prev)
                    or prev.rstrip().endswith("(")  # a Latin gloss: εἰμί (sum)
                ):
                    found.append(
                        (state["group"], state["block"], state["pos"], meaning)
                    )
                text(meaning, False)
            elif child.tag != "bibl":
                if child.text:
                    text(child.text, child_greek)
                walk(child, child_greek)
            if child.tail:
                text(child.tail, greek)

    if el.text:
        text(el.text, False)
    walk(el, False)
    if cuts:
        found = [f for f in found if f[1] > 1 or f[2] > cuts[0]]
    return [(group, meaning) for group, _, _, meaning in found]


def short_definition(el: ET.Element) -> str:
    """
    A few English equivalents: up to two from the first major sense (A, I,
    II, …) with any, then the first of each later one, as in ἴφθιμος
    "stout, strong; comely, stately". Without any, the start of the first
    sense.
    """
    groups: Dict[int, List[str]] = defaultdict(list)
    for group, meaning in _meanings(el):
        useful = _useful_meaning(meaning)
        if useful:
            groups[group].append(useful)
    picked: List[str] = []
    given: set = set()  # the comma-separated parts already picked
    for group in sorted(groups):
        allowed = 1 if picked else 2
        for meaning in groups[group]:
            if allowed == 0 or len(picked) == _SHORT_ITEMS:
                break
            parts = {p.strip().lower() for p in meaning.split(",") if p.strip()}
            if parts <= given:  # wrath after wrath, set after "set, put, place"
                continue
            if picked and len("; ".join(picked + [meaning])) > _SHORT_CHARS:
                break
            picked.append(meaning)
            given |= parts
            allowed -= 1
    if picked:
        return "; ".join(picked)
    return _leading_text(el)


def _plain_text(node: ET.Element, greek: bool = False) -> str:
    """A node's text without citations, Greek converted from Beta Code."""
    greek = greek or node.get("lang") == "greek"
    parts = [beta_to_unicode(node.text or "") if greek else node.text or ""]
    for child in node:
        if child.tag not in ("bibl", "orth"):
            parts.append(_plain_text(child, greek))
        parts.append(child.tail or "")
    return "".join(parts)


def _leading_text(el: ET.Element) -> str:
    """
    The start of the first sense, or what follows ":—" in the entry's head
    (Ἀχιλλεύς, … :— Achilles.), for entries without English equivalents.
    """
    head = ET.Element("head")
    head.text = el.text
    for child in el:
        if child.tag == "sense":
            break
        head.append(child)
    head_text = _clean(_plain_text(head))
    sense = el.find("sense")
    if _PREAMBLE_END.search(head_text):
        text = _PREAMBLE_END.split(head_text)[-1]
    else:
        text = _clean(_plain_text(sense if sense is not None else el))
    text = re.sub(r"\s+([,;:.)])", r"\1", text)
    # punctuation left around a removed citation: "ὁ,, a town", "= ἀρήν,."
    text = re.sub(r"([,;:.])(?:\s*[,;:.])+", r"\1", text).strip(" ,;:—-")
    if len(text) <= _FALLBACK_CHARS:
        return text
    return text[:_FALLBACK_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + " …"


def entry_html(el: ET.Element) -> str:
    """
    The whole entry as HTML: the head in a paragraph, each sense in its own
    (data-level gives LSJ's depth), Greek converted from Beta Code in
    lang="grc" spans, English equivalents in <em>, citations in <cite>.
    """
    blocks: List[List[str]] = [[]]
    levels = [0]

    def text(value: Optional[str], greek: bool) -> str:
        if not value:
            return ""
        value = re.sub(r"\s+", " ", value)
        return html.escape(beta_to_unicode(value) if greek else value, quote=False)

    def render(node: ET.Element, greek: bool) -> str:
        greek = greek or node.get("lang") == "greek"
        inner = text(node.text, greek) + "".join(
            render(c, greek) + text(c.tail, greek) for c in node
        )
        tag = node.tag
        if tag == "orth":
            return f'<b lang="grc">{inner}</b>'
        if tag in _GREEK_TAGS and node.get("lang") == "greek":
            return f'<span lang="grc">{inner}</span>'
        if tag == "tr":
            return f"<em>{inner}</em>"
        if tag == "bibl":
            return f"<cite>{inner.strip()}</cite>"
        if tag in _ITALIC_TAGS:
            return f"<i>{inner}</i>"
        if tag == "pb":
            return ""
        return inner

    blocks[0].append(text(el.text, False))
    for child in el:
        if child.tag == "sense":
            blocks.append([])
            levels.append(int(child.get("level") or 1))
            label = child.get("n") or ""
            if label and not label.startswith("0"):
                blocks[-1].append(f"<b>{html.escape(label)}.</b> ")
        blocks[-1].append(render(child, False) + text(child.tail, False))

    paragraphs = []
    for level, parts in zip(levels, blocks):
        body = re.sub(r" {2,}", " ", "".join(parts)).strip()
        if body:
            attr = f' data-level="{level}"' if level else ""
            paragraphs.append(f"<p{attr}>{body}</p>")
    return "\n".join(paragraphs)


def read_entries(
    paths: Iterable[Path], wanted: Iterable[str]
) -> Dict[str, LexiconEntry]:
    """Short definitions and HTML for the entries with the given LSJ ids."""
    wanted = set(wanted)
    entries = {}
    for el in _entries(paths):
        entry_id = el.get("id") or ""
        if entry_id in wanted:
            key = el.get("key") or ""
            entries[entry_id] = LexiconEntry(
                source="LSJ",
                key=key,
                headword=beta_to_unicode(_strip_homograph(key)),
                short_def=short_definition(el),
                entry_html=entry_html(el),
            )
    return entries
