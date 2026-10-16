"""
Work profiles (greek/api/works.py) and the prompts made from them. The Iliad's
requests must stay byte for byte as Books 1–7 were made:
tests/fixtures/iliad_requests.json was written by the code before profiles
existed (commit dda40e6), from the inputs below.
"""

import json
from pathlib import Path

import pytest

from greek.api.forms import (
    FORMS_SYSTEM_PROMPT,
    FormsLemma,
    build_forms_request,
    forms_system_prompt,
)
from greek.api.parts import (
    PARTS_SYSTEM_PROMPT,
    build_parts_request,
    parts_system_prompt,
)
from greek.api.study import (
    NOTES_SYSTEM_PROMPT,
    STUDY_SYSTEM_PROMPT,
    build_notes_request,
    build_request,
    notes_system_prompt,
    study_system_prompt,
    study_words,
)
from greek.api.uses import (
    PREFERRED_NAMES,
    PROSE_NAMES,
    USES_SYSTEM_PROMPT,
    build_uses_request,
    uses_system_prompt,
)
from greek.api.works import (
    DIALECTS,
    HERODOTUS,
    ILIAD,
    ODYSSEY,
    PROFILES,
    THUCYDIDES,
    work_profile,
)
from greek.fetch.treebank import WORKS
from greek.models import Line, TranslationUnit, WordParse

SNAPSHOT = Path(__file__).parent.parent / "fixtures" / "iliad_requests.json"


def _parse(n, i, form, lemma, postag, part=0):
    return WordParse(12, n, i, part, form, lemma, postag, 1 if lemma else None)


LINES = [
    Line(
        id=1,
        text_id=12,
        line_number=1,
        content="μῆνιν ἄειδε, θεὰ Πηληϊάδεω",
        reference="1.1",
    ),
    Line(
        id=2,
        text_id=12,
        line_number=2,
        content="οὐδʼ Ἄϊδι προΐαψεν·",
        reference="1.2",
    ),
]
PARSES = [
    _parse(1, 0, "μῆνιν", "μῆνις", "n-s---fa-"),
    _parse(1, 1, "ἄειδε", "ἀείδω", "v2spma---"),
    _parse(1, 2, "θεὰ", "θεά", "n-s---fv-"),
    _parse(1, 3, "Πηληϊάδεω", "Πηληιάδης", "n-s---mg-"),
    _parse(2, 0, "οὐ", "οὐ", "d--------"),
    _parse(2, 0, "δ̓", "δέ", "g--------", part=1),
    _parse(2, 1, "Ἄϊδι", None, None),
    _parse(2, 2, "προΐαψεν", "προϊάπτω", "v3saia---"),
]
UNIT = TranslationUnit(12, 1, 2)
FLUENT = "Sing the wrath, goddess, of Peleus' son. Not to Hades did it send."
GLOSSES = [
    "wrath",
    "sing",
    "goddess",
    "of Peleus' son",
    "and not",
    "to Hades",
    "sent forth",
]
ROLES = ["object of ἄειδε", "main verb", "addressed", "genitive with μῆνιν", "", "", ""]
TARGETS = [None, "imperative", None, "genitive", None, None, None]
LEMMAS = [
    FormsLemma(
        "μῆνις",
        "noun",
        lsj="μῆνις, Dor. and Aeol. μᾶνις, ἡ, gen. A. μήνιος …",
        forms=["μῆνιν", "μῆνις"],
    ),
    FormsLemma(
        "Πηληιάδης",
        "noun",
        lsj="Πηλεΐδης, ου, ὁ …",
        lsj_headword="Πηλεΐδης",
        forms=["Πηληϊάδεω"],
    ),
    FormsLemma(
        "οὐλόμενος", "adjective", identification="destructive", forms=["οὐλομένην"]
    ),
    FormsLemma("Σμινθεύς", "noun"),
    FormsLemma("ἀείδω", "verb", meaning="sing", forms=["ἄειδε"]),
]
VERBS = ["ἀείδω", "προϊάπτω", "εἶπον"]


def _words(glossed=False, roles=False):
    words = study_words(LINES, PARSES, {(1, 0), (1, 1), (2, 2)})
    if glossed:
        for w, gloss in zip(words, GLOSSES):
            w.gloss = gloss
    if roles:
        for w, role, target in zip(words, ROLES, TARGETS):
            w.role, w.target = role, target
    return words


def _iliad_requests():
    return {
        "study": build_request(ILIAD, UNIT, LINES, _words(), "high"),
        "study_medium": build_request(ILIAD, UNIT, LINES, _words()),
        "notes": build_notes_request(
            ILIAD, UNIT, LINES, FLUENT, _words(glossed=True), "high"
        ),
        "uses": build_uses_request(
            ILIAD, UNIT, LINES, FLUENT, _words(glossed=True, roles=True), "high"
        ),
        "forms": build_forms_request(ILIAD, "12_forms_1", LEMMAS),
        "parts": build_parts_request(ILIAD, "12_parts_1", VERBS),
    }


@pytest.mark.parametrize(
    "kind", ["study", "study_medium", "notes", "uses", "forms", "parts"]
)
def test_the_iliads_requests_are_byte_identical_to_books_1_to_7(kind):
    expected = json.loads(SNAPSHOT.read_text())[kind]
    assert _iliad_requests()[kind] == expected
    # the same bytes, not only equal values
    assert json.dumps(_iliad_requests()[kind], ensure_ascii=False) == json.dumps(
        expected, ensure_ascii=False
    )


def test_the_iliads_system_prompts_are_the_module_constants():
    assert study_system_prompt(ILIAD) == STUDY_SYSTEM_PROMPT
    assert notes_system_prompt(ILIAD) == NOTES_SYSTEM_PROMPT
    assert uses_system_prompt(ILIAD) == USES_SYSTEM_PROMPT
    assert forms_system_prompt(ILIAD) == FORMS_SYSTEM_PROMPT
    assert parts_system_prompt(ILIAD) == PARTS_SYSTEM_PROMPT


# --- Profiles -------------------------------------------------------------------


def test_every_treebank_work_has_a_profile_with_a_dialect():
    assert set(PROFILES) == set(WORKS)
    for key, work in PROFILES.items():
        assert work.key == key
        assert work.dialect in DIALECTS
        assert work.verse == (work.max_lines is not None)
        assert work.verse != (work.max_words is not None)
    assert [PROFILES[k].dialect for k in WORKS] == [
        "homeric",
        "homeric",
        "ionic",
        "attic",
    ]


def test_a_text_without_a_recorded_work_reads_as_the_iliad():
    assert work_profile(None) is ILIAD
    assert work_profile("herodotus") is HERODOTUS
    with pytest.raises(ValueError, match="No study profile"):
        work_profile("aeneid")


def test_counts_and_unit_sizes():
    assert ILIAD.count(1) == "1 lines"  # as the Iliad's Books 1–7 were asked
    assert ILIAD.count(7) == "7 lines"
    assert HERODOTUS.count(1) == "1 section"
    assert THUCYDIDES.count(3) == "3 sections"
    assert ILIAD.unit_size == {"max_lines": 10}
    assert HERODOTUS.unit_size == {"max_words": 80}


@pytest.mark.parametrize("work", [HERODOTUS, THUCYDIDES])
def test_every_prose_field_is_filled(work):
    for name, value in vars(work).items():
        if name not in ("max_lines",):
            assert value not in ("", None), name


# --- The Odyssey: the Iliad's prompts with its own name ---------------------------


def test_the_odysseys_prompts_are_the_iliads_with_its_name_and_themes():
    def as_odyssey(text):
        return (
            text.replace("the Iliad", "the Odyssey")
            .replace("Iliad: ", "Odyssey: ")
            .replace("μῆνις, κλέος, τιμή", "νόστος, ξεῖνος, μῆτις")
        )

    assert study_system_prompt(ODYSSEY) == as_odyssey(STUDY_SYSTEM_PROMPT)
    assert notes_system_prompt(ODYSSEY) == as_odyssey(NOTES_SYSTEM_PROMPT)
    assert uses_system_prompt(ODYSSEY) == as_odyssey(USES_SYSTEM_PROMPT)
    assert forms_system_prompt(ODYSSEY) == as_odyssey(FORMS_SYSTEM_PROMPT)
    assert parts_system_prompt(ODYSSEY) == as_odyssey(PARTS_SYSTEM_PROMPT)
    for prompt in (study_system_prompt(ODYSSEY), forms_system_prompt(ODYSSEY)):
        assert "Iliad" not in prompt
    message = build_forms_request(ODYSSEY, "13_forms_1", LEMMAS)["params"]
    assert "| Odyssey: μῆνιν, μῆνις" in message["messages"][0]["content"]


# --- The prose prompts -------------------------------------------------------------


@pytest.mark.parametrize("work", [HERODOTUS, THUCYDIDES])
def test_prose_prompts_speak_of_sections_glaux_and_the_work(work):
    prompts = {
        "study": study_system_prompt(work),
        "notes": notes_system_prompt(work),
        "uses": uses_system_prompt(work),
        "forms": forms_system_prompt(work),
        "parts": parts_system_prompt(work),
    }
    for kind, prompt in prompts.items():
        assert work.reading in prompt, kind
        assert "GLAUx corpus" in prompt, kind
        for homeric in ("Iliad", "Perseus", "Homeric epic", "{", "}"):
            assert homeric not in prompt, (kind, homeric)
    for kind in ("study", "notes", "uses"):
        assert "book.chapter.section" in prompts[kind]
    assert "exactly one string per section" in prompts["study"]
    assert work.note_topics in prompts["study"]
    assert work.note_topics in prompts["notes"]
    for kind in ("study", "notes", "uses"):  # GLAUx's δέ is a conjunction
        assert 'δέ, conjunction"); it is still one word.' in prompts[kind]
    assert "automatic parse" in prompts["study"]
    assert "Look hardest at words parsed automatically" in prompts["study"]
    assert work.lemma_convention in prompts["study"]
    assert "about one role in five is wrong" in prompts["uses"]
    assert work.respect_example in prompts["uses"]
    assert PROSE_NAMES in prompts["uses"] and PREFERRED_NAMES not in prompts["uses"]
    assert work.headword_rule in prompts["forms"]
    assert f"it takes in {work.author}" in prompts["forms"]
    assert work.parts_form_rule in prompts["parts"]


def test_prose_use_names_drop_the_homeric_ones():
    assert "Homeric" not in PROSE_NAMES and "κε" not in PROSE_NAMES
    assert "Homeric" in PREFERRED_NAMES
    for name in (
        "dative of degree of difference",
        "dative of agent",
        "accusative absolute",
        "infinitive after πρίν",
    ):
        assert name in PROSE_NAMES


def test_herodotus_and_thucydides_differ_in_dialect():
    assert "Ionic" in forms_system_prompt(HERODOTUS)
    assert "βασιλεύς, -έος, ὁ" in forms_system_prompt(HERODOTUS)
    assert "Ionic" not in forms_system_prompt(THUCYDIDES)
    assert "ξυν-" in forms_system_prompt(THUCYDIDES)
    assert "ἀπικνέομαι, ἀπίξομαι, ἀπικόμην" in parts_system_prompt(HERODOTUS)
    assert "not in Thucydides" in parts_system_prompt(THUCYDIDES)
    assert "ἱστορίη, νόμος, τίσις, φθόνος" in study_system_prompt(HERODOTUS)
    assert "λόγος against ἔργον" in study_system_prompt(THUCYDIDES)


def test_repeat_words_asks_each_word_object_to_repeat_its_word():
    plain = study_system_prompt(HERODOTUS)
    repeat = study_system_prompt(HERODOTUS, repeat_words=True)
    assert '- "word": the numbered word, exactly as given.\n- "gloss"' in repeat
    assert '"word"' not in plain
    request = build_request(ILIAD, UNIT, LINES, _words(), "high", repeat_words=True)
    items = request["params"]["output_config"]["format"]["schema"]["properties"][
        "words"
    ]["items"]
    assert list(items["properties"])[0] == "word"
    assert items["required"] == ["word", "gloss", "note"]
