"""Which principal part a verb form comes from (greek/web/reader_data.py)."""

import pytest

from greek.postag import decode
from greek.web.reader_data import PrincipalPart, principal_part_slot


def _parts(*forms):
    return [PrincipalPart(name=str(n), form=f) for n, f in enumerate(forms, 1)]


TITHEMI = _parts("τίθημι", "θήσω", "ἔθηκα", "τέθηκα", "τέθειμαι", "ἐτέθην")


@pytest.mark.parametrize(
    "postag, slot",
    [
        ("v1spia---", 1),  # present
        ("v3siia---", 1),  # imperfect
        ("v3spie---", 1),  # present middle/passive
        ("v1sfia---", 2),  # future active
        ("v1sfim---", 2),  # future middle
        ("v1sfip---", 6),  # future passive
        ("v3saia---", 3),  # aorist active
        ("v3saim---", 3),  # aorist middle
        ("v3saip---", 6),  # aorist passive
        ("v3sria---", 4),  # perfect active
        ("v3slia---", 4),  # pluperfect active
        ("v3srie---", 5),  # perfect middle/passive
        ("v3slip---", 5),  # pluperfect passive
        ("v3stie---", 5),  # future perfect
        ("v-sapamn-", 3),  # an aorist participle
        ("v--rna---", 4),  # a perfect infinitive
    ],
)
def test_every_tense_and_voice(postag, slot):
    assert principal_part_slot(decode(postag)[1], TITHEMI) == slot


def test_a_deponents_middle_form_fills_the_slot_its_parts_give_it():
    boulomai = _parts(
        "βούλομαι", "βουλήσομαι", "—", "βέβουλα", "βεβούλημαι", "ἐβουλήθην"
    )
    # an aorist tagged middle, of a verb whose aorist is the sixth part
    assert principal_part_slot(decode("v3saim---")[1], boulomai) == 6
    machomai = _parts("μάχομαι", "μαχήσομαι", "ἐμαχεσάμην", "—", "μεμάχημαι", "—")
    assert principal_part_slot(decode("v3saip---")[1], machomai) == 3
    # a perfect tagged active when only the middle perfect is given
    assert principal_part_slot(decode("v3sria---")[1], machomai) == 5


def test_an_empty_slot_highlights_nothing():
    choomai = _parts("χώομαι", "χώσομαι", "ἐχωσάμην", "—", "—", "—")
    assert principal_part_slot(decode("v3srie---")[1], choomai) is None
    assert principal_part_slot(decode("v3sria---")[1], choomai) is None
    assert principal_part_slot({}, choomai) is None  # no tense


def test_a_suppletive_lemma_comes_from_the_part_that_is_the_lemma():
    eipon = _parts("—", "ἐρέω", "εἶπον", "εἴρηκα", "εἴρημαι", "ἐρρήθην")
    # ἔειπε tagged imperfect: the present slot is empty
    assert principal_part_slot(decode("v3siia---")[1], eipon, "εἶπον") == 3
    assert principal_part_slot(decode("v3siia---")[1], eipon, "") is None
    # an ordinary form still goes by its tense
    assert principal_part_slot(decode("v3sfia---")[1], eipon, "εἶπον") == 2


def test_parts_with_fewer_than_six_forms():
    assert principal_part_slot(decode("v3saip---")[1], _parts("τίθημι", "θήσω")) is None
