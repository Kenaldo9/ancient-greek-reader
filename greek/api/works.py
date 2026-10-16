"""
What the study batches (greek study) need to know about each work: how its
prompts name it, whether it is verse or prose, the dialect its dictionary
forms and principal parts are made in, what its notes look out for, the
dialect-specific rules of its forms and parts prompts, and the size of a
translation unit. Profiles are keyed by tb_sources.work (WORKS in
greek/fetch/treebank.py); the prompts that use them are in greek/api/study.py,
uses.py, forms.py and parts.py.

The Iliad's prompts are the ones its Books 1–7 were made with, byte for byte
(tests/unit/test_work_prompts.py), so Books 8–24 match them. The Odyssey uses
the same Homeric prompts with its own name and themes. Herodotus and
Thucydides have prose prompts.
"""

from dataclasses import dataclass
from typing import Mapping, Optional

# The dialects that forms and principal parts are keyed by (lemma_forms and
# verb_parts), with how the reader names them. A Verbs-page lookup has none ("").
DIALECTS: Mapping[str, str] = {
    "homeric": "Homeric",
    "ionic": "Ionic",
    "attic": "Attic",
}


@dataclass(frozen=True)
class WorkProfile:
    """
    A work as the study prompts see it. title names a request's passage
    ("Iliad 1.1–1.7"); reading completes "a reader working through … in
    Greek"; author completes "the constructions it takes in …". note_topics
    is the note rule's list of what makes a word worth a closer look. A verse
    work's units are whole sentences up to max_lines lines; a prose work's
    are whole sections within a chapter, up to max_words words. The prose
    fields fill the prose prompts' dialect rules; a verse work uses the
    Iliad's wording.
    """

    key: str
    title: str
    reading: str
    author: str
    verse: bool
    dialect: str
    note_topics: str
    max_lines: Optional[int] = None
    max_words: Optional[int] = None
    # Prose only: the expert's speciality and the language forms follow
    language: str = ""
    # parse_check: the treebank's spelling conventions for this dialect
    lemma_convention: str = ""
    # forms: how GLAUx spells its lemmas, the headword and forms rules, examples
    lemma_spelling: str = ""
    headword_rule: str = ""
    forms_rule: str = ""
    noun_example: str = ""
    adjective_example: str = ""
    name_example: str = ""
    plural_example: str = ""
    # parts: which form to give, and what a note says
    parts_form_rule: str = ""
    parts_note_rule: str = ""
    # uses: an accusative of respect the treebank may call an object
    respect_example: str = ""

    @property
    def unit_name(self) -> str:
        """What a line of the work is: a verse line or a prose section."""
        return "line" if self.verse else "section"

    def count(self, n: int) -> str:
        """
        "7 lines" or "3 sections" for a request's first line. A verse count is
        always plural ("1 lines"), as the Iliad's Books 1–7 were asked.
        """
        if self.verse:
            return f"{n} lines"
        return f"{n} section" if n == 1 else f"{n} sections"

    @property
    def unit_size(self) -> dict:
        """group_units()'s size arguments for this work."""
        if self.verse:
            return {"max_lines": self.max_lines}
        return {"max_words": self.max_words}


_HOMERIC_TOPICS = (
    "epic and unusual forms; names, epithets and formulas, with the story, "
    "people and places behind them; rare words, and words whose sense here "
    "differs from the usual one; syntax a reader could misread (what the word "
    "agrees with or depends on, an unusual case or construction); words that "
    "carry the poem's themes ({themes}); a parallel elsewhere in Homer when it "
    "helps"
)

ILIAD = WorkProfile(
    key="iliad",
    title="Iliad",
    reading="the Iliad",
    author="Homer",
    verse=True,
    dialect="homeric",
    note_topics=_HOMERIC_TOPICS.format(themes="μῆνις, κλέος, τιμή"),
    max_lines=10,
)

ODYSSEY = WorkProfile(
    key="odyssey",
    title="Odyssey",
    reading="the Odyssey",
    author="Homer",
    verse=True,
    dialect="homeric",
    note_topics=_HOMERIC_TOPICS.format(themes="νόστος, ξεῖνος, μῆτις"),
    max_lines=10,
)

HERODOTUS = WorkProfile(
    key="herodotus",
    title="Herodotus",
    reading="Herodotus' Histories",
    author="Herodotus",
    verse=False,
    dialect="ionic",
    note_topics=(
        "Ionic forms, with the Attic form beside them (ἱστορίης, Attic "
        "ἱστορίας); names of people, peoples and places, with the story and "
        "history behind them; rare words, and words whose sense here differs "
        "from the usual one; syntax a reader could misread (what the word "
        "agrees with or depends on, an unusual case or construction); "
        "Herodotus' sources and his own voice (λέγουσι, ὡς ἐμοὶ δοκέει), and "
        "words that carry his themes (ἱστορίη, νόμος, τίσις, φθόνος); a "
        "parallel elsewhere in Herodotus, or in Homer, when it helps"
    ),
    max_words=80,
    language="the Ionic prose of Herodotus",
    lemma_convention=(
        "a lemma given in its Attic form for an Ionic word (ἀφικνέομαι for "
        "ἀπικνέομαι, μόνος for μοῦνος)"
    ),
    lemma_spelling=(
        "GLAUx gives lemmas in their Attic form (ἱστορία, ἀφικνέομαι), and "
        "Herodotus' forms in his Ionic (ἱστορίης, ἀπικνέονται)."
    ),
    headword_rule=(
        "Give Herodotus' Ionic form where it differs from the Attic one, in the "
        'nominative and the genitive alike ("ἡμέρη, -ης, ἡ"; "βασιλεύς, -έος, '
        'ὁ").'
    ),
    forms_rule=(
        'the Attic form when the headword\'s is Ionic ("Attic ἡμέρα, -ας"), and '
        "Ionic forms a reader might not recognise among those listed or common "
        'in Herodotus ("gen. pl. -έων; dat. pl. -ῃσι").'
    ),
    noun_example="ἱστορίη, -ης, ἡ",
    adjective_example="δίκαιος, -η, -ον",
    name_example="Κροῖσος, -ου, ὁ",
    plural_example="Πέρσαι, -έων, οἱ",
    parts_form_rule=(
        "Give the Ionic form a reader of Herodotus meets where his differs from "
        "the Attic one (ἀπικνέομαι, ἀπίξομαι, ἀπικόμην); otherwise the Attic "
        "form."
    ),
    parts_note_rule=(
        'the Attic form when the form given is Ionic ("Attic ἀφικόμην"), '
        '"middle in form", "rare", "poetic", "not in Herodotus", or the verb a '
        "suppletive part comes from."
    ),
    respect_example='τὸ εἶδος, "in appearance"',
)

THUCYDIDES = WorkProfile(
    key="thucydides",
    title="Thucydides",
    reading="Thucydides' History",
    author="Thucydides",
    verse=False,
    dialect="attic",
    note_topics=(
        "Thucydides' own spellings (ξύν for σύν, ἐς for εἰς, -σσ- for -ττ-) "
        "and his old or poetic words; names of people, peoples and places, "
        "with the history behind them; rare words, and words whose sense here "
        "differs from the usual one, above all his abstract nouns and the "
        "neuter adjectives and participles he uses as nouns (τὸ δεδιός); "
        "syntax a reader could misread in his compressed sentences (what the "
        "word agrees with or depends on, an unusual case or construction, a "
        "word to be understood); in a speech, who speaks and what the word "
        "does for the argument; words that carry his themes (δύναμις, ἀνάγκη, "
        "γνώμη, τύχη, λόγος against ἔργον); a parallel elsewhere in "
        "Thucydides when it helps"
    ),
    max_words=80,
    language="the Attic prose of Thucydides",
    lemma_convention=(
        "a lemma given in its usual spelling for one of Thucydides' (σύν for "
        "ξύν, εἰς for ἐς)"
    ),
    lemma_spelling=(
        "GLAUx gives lemmas in their usual Attic spelling (σύν, συμμαχία), "
        "and Thucydides' forms in his own (ξύν, ξυμμαχίας)."
    ),
    headword_rule=(
        "Give the Attic form as LSJ spells it; Thucydides' own spelling (ξυν-, "
        'ἐς) goes in "forms".'
    ),
    forms_rule=(
        "Thucydides' own spelling when it differs from the headword's (\"Thuc. "
        'ξυμμαχία"), and forms a reader might not recognise among those '
        "listed."
    ),
    noun_example="γνώμη, -ης, ἡ",
    adjective_example="δίκαιος, -α, -ον",
    name_example="Περικλῆς, -έους, ὁ",
    plural_example="Ἀθηναῖοι, -ων, οἱ",
    parts_form_rule="Give the Attic form, in its usual spelling.",
    parts_note_rule=(
        'Thucydides\' spelling when his differs ("Thuc. ξυνέγραψα"), "middle in '
        'form", "rare", "poetic", "not in Thucydides", or the verb a '
        "suppletive part comes from."
    ),
    respect_example='τὴν γνώμην, "in judgement"',
)

PROFILES: Mapping[str, WorkProfile] = {
    w.key: w for w in (ILIAD, ODYSSEY, HERODOTUS, THUCYDIDES)
}


def work_profile(work: Optional[str]) -> WorkProfile:
    """
    The profile of a tb_sources.work key. A text whose import recorded no
    work (before migration 018) is read as the Perseus treebank, which in
    practice means the Iliad, so it gets the Iliad's.
    """
    if work is None:
        return ILIAD
    try:
        return PROFILES[work]
    except KeyError:
        raise ValueError(f"No study profile for the work {work!r}.") from None
