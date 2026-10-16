from greek.db.repos.lines import resolve_line_range


def test_integer_range(db_with_text):
    db, text_id = db_with_text
    start, end, err = resolve_line_range(db, text_id, "1-10")
    assert err is None
    assert start == 1
    assert end == 10


def test_integer_range_partial(db_with_text):
    db, text_id = db_with_text
    start, end, err = resolve_line_range(db, text_id, "2-5")
    assert err is None
    assert start == 2
    assert end == 5


def test_reference_range(db_with_text):
    db, text_id = db_with_text
    start, end, err = resolve_line_range(db, text_id, "1.1-1.5")
    assert err is None
    assert start == 1
    assert end == 5


def test_reference_range_shorthand(db_with_text):
    """1.1-5 expands to 1.1-1.5 (same book shorthand)."""
    db, text_id = db_with_text
    start, end, err = resolve_line_range(db, text_id, "1.1-5")
    assert err is None
    assert start == 1
    assert end == 5


def test_reference_range_cross_book(db_with_text):
    db, text_id = db_with_text
    start, end, err = resolve_line_range(db, text_id, "1.5-2.1")
    assert err is None
    assert start == 5
    assert end == 6


def test_missing_start_ref(db_with_text):
    db, text_id = db_with_text
    start, end, err = resolve_line_range(db, text_id, "1.100-2.1")
    assert start is None
    assert end is None
    assert err is not None
    assert "1.100" in err


def test_missing_end_ref(db_with_text):
    db, text_id = db_with_text
    start, end, err = resolve_line_range(db, text_id, "1.1-1.100")
    assert start is None
    assert end is None
    assert err is not None
    assert "1.100" in err


def test_malformed_input(db_with_text):
    db, text_id = db_with_text
    start, end, err = resolve_line_range(db, text_id, "abc")
    assert start is None
    assert end is None
    assert err is not None


def test_malformed_no_hyphen(db_with_text):
    db, text_id = db_with_text
    start, end, err = resolve_line_range(db, text_id, "5")
    assert start is None
    assert end is None
    assert err is not None
