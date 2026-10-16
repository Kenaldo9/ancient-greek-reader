from greek.cli.import_cmd import _parse_lines


def test_plain_text_no_references():
    raw = "Line one\nLine two\nLine three"
    contents, references = _parse_lines(raw)
    assert contents == ["Line one", "Line two", "Line three"]
    assert references is None


def test_tab_separated_references_extracted():
    raw = "1.1\tFirst line\n1.2\tSecond line\n1.3\tThird line"
    contents, references = _parse_lines(raw)
    assert contents == ["First line", "Second line", "Third line"]
    assert references == ["1.1", "1.2", "1.3"]


def test_tab_separated_numeric_dot_format():
    raw = "1.1.1\tSection text\n1.1.2\tMore text\n1.2.1\tAnother"
    contents, references = _parse_lines(raw)
    assert references == ["1.1.1", "1.1.2", "1.2.1"]
    assert contents == ["Section text", "More text", "Another"]


def test_trailing_blank_lines_stripped():
    raw = "Line one\nLine two\n\n\n"
    contents, references = _parse_lines(raw)
    assert contents == ["Line one", "Line two"]
    assert references is None


def test_trailing_blank_lines_stripped_with_refs():
    raw = "1.1\tFirst\n1.2\tSecond\n\n"
    contents, references = _parse_lines(raw)
    assert len(contents) == 2
    assert references is not None


def test_mixed_file_without_tab_in_first_lines():
    """If first lines lack refs, entire file treated as plain text."""
    raw = "Plain line\nAnother plain\n1.1\tThis has a tab"
    contents, references = _parse_lines(raw)
    assert references is None
    assert len(contents) == 3


def test_empty_string():
    contents, references = _parse_lines("")
    assert contents == []
    # Empty input: all() on empty iterable is vacuously True, so refs is [] not None
    assert references == [] or references is None
