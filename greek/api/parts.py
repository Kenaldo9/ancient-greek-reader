"""
Message Batches requests for the principal parts of a treebank text's verbs,
from Claude Opus 5.5: one request per group of verbs (treebank lemmas), with
the work's prompt (greek/api/works.py). The results are stored in verb_parts
under the work's dialect. This module
builds the requests and reads their results; `greek study parts` submits them
and `greek study batch collect` stores them (greek/cli/study_cmd.py).
"""

import json
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

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

VERBS_PER_REQUEST = 20

# Output tokens per verb (thinking included) until a collected batch measures
# them: about 170 for the JSON of six parts, plus thinking at effort medium.
DEFAULT_OUTPUT_PER_VERB = 350.0

# The names stored with each part, as the word pane labels them
PART_NAMES = (
    "Present active",
    "Future active",
    "Aorist active",
    "Perfect active",
    "Perfect middle/passive",
    "Aorist passive",
)


def parts_custom_id(text_id: int, number: int) -> str:
    """The id of a batch's nth parts request: text 12, request 3 → "12_parts_3"."""
    return f"{text_id}_parts_{number}"


def group_verbs(verbs: Sequence[str], size: int = VERBS_PER_REQUEST) -> List[List[str]]:
    """The verbs in requests of `size`, in order."""
    return [list(verbs[i : i + size]) for i in range(0, len(verbs), size)]


_VERSE_PARTS = """You are an expert in Ancient Greek morphology, above all the language of Homer. You give the principal parts of verbs to a reader working through {reading} in Greek.

Each request lists verbs, numbered, as the lemmas (dictionary forms) of the Perseus Ancient Greek Dependency Treebank.

Return a JSON object with "verbs": exactly one object per listed verb, in the order listed. Each object has:
- "verb": the lemma exactly as listed.
- "parts": exactly six objects, in this order: present, future, aorist, perfect active, perfect middle/passive, aorist passive. Each has:
  - "form": the first person singular indicative, past tenses with their augment. Give the epic or Ionic form a reader of {reading} meets where Homer's differs from the Attic one; otherwise the Attic form. Give "—" when the verb has no such part.
  - "note": a few words, or "" when nothing needs saying: the Attic form when the form given is epic ("Attic ᾄσομαι"), Homer's unaugmented form, "middle in form", "rare", "late", "not in Homer", or the verb a suppletive part comes from.

For a deponent verb, give its middle or passive forms in the slots they fill (μάχομαι, μαχήσομαι, ἐμαχεσάμην, —, μεμάχημαι, —). When the listed lemma is itself a suppletive past form (εἶπον, εἶδον), give the principal parts of the verb it belongs to, with the lemma in its slot."""

_PROSE_PARTS = """You are an expert in Ancient Greek morphology, above all {language}. You give the principal parts of verbs to a reader working through {reading} in Greek.

Each request lists verbs, numbered, as the lemmas (dictionary forms) of the GLAUx corpus. {spelling}

Return a JSON object with "verbs": exactly one object per listed verb, in the order listed. Each object has:
- "verb": the lemma exactly as listed.
- "parts": exactly six objects, in this order: present, future, aorist, perfect active, perfect middle/passive, aorist passive. Each has:
  - "form": the first person singular indicative, past tenses with their augment. {form} Give "—" when the verb has no such part.
  - "note": a few words, or "" when nothing needs saying: {note}

For a deponent verb, give its middle or passive forms in the slots they fill (μάχομαι, μαχήσομαι, ἐμαχεσάμην, —, μεμάχημαι, —). When the listed lemma is itself a suppletive past form (εἶπον, εἶδον), give the principal parts of the verb it belongs to, with the lemma in its slot."""


def parts_system_prompt(work: WorkProfile) -> str:
    """The system prompt of a work's parts requests (the Iliad's as made for it)."""
    if work.verse:
        return _VERSE_PARTS.format(reading=work.reading)
    return _PROSE_PARTS.format(
        language=work.language,
        reading=work.reading,
        spelling=work.lemma_spelling,
        form=work.parts_form_rule,
        note=work.parts_note_rule,
    )


PARTS_SYSTEM_PROMPT = parts_system_prompt(ILIAD)


PARTS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "verbs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "verb": {"type": "string"},
                    "parts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "form": {"type": "string"},
                                "note": {"type": "string"},
                            },
                            "required": ["form", "note"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["verb", "parts"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verbs"],
    "additionalProperties": False,
}


def build_parts_prompt(verbs: Sequence[str]) -> str:
    """The user message for one request: the verbs, numbered."""
    return "\n".join(
        [
            f'Principal parts of {len(verbs)} verbs. Give "verbs" {len(verbs)} '
            "objects, in this order.",
            "",
            *(f"{number} {verb}" for number, verb in enumerate(verbs, 1)),
        ]
    )


def build_parts_request(
    work: WorkProfile, cid: str, verbs: Sequence[str]
) -> Dict[str, Any]:
    """One Message Batches request ({custom_id, params}) for some verbs."""
    return {
        "custom_id": cid,
        "params": {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            # Thinking is always on for Opus 5.5; effort is set explicitly.
            "output_config": {
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": PARTS_SCHEMA},
            },
            "system": [
                {
                    "type": "text",
                    "text": parts_system_prompt(work),
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            "messages": [{"role": "user", "content": build_parts_prompt(verbs)}],
        },
    }


# --- Estimates ----------------------------------------------------------------


@dataclass
class PartsEstimate:
    requests: int
    verbs: int
    input_tokens: int
    output_tokens: int
    output_per_verb: float
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


def estimate_parts(
    requests: Sequence[Mapping[str, Any]],
    verb_count: int,
    output_per_verb: float = DEFAULT_OUTPUT_PER_VERB,
) -> PartsEstimate:
    """The estimated tokens and batch cost of some parts requests."""
    return PartsEstimate(
        requests=len(requests),
        verbs=verb_count,
        input_tokens=sum(estimate_input_tokens(r) for r in requests),
        output_tokens=round(verb_count * output_per_verb),
        output_per_verb=output_per_verb,
    )


# --- Reading results ------------------------------------------------------------


@dataclass
class PartsOutcome:
    """
    A parts batch result: the parts of each verb that passed the checks (as
    stored in verb_parts), the verbs that failed and why, or why the whole
    request failed; and its usage.
    """

    custom_id: str
    usage: Usage
    parts: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    failed: Dict[str, str] = field(default_factory=dict)  # verb → reason
    failure: Optional[str] = None  # the whole request

    @property
    def summary(self) -> Optional[str]:
        """Why the request is not fully stored, or None if it is."""
        if self.failure:
            return self.failure
        if not self.failed:
            return None
        n, total = len(self.failed), len(self.failed) + len(self.parts)
        reasons = "; ".join(f"{v}: {r}" for v, r in self.failed.items())
        return f"{n} of {total} verbs not stored: {reasons}"


def _key(verb: Any) -> str:
    return unicodedata.normalize("NFC", verb).strip() if isinstance(verb, str) else ""


def _check_parts(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(PART_NAMES):
        n = len(value) if isinstance(value, list) else 0
        raise ValueError(f"{n} parts, not {len(PART_NAMES)}")
    parts = []
    for number, (item, name) in enumerate(zip(value, PART_NAMES), 1):
        form = item.get("form") if isinstance(item, dict) else None
        if not isinstance(form, str) or not form.strip():
            raise ValueError(f"part {number} has no form")
        note = item.get("note")
        parts.append(
            {
                "part": number,
                "name": name,
                "form": unicodedata.normalize("NFC", form.strip()),
                "notes": note.strip() if isinstance(note, str) else "",
            }
        )
    return parts


def check_parts_output(
    data: Any, verbs: Sequence[str]
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str]]:
    """
    A result's JSON checked against the verbs asked for: each verb's parts
    (found by the verb it repeats, so a skipped verb fails alone), and the
    verbs that failed and why. A verb repeated with other accents or a
    diaeresis (ἀναίσσω as ἀναΐσσω) is found when no other verb asked for or
    answered reads the same unaccented. Raises ValueError if the output has
    no list.
    """
    if not isinstance(data, dict) or not isinstance(data.get("verbs"), list):
        raise ValueError("the output has no list of verbs")
    answers: Dict[str, Any] = {}
    for item in data["verbs"]:
        if isinstance(item, dict):
            answers.setdefault(_key(item.get("verb")), item.get("parts"))
    asked = {_key(verb) for verb in verbs}
    unaccented: Dict[str, List[str]] = {}
    for key in answers.keys() - asked:
        unaccented.setdefault(plain(key), []).append(key)
    parts, failed = {}, {}
    for verb in verbs:
        key = _key(verb)
        if key not in answers:
            same = unaccented.get(plain(key), [])
            if len(same) != 1 or sum(plain(k) == plain(key) for k in asked) != 1:
                failed[verb] = "not in the output"
                continue
            key = same[0]
        try:
            parts[verb] = _check_parts(answers[key])
        except ValueError as e:
            failed[verb] = str(e)
    return parts, failed


def read_parts_result(
    entry: Any, verbs: Sequence[str], model: str = MODEL
) -> PartsOutcome:
    """
    One entry of a parts batch's results, checked against its verbs. The
    request fails on an API error, expiry, cancellation, a refusal,
    truncation at max_tokens and output that isn't JSON; a verb fails alone
    when it is missing or doesn't have six parts. A failed message is still
    billed, so its usage is kept.
    """
    result = entry.result
    if result.type == "errored":
        error = result.error.error
        return PartsOutcome(
            entry.custom_id, Usage(), failure=f"error: {error.type}: {error.message}"
        )
    if result.type in ("expired", "canceled"):
        return PartsOutcome(entry.custom_id, Usage(), failure=result.type)
    message = result.message
    usage = usage_of(message.usage, model)
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None)
        reason = f"refusal ({category})" if category else "refusal"
        return PartsOutcome(entry.custom_id, usage, failure=reason)
    if message.stop_reason == "max_tokens":
        return PartsOutcome(entry.custom_id, usage, failure="truncated at max_tokens")
    text = "".join(b.text for b in message.content if b.type == "text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return PartsOutcome(
            entry.custom_id, usage, failure="the output is not valid JSON"
        )
    try:
        parts, failed = check_parts_output(data, verbs)
    except ValueError as e:
        return PartsOutcome(entry.custom_id, usage, failure=str(e))
    return PartsOutcome(entry.custom_id, usage, parts=parts, failed=failed)
