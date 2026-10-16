"""Saving LSJ entries for lemmas (greek/db/repos/lexicon.py) and `greek study import-lsj`."""

import sqlite3
import urllib.error
from pathlib import Path

import pytest
from click.testing import CliRunner

from greek.cli.study_cmd import study_cmd
from greek.db.repos.lexicon import (
    get_lemma_entries,
    lemma_counts,
    list_unidentified_lemmas,
    save_lexicon,
)
from greek.db.repos.lines import insert_lines
from greek.db.repos.parses import save_treebank
from greek.db.repos.texts import insert_text
from greek.models import LexiconEntry, WordParse

FIXTURE = Path(__file__).parent.parent / "fixtures" / "lsj_excerpt.xml"


def _entry(key, headword, short_def="meaning"):
    return LexiconEntry(
        source="LSJ",
        key=key,
        headword=headword,
        short_def=short_def,
        entry_html=f"<p>{headword}</p>",
    )


@pytest.fixture
def parsed(db):
    """A text whose words have treebank lemmas: μῆνις twice, ἄειδε, Ἑλένη, ἐρωή."""
    text_id = insert_text(db, "Iliad", "Homer", "iliad.txt", "/data/iliad.txt", 2)
    insert_lines(db, text_id, ["μῆνιν μῆνιν ἄειδε", "Ἑλένη ἐρωή"], ["1.1", "1.2"])
    words = [
        (1, 0, "μῆνιν", "μῆνις"),
        (1, 1, "μῆνιν", "μῆνις"),
        (1, 2, "ἄειδε", "ἀείδω"),
        (2, 0, "Ἑλένη", "Ἑλένη"),
        (2, 1, "ἐρωή", "ἐρωή"),
    ]
    save_treebank(
        db,
        text_id,
        [],
        [
            WordParse(text_id, line, token, 0, form, lemma, "n-s---fa-", 1)
            for line, token, form, lemma in words
        ],
    )
    # a word without a parse has no lemma to look up
    db.execute(
        "UPDATE word_parses SET postag=NULL WHERE line_number=2 AND token_index=1"
    )
    db.commit()
    return db, text_id


def test_lemma_counts_counts_parsed_words(parsed):
    db, _ = parsed
    assert lemma_counts(db) == {"μῆνις": 2, "ἀείδω": 1, "Ἑλένη": 1}


def test_save_and_read_lemma_entries(parsed):
    db, _ = parsed
    save_lexicon(
        db,
        "LSJ",
        [_entry("mh=nis", "μῆνις", "wrath"), _entry("a)ei/dw", "ἀείδω")],
        {
            "μῆνις": ("mh=nis", "headword"),
            "ἀείδω": ("a)ei/dw", "headword"),
            "Ἑλένη": None,
        },
    )
    found = get_lemma_entries(db, ["μῆνις", "Ἑλένη", "unknown"])
    assert set(found) == {"μῆνις", "Ἑλένη"}
    assert found["μῆνις"].entry.short_def == "wrath"
    assert found["μῆνις"].entry.key == "mh=nis"
    assert found["μῆνις"].matched_by == "headword"
    assert found["Ἑλένη"].entry is None and found["Ἑλένη"].matched_by is None
    assert get_lemma_entries(db, []) == {}


def test_lemmas_can_share_an_entry(parsed):
    db, _ = parsed
    save_lexicon(
        db,
        "LSJ",
        [_entry("h(/lios", "ἥλιος")],
        {"ἥλιος": ("h(/lios", "headword"), "ἠέλιος": ("h(/lios", "variant")},
    )
    found = get_lemma_entries(db, ["ἥλιος", "ἠέλιος"])
    assert found["ἥλιος"].entry.id == found["ἠέλιος"].entry.id


def test_rerun_updates_entries_and_keeps_a_model_identification(parsed):
    db, _ = parsed
    matches = {"μῆνις": ("mh=nis", "headword"), "Ἑλένη": None}
    save_lexicon(db, "LSJ", [_entry("mh=nis", "μῆνις", "wrath")], matches)
    db.execute(
        """UPDATE lemma_entries SET model_def='Helen, wife of Menelaus',
           model_used='claude-opus-5-5' WHERE lemma='Ἑλένη'"""
    )
    db.commit()
    save_lexicon(db, "LSJ", [_entry("mh=nis", "μῆνις", "wrath; anger")], matches)
    found = get_lemma_entries(db, ["μῆνις", "Ἑλένη"])
    assert found["μῆνις"].entry.short_def == "wrath; anger"
    assert found["Ἑλένη"].model_def == "Helen, wife of Menelaus"
    assert found["Ἑλένη"].model_used == "claude-opus-5-5"
    assert db.execute("SELECT count(*) FROM lexicon_entries").fetchone()[0] == 1


def test_rerun_removes_entries_no_lemma_uses(parsed):
    db, _ = parsed
    save_lexicon(
        db,
        "LSJ",
        [_entry("mh=nis", "μῆνις"), _entry("a)ei/dw", "ἀείδω")],
        {"μῆνις": ("mh=nis", "headword"), "ἀείδω": ("a)ei/dw", "headword")},
    )
    save_lexicon(
        db,
        "LSJ",
        [_entry("mh=nis", "μῆνις")],
        {"μῆνις": ("mh=nis", "headword"), "ἀείδω": None},
    )
    keys = [r[0] for r in db.execute("SELECT key FROM lexicon_entries")]
    assert keys == ["mh=nis"]
    assert get_lemma_entries(db, ["ἀείδω"])["ἀείδω"].entry is None


def test_a_used_entry_cannot_be_deleted(parsed):
    db, _ = parsed
    save_lexicon(
        db, "LSJ", [_entry("mh=nis", "μῆνις")], {"μῆνις": ("mh=nis", "headword")}
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM lexicon_entries")


def test_model_def_needs_its_model(parsed):
    db, _ = parsed
    save_lexicon(db, "LSJ", [], {"Ἑλένη": None})
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE lemma_entries SET model_def='Helen'")


def test_unidentified_lemmas_most_frequent_first(parsed):
    db, _ = parsed
    save_lexicon(
        db,
        "LSJ",
        [_entry("a)ei/dw", "ἀείδω")],
        {"μῆνις": None, "ἀείδω": ("a)ei/dw", "headword"), "Ἑλένη": None},
    )
    assert list_unidentified_lemmas(db) == ["μῆνις", "Ἑλένη"]
    db.execute(
        "UPDATE lemma_entries SET model_def='wrath', model_used='m' WHERE lemma='μῆνις'"
    )
    assert list_unidentified_lemmas(db) == ["Ἑλένη"]


# --- CLI ---------------------------------------------------------------------


@pytest.fixture
def run_cli(db, monkeypatch, tmp_path):
    monkeypatch.setattr("greek.cli.study_cmd.get_connection", lambda: db)
    monkeypatch.setenv("GREEK_DATA_DIR", str(tmp_path))

    def run(*args):
        result = CliRunner().invoke(study_cmd, [str(a) for a in args])
        result.text = " ".join(result.output.split())  # Rich wraps long lines
        return result

    return run


@pytest.fixture
def lsj_fixture(monkeypatch, tmp_path):
    calls = []

    def fake_download(sources_dir, refresh=False):
        calls.append((sources_dir, refresh))
        return [FIXTURE]

    monkeypatch.setattr("greek.cli.study_cmd.download_lsj", fake_download)
    return calls


def test_cli_imports_lsj_and_reports(parsed, run_cli, lsj_fixture, tmp_path):
    db, _ = parsed
    result = run_cli("import-lsj")
    assert result.exit_code == 0, result.output
    assert lsj_fixture == [(tmp_path / "sources", False)]
    assert "LSJ entries for 3 treebank lemmas" in result.text
    assert "Headword (accents may differ) │ 1 │ 2" in result.text
    assert "Not in LSJ │ 2 │ 2" in result.text
    assert "Found in all │ 33.3% │ 50.00%" in result.text
    assert "Not in LSJ (1, most frequent first): ἀείδω (1)" in result.text
    assert "Names not in LSJ: 1" in result.text  # Ἑλένη is not ἑλένη, a torch
    assert "Saved 1 LSJ entries for 1 lemmas" in result.text
    found = get_lemma_entries(db, ["μῆνις"])["μῆνις"]
    assert found.entry.short_def == "wrath; revengeful temper"
    assert found.entry.entry_html.startswith('<p><b lang="grc">μῆνις</b>')


def test_cli_lists_names_on_request(parsed, run_cli, lsj_fixture):
    result = run_cli("import-lsj", "--list-unmatched", "--refresh")
    assert "Names not in LSJ (1): Ἑλένη (1)" in result.text
    assert lsj_fixture[0][1] is True


def test_cli_needs_treebank_lemmas(db, run_cli, lsj_fixture):
    result = run_cli("import-lsj")
    assert result.exit_code == 1
    assert "import-treebank" in result.text
    assert lsj_fixture == []


def test_cli_reports_network_errors(parsed, run_cli, monkeypatch):
    def fail(sources_dir, refresh=False):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr("greek.cli.study_cmd.download_lsj", fail)
    result = run_cli("import-lsj")
    assert result.exit_code == 1
    assert "Network error" in result.text
