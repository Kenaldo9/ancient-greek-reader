"""Treebanks for every text: migration 018,
reading and aligning a GLAUx treebank, `greek study import-treebank --work
thucydides`, and the word strip's labels for a GLAUx text."""

import sqlite3
import unicodedata
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from greek.cli.study_cmd import study_cmd
from greek.db.connection import _MIGRATIONS_DIR
from greek.db.repos.lines import get_lines, insert_lines
from greek.db.repos.parses import (
    get_tb_source,
    list_tb_sentences,
    list_word_parses,
    save_tb_source,
    save_treebank,
)
from greek.db.repos.texts import get_text, insert_text
from greek.fetch.treebank import WORKS, align_text, read_treebank
from greek.models import TreebankSource
from greek.search import plain
from greek.web.reader_data import (
    PERSEUS_CREDIT,
    study_word,
    treebank_credit,
    work_name,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
GLAUX = FIXTURES / "thucydides_glaux_excerpt.xml"
PERSEUS = FIXTURES / "iliad_treebank_excerpt.tb.xml"

# Thucydides as the app prints it (Perseus's text): a prose row is a section
APP_LINES = [
    (
        "1.1.1",
        "Θουκυδίδης Ἀθηναῖος ξυνέγραψε τὸν πόλεμον, καὶ ἐλπίσας μέγαν τε ἔσεσθαι",
    ),
    ("1.1.2", "οὐδʼ ὀλίγον. κίνησις γὰρ αὕτη μεγίστη δὴ τοῖς Ἕλλησιν ἐγένετο."),
    ("2.1.1", "Ἄρχεται δὲ ὁ πόλεμος ἐνθένδε ἤδη."),
]
ILIAD_LINES = [
    ("1.1", "Μῆνιν ἄειδε θεὰ Πηληϊάδεω Ἀχιλῆος"),
    ("1.2", "οὐλομένην, ἣ μυρίʼ Ἀχαιοῖς ἄλγεʼ ἔθηκε,"),
    ("1.3", "πολλὰς δʼ ἰφθίμους ψυχὰς Ἄϊδι προΐαψεν."),
    ("1.4", "ἔρδον ἀθανάτοισι τεληέσσας ἑκατόμβας"),
    ("1.5", "οὐδʼ ἔθελον σὺ"),
    ("2.1", "Ἄλλοι μέν ῥα θεοί τε"),
    ("2.2", "εὗδον παννύχιοι."),
]


def _add_text(db, rows=APP_LINES, title="History of the Peloponnesian War"):
    text_id = insert_text(db, title, "Thucydides", "thuc.txt", "/d/thuc.txt", len(rows))
    insert_lines(db, text_id, [c for _, c in rows], [r for r, _ in rows])
    return text_id


def _import(db, text_id, path=GLAUX, work="thucydides"):
    treebank = read_treebank(path)
    result = align_text(text_id, get_lines(db, text_id), treebank.words, treebank.auto)
    nodes = [replace(n, text_id=text_id) for n in treebank.nodes]
    save_treebank(db, text_id, result.sentences, result.parses, nodes)
    w = WORKS[work]
    save_tb_source(db, TreebankSource(text_id, work, w.corpus, path.name, w.licence))
    return result


@pytest.fixture
def thucydides(db):
    text_id = _add_text(db)
    _import(db, text_id)
    return db, text_id


@pytest.fixture
def run_cli(db, monkeypatch, tmp_path):
    monkeypatch.setattr("greek.cli.study_cmd.get_connection", lambda: db)
    monkeypatch.setenv("GREEK_DATA_DIR", str(tmp_path))

    def run(*args):
        result = CliRunner().invoke(study_cmd, [str(a) for a in args])
        result.text = " ".join(result.output.split())  # Rich wraps at the width
        return result

    return run


# --- Migration 018 ---------------------------------------------------------------


def test_migration_018_marks_existing_sentences_manual():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("greek_plain", 1, plain, deterministic=True)
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if path.name >= "018":
            break
        conn.executescript(path.read_text())
    conn.executescript(
        """
        INSERT INTO texts(id, title, author, filename, filepath, line_count)
        VALUES (12, 'Iliad', 'Homer', 'iliad.txt', '/data/iliad.txt', 1);
        INSERT INTO tb_sentences(text_id, sentence_id, first_line, last_line)
        VALUES (12, 7, 1, 1);
        """
    )
    conn.executescript((_MIGRATIONS_DIR / "018_treebank_sources.sql").read_text())
    assert conn.execute("SELECT annotation FROM tb_sentences").fetchall() == [
        ("manual",)
    ]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE tb_sentences SET annotation='guessed'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO tb_sources(text_id, work, corpus, filename, licence)
               VALUES (12, 'iliad', 'proiel', 'x.xml', 'CC')"""
        )


def test_tb_source_is_saved_replaced_and_deleted_with_its_text(db):
    text_id = _add_text(db)
    assert get_tb_source(db, text_id) is None
    save_tb_source(
        db, TreebankSource(text_id, "iliad", "perseus", "a.xml", "CC BY-SA 3.0")
    )
    save_tb_source(db, TreebankSource(text_id, "thucydides", "glaux", "b.xml", "CC"))
    source = get_tb_source(db, text_id)
    assert (source.work, source.corpus, source.filename) == (
        "thucydides",
        "glaux",
        "b.xml",
    )
    db.execute("DELETE FROM texts WHERE id=?", (text_id,))
    assert get_tb_source(db, text_id) is None


# --- Reading and aligning GLAUx ----------------------------------------------------


def test_read_glaux_words_nodes_and_annotation():
    treebank = read_treebank(GLAUX)
    forms = [w.form for w in treebank.words]
    # punctuation and the elliptic node are not words; the split -δ’ is
    assert "," not in forms and "." not in forms and "E" not in forms
    assert "-δ’" in forms
    assert [w.book for w in treebank.words][:1] == ["1"]
    assert treebank.words[-1].book == "2"
    # Greek is read as NFC
    assert all(unicodedata.is_normalized("NFC", w.form) for w in treebank.words)
    assert treebank.words[0].lemma == "Θουκυδίδης"
    # every node is kept; GLAUx's elliptic node reads as Perseus's [0]
    elliptic = [n for n in treebank.nodes if n.artificial]
    assert [(n.form, n.lemma, n.postag) for n in elliptic] == [("[0]", None, None)]
    assert len(treebank.nodes) == 15 + 9 + 8
    assert treebank.auto == {2: "auto"}
    assert [treebank.annotation(s) for s in (1, 2, 3)] == ["manual", "auto", "manual"]


def test_align_glaux_with_prose_sections(db):
    text_id = _add_text(db)
    treebank = read_treebank(GLAUX)
    result = align_text(text_id, get_lines(db, text_id), treebank.words, treebank.auto)
    assert (result.identical, result.split, len(result.unmatched)) == (25, 1, 0)
    assert result.aligned_share == 1.0
    sentences = {
        (s.sentence_id, s.first_line, s.last_line, s.annotation)
        for s in result.sentences
    }
    assert sentences == {(1, 1, 2, "manual"), (2, 2, 2, "auto"), (3, 3, 3, "manual")}
    # οὐδʼ = οὐ + -δ’ (GLAUx's MWE)
    oud = [p for p in result.parses if p.line_number == 2 and p.token_index == 0]
    assert [(p.form, p.lemma) for p in oud] == [("οὐ", "οὐ"), ("-δ’", "δέ")]


def test_saved_sentences_keep_their_annotation(thucydides):
    db, text_id = thucydides
    assert [s.annotation for s in list_tb_sentences(db, text_id)] == [
        "manual",
        "auto",
        "manual",
    ]
    parses = list_word_parses(db, text_id, 1, 1)
    assert parses[5].postag == "b--------"  # καί, GLAUx's own code, kept as is


# --- import-treebank ---------------------------------------------------------------


def test_cli_imports_glaux_and_records_its_source(db, run_cli):
    text_id = _add_text(db)
    result = run_cli(
        "import-treebank", text_id, "--work", "thucydides", "--file", GLAUX
    )
    assert result.exit_code == 0, result.output
    assert "Identical form │ 25 │" in result.text
    assert "By book" in result.text
    assert (
        "Sentences annotated by GLAUx's parser: 1 of 3; the rest by hand."
        in result.text
    )
    source = get_tb_source(db, text_id)
    assert (source.work, source.corpus, source.filename) == (
        "thucydides",
        "glaux",
        "thucydides_glaux_excerpt.xml",
    )
    assert "Gorman" in source.licence
    assert [s.annotation for s in list_tb_sentences(db, text_id)] == [
        "manual",
        "auto",
        "manual",
    ]


def test_cli_downloads_the_glaux_file(db, run_cli, monkeypatch, tmp_path):
    text_id = _add_text(db)
    calls = []

    def fake_download(work, sources_dir, refresh=False):
        calls.append(work)
        return GLAUX

    monkeypatch.setattr("greek.cli.study_cmd.download_treebank", fake_download)
    result = run_cli("import-treebank", text_id, "--work", "thucydides")
    assert result.exit_code == 0, result.output
    assert calls == ["thucydides"]
    assert "alekkeersmaekers/glaux/main/xml/0003-001.xml" in result.text.replace(
        " ", ""
    )


def test_nodes_only_records_the_source_of_an_older_import(db, run_cli):
    text_id = _add_text(db, ILIAD_LINES, title="Iliad")
    treebank = read_treebank(PERSEUS)
    result = align_text(text_id, get_lines(db, text_id), treebank.words)
    save_treebank(db, text_id, result.sentences, result.parses)  # before 018
    assert get_tb_source(db, text_id) is None
    out = run_cli("import-treebank", text_id, "--file", PERSEUS, "--nodes-only")
    assert out.exit_code == 0, out.output
    source = get_tb_source(db, text_id)
    assert (source.work, source.corpus) == ("iliad", "perseus")
    assert "By book" not in out.text  # the report of a Perseus import is unchanged


# --- Labels in the reader ----------------------------------------------------------


def test_credits_and_names():
    glaux = TreebankSource(4, "thucydides", "glaux", "0003-001.xml", "CC")
    assert treebank_credit(None) == PERSEUS_CREDIT
    assert treebank_credit(TreebankSource(1, "odyssey", "perseus", "o", "CC")) == (
        PERSEUS_CREDIT
    )
    manual = treebank_credit(glaux, "manual")
    assert manual.label == "GLAUx treebank" and not manual.automatic
    assert "Gorman" in manual.title
    auto = treebank_credit(glaux, "auto")
    assert auto.label == "GLAUx, automatic" and auto.automatic
    assert "80%" in auto.title


def test_work_name_falls_back_to_the_title(db):
    text_id = _add_text(db)
    text = get_text(db, text_id)
    assert work_name(text, None) == "the History of the Peloponnesian War"
    glaux = TreebankSource(text_id, "thucydides", "glaux", "x", "CC")
    assert work_name(text, glaux) == "Thucydides"


def test_study_word_carries_the_sentence_credit(thucydides):
    db, text_id = thucydides
    text = get_text(db, text_id)
    by_hand = study_word(db, text, 1, 0, "Θουκυδίδης")
    assert by_hand.credit.label == "GLAUx treebank"
    assert by_hand.frequency.work == "Thucydides"
    assert by_hand.role.text == "Role: subject of ξυνέγραψε"
    parser = study_word(db, text, 2, 2, "κίνησις")
    assert parser.credit.automatic
    # GLAUx's bare CO has no role
    assert study_word(db, text, 3, 5, "ἤδη").role is None


def _strip(client, text_id, line, i):
    resp = client.get(f"/texts/{text_id}/word", params={"line": line, "i": i})
    assert resp.status_code == 200
    return " ".join(resp.text.split())


def test_word_strip_labels_a_glaux_word(client, web_db):
    text_id = _add_text(web_db)
    _import(web_db, text_id)
    html = _strip(client, text_id, 1, 0)  # Θουκυδίδης, by hand
    assert "Parsing · <span title=" in html and ">GLAUx treebank</span>" in html
    assert "Vanessa Gorman" in html
    assert "in Thucydides ·" in html or "only here in Thucydides" in html
    assert "may be wrong" not in html
    html = _strip(client, text_id, 2, 2)  # κίνησις, by GLAUx's parser
    assert ">GLAUx, automatic</span>" in html
    assert "may be wrong" in html
    assert "Perseus treebank" not in html


def test_all_words_names_a_partly_automatic_selection(client, web_db):
    text_id = _add_text(web_db)
    _import(web_db, text_id)

    def sources(lines):
        resp = client.get(f"/texts/{text_id}/passage", params={"lines": lines})
        assert resp.status_code == 200
        return " ".join(resp.text.split())

    assert "Parsing: GLAUx treebank, partly automatic." in sources("1-2")
    assert "Parsing: GLAUx treebank." in sources("3-3")


def test_an_iliad_imported_before_018_reads_as_before(client, web_db):
    text_id = _add_text(web_db, ILIAD_LINES, title="Iliad")
    treebank = read_treebank(PERSEUS)
    result = align_text(text_id, get_lines(web_db, text_id), treebank.words)
    nodes = [replace(n, text_id=text_id) for n in treebank.nodes]
    save_treebank(web_db, text_id, result.sentences, result.parses, nodes)
    html = _strip(client, text_id, 1, 0)  # Μῆνιν
    assert ">Perseus treebank</span>" in html
    assert "in the Iliad" in html
    assert "GLAUx" not in html
