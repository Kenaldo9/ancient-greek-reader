from greek.fetch.catalog import CatalogEntry, search_catalog
from greek.cli.fetch_cmd import _txt_filename


def _make_entry(author, title):
    return CatalogEntry(
        author=author,
        title=title,
        tlg_author="tlg0012",
        tlg_work="tlg001",
        filename="tlg0012.tlg001.perseus-grc2.xml",
    )


CATALOG = [
    _make_entry("Homer", "Iliad"),
    _make_entry("Homer", "Odyssey"),
    _make_entry("Herodotus", "Histories"),
    _make_entry("Thucydides", "History of the Peloponnesian War"),
    _make_entry("Plato", "Republic"),
]


def test_search_single_token():
    results = search_catalog(CATALOG, "homer")
    assert len(results) == 2
    assert all(e.author == "Homer" for e in results)


def test_search_multi_token_and_logic():
    results = search_catalog(CATALOG, "homer iliad")
    assert len(results) == 1
    assert results[0].title == "Iliad"


def test_search_no_match():
    results = search_catalog(CATALOG, "zzz")
    assert results == []


def test_search_case_insensitive():
    results = search_catalog(CATALOG, "HOMER")
    assert len(results) == 2


def test_search_title_match():
    results = search_catalog(CATALOG, "peloponnesian")
    assert len(results) == 1
    assert results[0].author == "Thucydides"


def test_txt_filename_spaces_become_underscores():
    entry = _make_entry("John Smith", "Some Work")
    name = _txt_filename(entry)
    assert " " not in name


def test_txt_filename_ends_with_txt():
    entry = _make_entry("Homer", "Iliad")
    assert _txt_filename(entry).endswith(".txt")


def test_txt_filename_apostrophes_stripped():
    entry = _make_entry("O'Brien", "Work")
    name = _txt_filename(entry)
    assert "'" not in name


def test_txt_filename_commas_stripped():
    entry = _make_entry("Author, Jr.", "Work")
    name = _txt_filename(entry)
    assert "," not in name


def test_txt_filename_only_safe_chars():
    entry = _make_entry("Homer", "Iliad")
    name = _txt_filename(entry)
    stem = name[:-4]  # strip .txt
    assert all(c.isalnum() or c in "_-" for c in stem)
