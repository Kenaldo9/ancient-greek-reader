"""
Message Batches requests for the dictionary forms of a treebank text's nouns
and adjectives, and the constructions of its verbs, from Claude Opus 5.5: one
request per group of lemmas, each with its part of speech, the start of its
LSJ entry (a verb's short definition) and the forms the text has. The results
are stored in lemma_forms under the work's dialect ("μῆνις, -ιος, ἡ", with a
note on the Homeric forms and an adjective's irregular comparison; "+ gen. of
the person heard, + acc. of the thing" for a verb); the prompt is the work's
(greek/api/works.py). This module builds the requests and reads their
results; `greek study forms` submits them and `greek study batch collect`
stores them (greek/cli/study_cmd.py).
"""

import html
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Collection, Dict, List, Mapping, Optional, Sequence, Tuple

from greek.api.study import (
    BATCH_PRICES,
    EFFORT,
    MAX_TOKENS,
    MODEL,
    estimate_input_tokens,
    usage_of,
)
from greek.api.works import ILIAD, WorkProfile
from greek.models import Usage
from greek.search import plain

LEMMAS_PER_REQUEST = 25
FORMS_SHOWN = 6  # forms of a lemma in the request, the most frequent first
LSJ_CHARS = 200  # characters of an LSJ entry's start in the request

# Output tokens per lemma (thinking included) until a collected batch measures
# them: the plan's 100–270 (a single dictionary form costs less than the six
# principal parts' measured 266), taken at the upper end. The Pilot 1 batch
# measured 63 for nouns and adjectives.
DEFAULT_OUTPUT_PER_LEMMA = 250.0

ARTICLES = ("ὁ", "ἡ", "τό")


@dataclass
class FormsLemma:
    """
    A lemma to request: its part of speech ("noun", "adjective" or "verb"),
    the start of its LSJ entry as plain text (for a verb, LSJ's short
    definition, as meaning) or a model's short identification when LSJ lacks
    it, and forms of it in the text, the most frequent first.
    """

    lemma: str
    pos: str
    lsj: str = ""
    identification: str = ""
    forms: List[str] = field(default_factory=list)
    lsj_headword: str = ""  # the entry's headword, when it isn't the lemma
    meaning: str = ""  # a verb's LSJ short definition


def forms_custom_id(text_id: int, number: int) -> str:
    """The id of a batch's nth forms request: text 12, request 3 → "12_forms_3"."""
    return f"{text_id}_forms_{number}"


def group_lemmas(
    lemmas: Sequence[FormsLemma], size: int = LEMMAS_PER_REQUEST
) -> List[List[FormsLemma]]:
    """
    The lemmas in order, in as few requests of at most `size` as possible,
    evenly filled (127 lemmas: six requests of 21 or 22).
    """
    if not lemmas:
        return []
    count = -(-len(lemmas) // size)
    base, extra = divmod(len(lemmas), count)
    groups, start = [], 0
    for i in range(count):
        end = start + base + (i < extra)
        groups.append(list(lemmas[start:end]))
        start = end
    return groups


# --- LSJ text -------------------------------------------------------------------


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def lsj_start(entry_html: str, chars: int = LSJ_CHARS) -> str:
    """
    The start of an LSJ entry as plain text, about `chars` characters cut at
    a word ("μῆνις, Dor. and Aeol. μᾶν-, ἡ, gen. A. μήνιος Pl. R. 390e, …").
    """
    text = re.sub(r"<b>[A-Z0-9IVX]{1,4}\.</b>", " ", entry_html)  # sense labels
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:)])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text).strip()
    text = _nfc(text)
    if len(text) <= chars:
        return text
    cut = text.rfind(" ", 0, chars)
    return text[: cut if cut > chars // 2 else chars].rstrip(" ,;:") + " …"


def lsj_article(entry_html: str) -> Optional[str]:
    """
    The article LSJ gives a noun in its entry's head (its <gen>: ὁ, ἡ or τό),
    or None when the head gives none or more than one (ὁ, ἡ).
    """
    head = entry_html.split("</p>", 1)[0]
    found = []
    for span in re.findall(r'<span lang="grc">([^<]*)</span>', head):
        word = _nfc(span).strip(" ,.;:")
        if word in ARTICLES and word not in found:
            found.append(word)
    return found[0] if len(found) == 1 else None


_SINGULAR = {"οἱ": "ὁ", "αἱ": "ἡ", "τά": "τό"}


def headword_articles(headword: str) -> Tuple[str, ...]:
    """
    The articles a dictionary form ends with, in the singular and in order:
    "μῆνις, -ιος, ἡ" → ("ἡ",), "κύων, κυνός, ὁ, ἡ" and "θίς, θινός, ὁ/ἡ" →
    ("ὁ", "ἡ"), "Ἀχαιοί, -ῶν, οἱ" → ("ὁ",). A note in brackets after them is
    ignored; () without an article.
    """
    words = re.split(r"[,/\s]+", _nfc(headword).split("(", 1)[0])
    found: List[str] = []
    for word in reversed([w.strip(" .") for w in words if w.strip(" .")]):
        article = _SINGULAR.get(word, word)
        if article not in ARTICLES:
            break
        found.insert(0, article)
    return tuple(found)


# --- Prompt and request -----------------------------------------------------------

_VERSE_FORMS = """You are an expert in Ancient Greek morphology and syntax, above all the language of Homer. You give the dictionary forms of nouns and adjectives, and the constructions of verbs, to a reader working through {reading} in Greek.

Each request lists lemmas of the Perseus Ancient Greek Dependency Treebank, numbered, each with its part of speech, the start of its LSJ entry when LSJ has one (for a verb, LSJ's short definition), or a short identification when it doesn't, and forms of it that occur in {reading}, the most frequent first.

Return a JSON object with "lemmas": exactly one object per listed lemma, in the order listed. Each object has:
- "lemma": the lemma exactly as listed.
- "headword": the dictionary form as a commentary's vocabulary gives it: a noun with its genitive ending and article ("μῆνις, -ιος, ἡ"; a short or irregular noun with its whole genitive: "κῆρ, κῆρος, τό"), an adjective with its feminine and neuter endings ("οὐλόμενος, -η, -ον"; two endings: "ἀθάνατος, -ον"; third declension: "ταχύς, -εῖα, -ύ"). Give the Homeric genitive where Homer's differs from the Attic one ("Ἀχιλλεύς, -ῆος, ὁ"). A name has its genitive and article too ("Ἀγαμέμνων, -ονος, ὁ"); a plural-only noun is given in the plural ("Ἀχαιοί, -ῶν, οἱ"). The headword is the dictionary form alone: a remark (another number, a Homeric spelling) goes in "forms", not in brackets after it.
- "forms": a few words, or "" when nothing needs saying: the Attic form when the headword's is Homeric ("Attic gen. -έως"), and Homeric forms a reader might not recognise among those listed or common in Homer ("dat. pl. -εσσι; gen. -οιο").
- "comparison": for an adjective whose comparison is irregular, its comparative and superlative ("ἀμείνων, ἄριστος"); otherwise "", with no remark.
- "construction": for a verb, the cases and constructions it takes in Homer, at most three, the commonest first, separated by semicolons. Write each as the case or construction, a comma, and the sense in a few words: "+ gen. of the person heard, listen to; + acc. of the sound, hear"; "+ acc. and dat. of the person, give"; "+ inf., be eager to"; "intransitive, stand apart". Give constructions only: no forms, tenses or quotations. For a noun or adjective, "".

For a verb, "headword", "forms" and "comparison" are "": its principal parts are given elsewhere."""

_PROSE_FORMS = """You are an expert in Ancient Greek morphology and syntax, above all {language}. You give the dictionary forms of nouns and adjectives, and the constructions of verbs, to a reader working through {reading} in Greek.

Each request lists lemmas of the GLAUx corpus, numbered, each with its part of speech, the start of its LSJ entry when LSJ has one (for a verb, LSJ's short definition), or a short identification when it doesn't, and forms of it that occur in {author}, the most frequent first. {spelling}

Return a JSON object with "lemmas": exactly one object per listed lemma, in the order listed. Each object has:
- "lemma": the lemma exactly as listed.
- "headword": the dictionary form as a commentary's vocabulary gives it: a noun with its genitive ending and article ("{noun}"; a short or irregular noun with its whole genitive: "χείρ, χειρός, ἡ"), an adjective with its feminine and neuter endings ("{adjective}"; two endings: "ἀθάνατος, -ον"; third declension: "ταχύς, -εῖα, -ύ"). {headword} A name has its genitive and article too ("{name}"); a plural-only noun is given in the plural ("{plural}"). The headword is the dictionary form alone: a remark (another number, another spelling) goes in "forms", not in brackets after it.
- "forms": a few words, or "" when nothing needs saying: {forms}
- "comparison": for an adjective whose comparison is irregular, its comparative and superlative ("ἀμείνων, ἄριστος"); otherwise "", with no remark.
- "construction": for a verb, the cases and constructions it takes in {author}, at most three, the commonest first, separated by semicolons. Write each as the case or construction, a comma, and the sense in a few words: "+ gen. of the person heard, listen to; + acc. of the sound, hear"; "+ acc. and dat. of the person, give"; "+ inf., be eager to"; "intransitive, stand apart". Give constructions only: no forms, tenses or quotations. For a noun or adjective, "".

For a verb, "headword", "forms" and "comparison" are "": its principal parts are given elsewhere."""


def forms_system_prompt(work: WorkProfile) -> str:
    """The system prompt of a work's forms requests (the Iliad's as made for it)."""
    if work.verse:
        return _VERSE_FORMS.format(reading=work.reading)
    return _PROSE_FORMS.format(
        language=work.language,
        reading=work.reading,
        author=work.author,
        spelling=work.lemma_spelling,
        noun=work.noun_example,
        adjective=work.adjective_example,
        headword=work.headword_rule,
        name=work.name_example,
        plural=work.plural_example,
        forms=work.forms_rule,
    )


FORMS_SYSTEM_PROMPT = forms_system_prompt(ILIAD)


FORMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "lemmas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma": {"type": "string"},
                    "headword": {"type": "string"},
                    "forms": {"type": "string"},
                    "comparison": {"type": "string"},
                    "construction": {"type": "string"},
                },
                "required": [
                    "lemma",
                    "headword",
                    "forms",
                    "comparison",
                    "construction",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lemmas"],
    "additionalProperties": False,
}


def _lemma_line(number: int, item: FormsLemma, work: WorkProfile = ILIAD) -> str:
    """
    '1 μῆνις (noun) | LSJ: μῆνις, … ἡ, gen. … | Iliad: μῆνιν, μῆνις';
    '2 ἀκούω (verb) | LSJ meaning: hear | Iliad: …'.
    """
    fields = [f"{number} {item.lemma} ({item.pos})"]
    if item.meaning:
        fields.append(f"LSJ meaning: {item.meaning}")
    elif item.lsj and item.lsj_headword:
        fields.append(f"LSJ, under {item.lsj_headword}: {item.lsj}")
    elif item.lsj:
        fields.append(f"LSJ: {item.lsj}")
    elif item.identification:
        fields.append(f"not in LSJ; identified as: {item.identification}")
    else:
        fields.append("not in LSJ")
    if item.forms:
        fields.append(f"{work.title}: " + ", ".join(item.forms))
    return " | ".join(fields)


def build_forms_prompt(lemmas: Sequence[FormsLemma], work: WorkProfile = ILIAD) -> str:
    """The user message for one request: the lemmas, numbered."""
    return "\n".join(
        [
            f'Dictionary forms of {len(lemmas)} lemmas. Give "lemmas" '
            f"{len(lemmas)} objects, in this order.",
            "",
            *(_lemma_line(number, item, work) for number, item in enumerate(lemmas, 1)),
        ]
    )


def build_forms_request(
    work: WorkProfile, cid: str, lemmas: Sequence[FormsLemma]
) -> Dict[str, Any]:
    """One Message Batches request ({custom_id, params}) for some lemmas."""
    return {
        "custom_id": cid,
        "params": {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            # Thinking is always on for Opus 5.5; effort is set explicitly.
            "output_config": {
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": FORMS_SCHEMA},
            },
            "system": [
                {
                    "type": "text",
                    "text": forms_system_prompt(work),
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            "messages": [{"role": "user", "content": build_forms_prompt(lemmas, work)}],
        },
    }


# --- Estimates ----------------------------------------------------------------


@dataclass
class FormsEstimate:
    requests: int
    lemmas: int
    input_tokens: int
    output_tokens: int
    output_per_lemma: float
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


def estimate_forms(
    requests: Sequence[Mapping[str, Any]],
    lemma_count: int,
    output_per_lemma: float = DEFAULT_OUTPUT_PER_LEMMA,
) -> FormsEstimate:
    """The estimated tokens and batch cost of some forms requests."""
    return FormsEstimate(
        requests=len(requests),
        lemmas=lemma_count,
        input_tokens=sum(estimate_input_tokens(r) for r in requests),
        output_tokens=round(lemma_count * output_per_lemma),
        output_per_lemma=output_per_lemma,
    )


# --- Reading results ------------------------------------------------------------


@dataclass
class LemmaOutput:
    headword: str
    forms: str
    comparison: str
    construction: str = ""


@dataclass
class FormsOutcome:
    """
    A forms batch result: the dictionary form of each lemma that passed the
    checks, the lemmas that failed and why, or why the whole request failed;
    and its usage.
    """

    custom_id: str
    usage: Usage
    lemmas: Dict[str, LemmaOutput] = field(default_factory=dict)
    failed: Dict[str, str] = field(default_factory=dict)  # lemma → reason
    failure: Optional[str] = None  # the whole request

    @property
    def summary(self) -> Optional[str]:
        """Why the request is not fully stored, or None if it is."""
        if self.failure:
            return self.failure
        if not self.failed:
            return None
        n, total = len(self.failed), len(self.failed) + len(self.lemmas)
        reasons = "; ".join(f"{v}: {r}" for v, r in self.failed.items())
        return f"{n} of {total} lemmas not stored: {reasons}"


def _key(value: Any) -> str:
    return _nfc(value).strip() if isinstance(value, str) else ""


def _text(item: Mapping[str, Any], name: str) -> str:
    value = item.get(name)
    return _nfc(value.strip()) if isinstance(value, str) else ""


def check_forms_output(
    data: Any, lemmas: Sequence[str], verbs: Collection[str] = ()
) -> Tuple[Dict[str, LemmaOutput], Dict[str, str]]:
    """
    A result's JSON checked against the lemmas asked for: each lemma's
    dictionary form, or for a verb (one of verbs) its construction (found by
    the lemma it repeats, so a skipped lemma fails alone), and the lemmas
    that failed and why. A lemma repeated with other accents is found when no
    other lemma asked for or answered reads the same unaccented. Raises
    ValueError if the output has no list.
    """
    if not isinstance(data, dict) or not isinstance(data.get("lemmas"), list):
        raise ValueError("the output has no list of lemmas")
    answers: Dict[str, Any] = {}
    for item in data["lemmas"]:
        if isinstance(item, dict):
            answers.setdefault(_key(item.get("lemma")), item)
    asked = {_key(lemma) for lemma in lemmas}
    unaccented: Dict[str, List[str]] = {}
    for key in answers.keys() - asked:
        unaccented.setdefault(plain(key), []).append(key)
    found, failed = {}, {}
    for lemma in lemmas:
        key = _key(lemma)
        if key not in answers:
            same = unaccented.get(plain(key), [])
            if len(same) != 1 or sum(plain(k) == plain(key) for k in asked) != 1:
                failed[lemma] = "not in the output"
                continue
            key = same[0]
        item = answers[key]
        headword = _text(item, "headword")
        construction = _text(item, "construction")
        if lemma in verbs and not construction:
            failed[lemma] = "no construction"
            continue
        if lemma not in verbs and not headword:
            failed[lemma] = "no headword"
            continue
        found[lemma] = LemmaOutput(
            headword, _text(item, "forms"), _text(item, "comparison"), construction
        )
    return found, failed


def read_forms_result(
    entry: Any, lemmas: Sequence[str], model: str = MODEL, verbs: Collection[str] = ()
) -> FormsOutcome:
    """
    One entry of a forms batch's results, checked against its lemmas. The
    request fails on an API error, expiry, cancellation, a refusal,
    truncation at max_tokens and output that isn't JSON; a lemma fails alone
    when it is missing or has no headword (a verb, no construction). A failed
    message is still billed, so its usage is kept.
    """
    result = entry.result
    if result.type == "errored":
        error = result.error.error
        return FormsOutcome(
            entry.custom_id, Usage(), failure=f"error: {error.type}: {error.message}"
        )
    if result.type in ("expired", "canceled"):
        return FormsOutcome(entry.custom_id, Usage(), failure=result.type)
    message = result.message
    usage = usage_of(message.usage, model)
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None)
        reason = f"refusal ({category})" if category else "refusal"
        return FormsOutcome(entry.custom_id, usage, failure=reason)
    if message.stop_reason == "max_tokens":
        return FormsOutcome(entry.custom_id, usage, failure="truncated at max_tokens")
    text = "".join(b.text for b in message.content if b.type == "text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return FormsOutcome(
            entry.custom_id, usage, failure="the output is not valid JSON"
        )
    try:
        found, failed = check_forms_output(data, lemmas, verbs)
    except ValueError as e:
        return FormsOutcome(entry.custom_id, usage, failure=str(e))
    return FormsOutcome(entry.custom_id, usage, lemmas=found, failed=failed)
