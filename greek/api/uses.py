"""
Message Batches requests for how a treebank text's words work in their
sentence ("dative of disadvantage", "optative of wish", "circumstantial
participle: causal"), from Claude Opus 5.5, one request per translation
unit. The words to label are chosen from the treebank the same way for every
book (use_target); each request gives the unit's lines, its saved fluent
translation, and every word with its lemma, parse, saved gloss and treebank
role. The model may also label an unmarked word whose use a reader could
miss. The labels are stored in word_uses. This module builds the requests
and reads their results; `greek study uses` submits them and `greek study
batch collect` stores them (greek/cli/study_cmd.py).
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from greek.api.study import (
    _AUTOMATIC,
    MODEL,
    _check_word,
    _lines_heading,
    _passage_lines,
    _request,
    _result_json,
    custom_id,
    numbered_words,
    parse_label,
    split_word_rule,
    study_words,
)
from greek.api.works import ILIAD, WorkProfile
from greek.models import Line, TranslationUnit, Usage, WordParse
from greek.syntax import SentenceSyntax, split_relation

# Output tokens per label (thinking included) until a collected batch
# measures them, by effort: the plan's estimate (the number, the repeated
# word, a short label, and thinking).
DEFAULT_OUTPUT_PER_LABEL = {"low": 50.0, "medium": 65.0, "high": 100.0}
OUTPUT_PER_LABEL_ABOVE_HIGH = 130.0

_MOODS = {
    "p": "participle",
    "n": "infinitive",
    "s": "subjunctive",
    "o": "optative",
    "m": "imperative",
}
_CASES = {"g": "genitive", "d": "dative"}
KINDS = (
    "genitive",
    "dative",
    "adverbial accusative",
    "participle",
    "infinitive",
    "subjunctive",
    "optative",
    "imperative",
)


def default_output_per_label(effort: str) -> float:
    return DEFAULT_OUTPUT_PER_LABEL.get(effort, OUTPUT_PER_LABEL_ABOVE_HIGH)


# --- Which words get a label --------------------------------------------------------


def _tree_node(p: WordParse, trees: Mapping[int, SentenceSyntax]):
    tree = trees.get(p.sentence_id) if p.sentence_id is not None else None
    if tree is None or p.node_id is None:
        return None, None
    return tree, tree.nodes.get(p.node_id)


def use_target(
    parts: Sequence[WordParse], trees: Mapping[int, SentenceSyntax]
) -> Optional[str]:
    """
    What kind of word to label a word is, from its parse and treebank node,
    or None: a genitive or dative noun, adjective or pronoun whose phrase
    doesn't depend on a preposition; such an accusative that the treebank
    calls adverbial (ADV); a participle, infinitive, subjunctive, optative
    or imperative. A word the treebank splits is one if any part is. A
    genitive, dative or accusative without a node isn't one, since its
    preposition can't be told, nor is one that agrees with a noun or stands
    in apposition to it (SentenceSyntax.agrees), since it shares the noun's
    use; the model may still add it.
    """
    for p in parts:
        tag = p.postag or ""
        if len(tag) < 9:
            continue
        pos, mood, case = tag[0], tag[4], tag[7]
        if pos == "v" and mood in _MOODS:
            return _MOODS[mood]
        if pos not in "nap" or case not in "gda":
            continue
        tree, node = _tree_node(p, trees)
        if node is None or tree.preposition(node.node_id) is not None:
            continue
        if tree.agrees(node.node_id):
            continue
        if case in _CASES:
            return _CASES[case]
        if split_relation(node.relation)[0] == "ADV":
            return "adverbial accusative"
    return None


def role_text(parts: Sequence[WordParse], trees: Mapping[int, SentenceSyntax]) -> str:
    """
    A word's role row as plain text ("object of ἄειδε"), from the first part
    with a role, as the word strip shows it; "" without one.
    """
    for p in parts:
        tree, node = _tree_node(p, trees)
        role = tree.role(node.node_id) if node is not None else None
        if role is not None:
            return role.rows[0].text
    return ""


def use_words(
    lines: Sequence[Line],
    parses: Sequence[WordParse],
    trees: Mapping[int, SentenceSyntax],
    automatic: Optional[Set[int]] = None,
):
    """
    Every word of the lines in order (study_words), with its treebank role
    and, for a word to label, what kind it is; marked as parsed
    automatically when its sentence_id is in automatic.
    """
    words = study_words(lines, parses, automatic=automatic)
    for w in words:
        w.role = role_text(w.parts, trees)
        w.target = use_target(w.parts, trees)
    return words


# --- Prompt and request ---------------------------------------------------------

PREFERRED_NAMES = """- Genitive: genitive of possession, partitive genitive, genitive of description, genitive of material, genitive of time within which, genitive of place (Homeric), genitive of separation, genitive of comparison, genitive of cause, genitive of price, objective genitive, subjective genitive, genitive absolute; with a verb, "genitive with a verb of hearing" (or of remembering, ruling, touching, aiming).
- Dative: indirect object, dative of advantage, dative of disadvantage, dative of interest, dative of possession, ethical dative, dative of means, dative of manner, dative of cause, dative of accompaniment, dative of place, dative of time, dative of respect; with a verb or adjective, "dative with ἕπομαι".
- Accusative: accusative of respect, accusative of extent (of time or space), accusative of goal (Homeric, without a preposition), cognate accusative, adverbial accusative.
- Subjunctive: hortatory subjunctive, deliberative subjunctive, prohibitive subjunctive, subjunctive of purpose, subjunctive of fearing, general subjunctive (with κε or ἄν), future more vivid protasis, anticipatory subjunctive (Homeric).
- Optative: optative of wish, potential optative (with κε or ἄν), future less vivid protasis, past general optative, optative of purpose (secondary sequence), optative of fearing (secondary sequence), optative in indirect speech.
- Imperative: imperative of command, imperative of prayer, imperative of prohibition.
- Infinitive: complementary infinitive, infinitive of purpose, infinitive of result, infinitive in indirect statement, imperatival infinitive (Homeric), epexegetic infinitive, infinitive as subject, articular infinitive.
- Participle: attributive participle, substantive participle, circumstantial participle with its kind after a comma ("circumstantial participle, temporal"; also causal, concessive, conditional, manner, means, and purpose with the future), supplementary participle, genitive absolute."""

# The prose works' names: the Homeric ones go (genitive of place, accusative
# of goal, anticipatory subjunctive; κε), and prose's own come in (genitive of
# measure, dative of degree of difference and of agent, infinitive after πρίν,
# absolute infinitive, accusative absolute).
PROSE_NAMES = """- Genitive: genitive of possession, partitive genitive, genitive of description, genitive of material, genitive of measure, genitive of time within which, genitive of separation, genitive of comparison, genitive of cause, genitive of price, objective genitive, subjective genitive, genitive absolute; with a verb or adjective, "genitive with a verb of hearing" (or of remembering, ruling, touching, aiming, sharing).
- Dative: indirect object, dative of advantage, dative of disadvantage, dative of interest, dative of possession, ethical dative, dative of means, dative of manner, dative of cause, dative of accompaniment, dative of place, dative of time, dative of respect, dative of degree of difference, dative of agent (with a perfect passive or a verbal adjective); with a verb or adjective, "dative with ἕπομαι".
- Accusative: accusative of respect, accusative of extent (of time or space), cognate accusative, adverbial accusative.
- Subjunctive: hortatory subjunctive, deliberative subjunctive, prohibitive subjunctive, subjunctive of purpose, subjunctive of fearing, general subjunctive (with ἄν), future more vivid protasis.
- Optative: optative of wish, potential optative (with ἄν), future less vivid protasis, past general optative, optative of purpose (secondary sequence), optative of fearing (secondary sequence), optative in indirect speech.
- Imperative: imperative of command, imperative of prayer, imperative of prohibition.
- Infinitive: complementary infinitive, infinitive of purpose, infinitive of result, infinitive in indirect statement, imperatival infinitive, epexegetic infinitive, infinitive as subject, articular infinitive, infinitive after πρίν, absolute infinitive (ὡς ἔπος εἰπεῖν).
- Participle: attributive participle, substantive participle, circumstantial participle with its kind after a comma ("circumstantial participle, temporal"; also causal, concessive, conditional, manner, means, and purpose with the future), supplementary participle, genitive absolute, accusative absolute."""

# Prose only: GLAUx's syntax is about 80% right where its parser annotated.
AUTOMATIC_ROLES = (
    f'Words under a heading marked "({_AUTOMATIC})", and words marked '
    f'"{_AUTOMATIC}", were parsed by GLAUx\'s parser, not by hand: about one '
    "role in five is wrong there."
)

_VERSE_USES = """You are an expert in Ancient Greek syntax and Homeric epic. You say how words work in their sentence, for a reader working through {reading} in Greek.

Each request gives a passage of whole sentences: its lines, labelled book.line, an English translation of the passage, and then every word of the passage in order, numbered from 1, with its lemma and parse from the Perseus Ancient Greek Dependency Treebank, the gloss the reader sees with it, and its role in the sentence as the treebank's tree gives it. {split} Some words are marked "→ use".

{rules}

You may also add an object, in its place among the others, for an unmarked word whose use a reader could miss, such as an accusative of respect that the treebank calls an object (χωόμενος κῆρ, "angered at heart"). Give its number and word the same way. Do not add one for a word whose use is plain.

Names:
{names}"""

_PROSE_USES = """You are an expert in Ancient Greek syntax and Greek prose. You say how words work in their sentence, for a reader working through {reading} in Greek.

Each request gives a passage of whole sentences: its sections, labelled book.chapter.section, an English translation of the passage, and then every word of the passage in order, numbered from 1, with its lemma and parse from the GLAUx corpus, the gloss the reader sees with it, and its role in the sentence as the treebank's tree gives it. {split} {automatic} Some words are marked "→ use".

{rules}

You may also add an object, in its place among the others, for an unmarked word whose use a reader could miss, such as an accusative of respect that the treebank calls an object ({example}). Give its number and word the same way. Do not add one for a word whose use is plain.

Names:
{names}"""

_USES_RULES = """Return a JSON object with one field, "uses": exactly one object per marked word, in order. Each object has:
- "n": the word's number.
- "word": the word, exactly as given.
- "use": how this form works here, in a few words: one of the names below, written exactly as listed, so that the same use reads the same in every passage. Words in brackets say when a name applies and are not part of it. When no name below fits, use the usual name of Greek grammar in the same pattern. Add a short phrase after a colon only when it helps ("genitive of separation: from the ships"). Do not repeat the parse, the gloss or the role, and say nothing of how the word is formed (its ending, spelling or dialect): the reader's notes cover that. The treebank's role can be wrong; label what the Greek does."""


def uses_system_prompt(work: WorkProfile) -> str:
    """The system prompt of a work's uses requests (the Iliad's as made for Books 1–7)."""
    if work.verse:
        return _VERSE_USES.format(
            reading=work.reading,
            split=split_word_rule(work),
            rules=_USES_RULES,
            names=PREFERRED_NAMES,
        )
    return _PROSE_USES.format(
        reading=work.reading,
        split=split_word_rule(work),
        automatic=AUTOMATIC_ROLES,
        rules=_USES_RULES,
        example=work.respect_example,
        names=PROSE_NAMES,
    )


USES_SYSTEM_PROMPT = uses_system_prompt(ILIAD)

# One schema for every request; check_uses() checks the numbers and words.
USES_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "uses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "word": {"type": "string"},
                    "use": {"type": "string"},
                },
                "required": ["n", "word", "use"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["uses"],
    "additionalProperties": False,
}


def _use_word_line(number: int, word, automatic: bool = False) -> str:
    """
    '4 Πηληϊάδεω: Πηληιάδης, noun singular masculine genitive | gloss: son of
    Peleus | role: genitive with Ἀχιλῆος → use'; with automatic, "|
    automatic parse" after the parse.
    """
    if word.needs_parse:
        fields = ["no treebank parse"]
    else:
        fields = [" + ".join(f"{p.lemma}, {parse_label(p.postag)}" for p in word.parts)]
    if automatic:
        fields.append(_AUTOMATIC)
    if word.gloss:
        fields.append(f"gloss: {word.gloss}")
    if word.role:
        fields.append(f"role: {word.role}")
    line = f"{number} {word.form}: {' | '.join(fields)}"
    return f"{line} → use" if word.target else line


def build_uses_prompt(
    work: WorkProfile, lines: Sequence[Line], fluent: str, words: Sequence
) -> str:
    """
    The user message for a unit's uses: its lines, its saved fluent
    translation, then its words with their parses, glosses and roles, the
    words to label marked.
    """
    passage, labelled = _passage_lines(work, lines)
    marked = sum(1 for w in words if w.target)
    return "\n".join(
        [
            f"{passage}: {work.count(len(lines))}, {len(words)} words, {marked} "
            f'marked. Give "uses" {marked} objects, one per marked word, in order.',
            "",
            _lines_heading(work),
            *labelled,
            "",
            "Translation:",
            fluent,
            "",
            "Words:",
            *numbered_words(words, _use_word_line),
        ]
    )


def build_uses_request(
    work: WorkProfile,
    unit: TranslationUnit,
    lines: Sequence[Line],
    fluent: str,
    words: Sequence,
    effort: str,
) -> Dict[str, Any]:
    """One Message Batches request ({custom_id, params}) for a unit's uses."""
    return _request(
        custom_id(unit),
        uses_system_prompt(work),
        USES_SCHEMA,
        build_uses_prompt(work, lines, fluent, words),
        effort,
    )


# --- Reading results ------------------------------------------------------------


@dataclass
class UseOutput:
    """A label for the nth word of a unit (from 1); extra if it wasn't marked."""

    number: int
    label: str
    extra: bool = False


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} is missing or empty")
    return value.strip()


def check_uses(data: Any, words: Sequence) -> List[UseOutput]:
    """
    A uses result's JSON checked against its unit: every marked word
    labelled once, and every label naming a word of the unit by its number
    and form (or the lemma of a part of it); a label for an unmarked word is
    an extra. Raises ValueError saying what is wrong, so a request that
    doesn't line up fails.
    """
    if not isinstance(data, dict):
        raise ValueError("the output is not a JSON object")
    items = data.get("uses")
    if not isinstance(items, list):
        raise ValueError("uses is missing")
    found: Dict[int, UseOutput] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("a use is not an object")
        number = item.get("n")
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or not 1 <= number <= len(words)
        ):
            raise ValueError(f"a use is for word {number!r}, which isn't numbered")
        word = words[number - 1]
        _check_word(
            _text(item.get("word"), f"use {number}'s word"), word, number, "use"
        )
        if number in found:
            raise ValueError(f"word {number} ({word.form}) is labelled twice")
        found[number] = UseOutput(
            number, _text(item.get("use"), f"use {number}"), word.target is None
        )
    missing = [n for n, w in enumerate(words, 1) if w.target and n not in found]
    if missing:
        named = ", ".join(
            f"{n} {words[n - 1].form} ({words[n - 1].reference})" for n in missing[:3]
        )
        more = f" and {len(missing) - 3} more" if len(missing) > 3 else ""
        raise ValueError(f"no use for marked words {named}{more}")
    return [found[n] for n in sorted(found)]


@dataclass
class UsesOutcome:
    """A uses result: the checked labels, or why it failed; and its usage."""

    custom_id: str
    usage: Usage
    uses: Optional[List[UseOutput]] = None
    failure: Optional[str] = None


def read_uses_result(entry: Any, words: Sequence, model: str = MODEL) -> UsesOutcome:
    """
    One entry of a uses batch's results, checked against its unit's words.
    Failures are those of a study result (API errors, expiry, refusals,
    truncation, output that isn't JSON) and output that doesn't line up.
    """
    usage, data, failure = _result_json(entry, model)
    if failure:
        return UsesOutcome(entry.custom_id, usage, failure=failure)
    try:
        uses = check_uses(data, words)
    except ValueError as e:
        return UsesOutcome(entry.custom_id, usage, failure=str(e))
    return UsesOutcome(entry.custom_id, usage, uses=uses)
