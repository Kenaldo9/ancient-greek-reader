import time

import pytest

from greek.web.formatting import (
    condensed_parse,
    count_noun,
    format_date,
    format_time,
    greek_spans,
    line_unit,
    number_ranges,
    parse_items,
    ref_range,
    slash_breaks,
)


@pytest.fixture
def helsinki(monkeypatch):
    """Run in UTC+3 (summer), so UTC timestamps late in the day roll over."""
    monkeypatch.setenv("TZ", "Europe/Helsinki")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


# --- format_date ---


def test_format_date_day_month_year(helsinki):
    assert format_date("2026-04-08 14:06:07") == "8 Apr 2026"


def test_format_date_converts_utc_to_local(helsinki):
    # 22:06 UTC is already the next day in Helsinki
    assert format_date("2026-04-08 22:06:07") == "9 Apr 2026"


def test_format_date_no_leading_zero(helsinki):
    assert format_date("2026-10-03 10:44:02") == "3 Oct 2026"


def test_format_date_empty():
    assert format_date(None) == ""
    assert format_date("") == ""


def test_format_date_unparseable_returned_as_is():
    assert format_date("yesterday") == "yesterday"


# --- format_time ---


def test_format_time_local_hours_and_minutes(helsinki):
    assert format_time("2026-04-08 14:06:07") == "17:06"


def test_format_time_pads_hours(helsinki):
    assert format_time("2026-04-08 04:05:00") == "07:05"


def test_format_time_empty_and_unparseable():
    assert format_time(None) == ""
    assert format_time("yesterday") == "yesterday"


# --- count_noun ---


def test_count_noun_plural_with_thousands_separator():
    assert count_noun(15687, "line") == "15,687 lines"


def test_count_noun_singular():
    assert count_noun(1, "text") == "1 text"


def test_count_noun_zero_is_plural():
    assert count_noun(0, "lookup") == "0 lookups"


# --- ref_range ---


def test_ref_range_two_refs_use_en_dash():
    assert ref_range("1.1", "1.7") == "1.1–1.7"


def test_ref_range_single_ref():
    assert ref_range("1.1.0", "1.1.0") == "1.1.0"
    assert ref_range("1.1", None) == "1.1"


def test_ref_range_missing():
    assert ref_range(None, None) == ""


# --- line_unit ---


def test_line_unit_verse():
    assert line_unit("1.1") == "line"


def test_line_unit_prose():
    assert line_unit("1.1.0") == "section"


def test_line_unit_no_reference():
    assert line_unit(None) == "line"


# --- condensed_parse ---


def test_condensed_parse_noun():
    morph = {"case": "accusative", "number": "singular", "gender": "feminine"}
    assert condensed_parse("noun", morph) == "noun · acc. sg. fem."


def test_condensed_parse_finite_verb():
    morph = {
        "person": 2,
        "number": "singular",
        "tense": "present",
        "mood": "imperative",
        "voice": "active",
    }
    assert condensed_parse("verb", morph) == "verb · 2nd sg. pres. imper. act."


def test_condensed_parse_compound_voice():
    morph = {
        "person": "3rd",
        "number": "singular",
        "tense": "imperfect",
        "mood": "indicative",
        "voice": "middle/passive",
    }
    assert condensed_parse("verb", morph) == "verb · 3rd sg. impf. ind. mid./pass."


def test_condensed_parse_participle_does_not_repeat_mood():
    morph = {
        "tense": "aorist",
        "voice": "active",
        "mood": "participle",
        "case": "nominative",
        "number": "dual",
        "gender": "masculine",
    }
    assert (
        condensed_parse("participle", morph) == "participle · aor. act. nom. du. masc."
    )


def test_condensed_parse_uninflected():
    assert condensed_parse("particle", {}) == "particle"
    assert condensed_parse("particle", None) == "particle"


def test_condensed_parse_unknown_value_kept():
    assert condensed_parse("noun", {"case": "locative"}) == "noun · locative"


# --- parse_items ---


def test_parse_items_reading_order_and_labels():
    morph = {
        "gender": "feminine",
        "case": "accusative",
        "voice": "middle",
        "tense": "aorist",
        "number": "singular",
    }
    assert parse_items(morph) == [
        ("Tense", "aorist"),
        ("Voice", "middle"),
        ("Case", "accusative"),
        ("Number", "singular"),
        ("Gender", "feminine"),
    ]


def test_parse_items_person_and_free_text():
    items = parse_items({"person": "3", "dialect_form": "Ionic -εω"})
    assert items == [("Person", "3rd"), ("Dialect form", "Ionic -εω")]


def test_slash_breaks_lets_a_value_wrap_after_a_slash():
    assert slash_breaks("middle/passive") == "middle/<wbr>passive"
    assert slash_breaks("aorist") == "aorist"
    assert slash_breaks(None) == ""
    # the value is escaped; only the break is markup
    assert slash_breaks("<b>/x") == "&lt;b&gt;/<wbr>x"


def test_greek_spans_marks_greek_for_the_greek_font():
    assert greek_spans("dative with ἐφίημι (target)") == (
        'dative with <span lang="grc">ἐφίημι</span> (target)'
    )
    # Greek words next to each other are one run; elision marks stay in it
    assert greek_spans("with ἐκ Διὸς καὶ Ἥρης, δηθύνοντʼ") == (
        'with <span lang="grc">ἐκ Διὸς καὶ Ἥρης</span>, '
        '<span lang="grc">δηθύνοντʼ</span>'
    )
    assert greek_spans("+ acc., have") == "+ acc., have"
    assert greek_spans(None) == ""
    # the text is escaped; only the spans are markup
    assert greek_spans("one's <b>") == "one&#39;s &lt;b&gt;"


# --- number_ranges ---


@pytest.mark.parametrize(
    "numbers, text",
    [
        (["1"], "1"),
        (["1", "2", "3"], "1–3"),
        (["1", "2", "3", "5", "7", "8"], "1–3, 5, 7–8"),
        (["9", "10", "11"], "9–11"),
        (["A", "B"], "A, B"),
        ([], ""),
    ],
)
def test_number_ranges(numbers, text):
    assert number_ranges(numbers) == text
