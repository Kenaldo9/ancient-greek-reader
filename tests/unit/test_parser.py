from pathlib import Path


from greek.fetch.parser import extract_entries

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _iliad_xml() -> bytes:
    return (FIXTURES / "iliad_excerpt.xml").read_bytes()


def _herodotus_xml() -> bytes:
    return (FIXTURES / "herodotus_excerpt.xml").read_bytes()


def test_iliad_returns_list_of_tuples():
    entries = extract_entries(_iliad_xml())
    assert isinstance(entries, list)
    assert all(isinstance(e, tuple) and len(e) == 2 for e in entries)


def test_iliad_poetry_refs():
    entries = extract_entries(_iliad_xml())
    refs = [ref for ref, _ in entries]
    assert "1.1" in refs
    assert "1.2" in refs
    assert "2.1" in refs


def test_iliad_poetry_entry_count():
    entries = extract_entries(_iliad_xml())
    assert len(entries) == 5  # 3 lines in book 1, 2 in book 2


def test_iliad_poetry_content():
    entries = extract_entries(_iliad_xml())
    first = dict(entries)["1.1"]
    assert "Μῆνιν" in first


def test_herodotus_prose_refs():
    entries = extract_entries(_herodotus_xml())
    refs = [ref for ref, _ in entries]
    assert "1.1.1" in refs
    assert "1.1.2" in refs
    assert "1.2.1" in refs
    assert "2.1.1" in refs


def test_herodotus_prose_entry_count():
    entries = extract_entries(_herodotus_xml())
    assert len(entries) == 4


def test_herodotus_urn_prefix_excluded():
    entries = extract_entries(_herodotus_xml())
    refs = [ref for ref, _ in entries]
    # No ref should start with "urn"
    assert not any(r.startswith("urn") for r in refs)


def test_herodotus_prose_content():
    entries = extract_entries(_herodotus_xml())
    entry_dict = dict(entries)
    assert "Ἡροδότου" in entry_dict["1.1.1"]


def test_empty_xml_returns_empty_list():
    xml = b'<?xml version="1.0"?><TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body></body></text></TEI>'
    assert extract_entries(xml) == []


def test_iliad_poetry_detected_not_prose():
    """Iliad should be detected as poetry (numbered <l> elements present)."""
    entries = extract_entries(_iliad_xml())
    # Poetry entries have refs like "book.line" with exactly one dot
    assert all(ref.count(".") == 1 for ref, _ in entries)


def test_herodotus_prose_detected_not_poetry():
    """Herodotus should be detected as prose (no numbered <l> elements)."""
    entries = extract_entries(_herodotus_xml())
    # Prose entries have refs like "book.chapter.section" with two dots
    assert all(ref.count(".") == 2 for ref, _ in entries)


def _prose(sections: str) -> bytes:
    return (
        '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
        '<div n="urn:cts:greekLit:tlg0016.tlg001" type="edition">'
        f'<div n="1" type="book"><div n="4" type="chapter">{sections}</div></div>'
        "</div></body></text></TEI>"
    ).encode()


def test_footnote_markers_are_left_out():
    """Herodotus 1.4.4 and 1.76.1: a <note> holds an editor's footnote number."""
    entries = dict(
        extract_entries(
            _prose(
                '<div n="4" type="section"><p>ἔθνεα βάρβαρα '
                '<note anchored="true" resp="ed" place="unspecified">1</note> '
                "οἰκηιεῦνται οἱ Πέρσαι, ἐς τὸ<note>1</note> ὄρος</p></div>"
            )
        )
    )
    assert entries["1.4.4"] == "ἔθνεα βάρβαρα οἰκηιεῦνται οἱ Πέρσαι, ἐς τὸ ὄρος"


def test_a_footnote_between_words_still_separates_them():
    """Herodotus 4.167.3 and 3.81.2: no space around the <note> in the TEI."""
    entries = dict(
        extract_entries(
            _prose(
                '<div n="1" type="section"><p>Δαρείου<note>1</note>οὐδέν. '
                "οἰκήιον,<note>1</note>ὠθέει</p></div>"
            )
        )
    )
    assert entries["1.4.1"] == "Δαρείου οὐδέν. οἰκήιον, ὠθέει"


def test_citations_of_quoted_passages_are_left_out():
    """Herodotus 2.116.3 quotes Homer; Thucydides 1.9.4 glues the citation on."""
    entries = dict(
        extract_entries(
            _prose(
                '<div n="1" type="section"><p>ἐν Ἰλιάδι·</p>\n<cit><quote>'
                "<l>ἔνθʼ ἔσαν οἱ πέπλοι</l></quote>"
                '<bibl n="Hom. Il. 6.289-292">homer, Iliad, 6.289-292</bibl></cit></div>'
                '<div n="2" type="section"><p>λέγει· <bibl n="Hom. Il. 2.108" '
                'default="false">Hom. Il. 2.108</bibl>οὐκ ἂν οὖν</p></div>'
            )
        )
    )
    assert entries["1.4.1"] == "ἐν Ἰλιάδι· ἔνθʼ ἔσαν οἱ πέπλοι"
    assert entries["1.4.2"] == "λέγει· οὐκ ἂν οὖν"


def test_a_choice_keeps_the_correction_not_the_misprint():
    entries = dict(
        extract_entries(
            _prose(
                '<div n="1" type="section"><p>ἀλλά μιν <choice><sic>αἱ</sic> '
                "<corr>οἱ</corr></choice> ἱρέες, <sic>οὕτω</sic> θάπτουσι</p></div>"
            )
        )
    )
    # A <sic> outside a <choice> is the edition's text ("thus in the source")
    assert entries["1.4.1"] == "ἀλλά μιν οἱ ἱρέες, οὕτω θάπτουσι"


def test_a_footnote_in_a_verse_line_is_left_out():
    xml = (
        '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
        '<div n="urn:cts:greekLit:tlg0012.tlg002" type="edition"><div n="1" type="book">'
        '<l n="1">ἄνδρα μοι ἔννεπε,<note>1</note> μοῦσα</l></div></div>'
        "</body></text></TEI>"
    ).encode()
    assert extract_entries(xml) == [("1.1", "ἄνδρα μοι ἔννεπε, μοῦσα")]
