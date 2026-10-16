"""Saving treebank alignments (greek/db/repos/parses.py) and `greek study import-treebank`."""

import sqlite3
import urllib.error
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from greek.cli.study_cmd import study_cmd
from greek.db.repos.lines import get_lines, insert_lines
from greek.db.repos.parses import (
    count_tb_nodes,
    list_missing_parses,
    list_sentence_nodes,
    list_tb_sentences,
    list_word_parses,
    node_positions,
    save_treebank,
)
from greek.db.repos.texts import insert_text
from greek.fetch.treebank import align_text, parse_treebank, read_treebank

FIXTURE = Path(__file__).parent.parent / "fixtures" / "iliad_treebank_excerpt.tb.xml"

APP_LINES = [
    ("1.1", "Μῆνιν ἄειδε θεὰ Πηληϊάδεω Ἀχιλῆος"),
    ("1.2", "οὐλομένην, ἣ μυρίʼ Ἀχαιοῖς ἄλγεʼ ἔθηκε,"),
    ("1.3", "πολλὰς δʼ ἰφθίμους ψυχὰς Ἄϊδι προΐαψεν."),
    ("1.4", "ἔρδον ἀθανάτοισι τεληέσσας ἑκατόμβας"),
    ("1.5", "οὐδʼ ἔθελον σὺ"),
    ("2.1", "Ἄλλοι μέν ῥα θεοί τε"),
    ("2.2", "εὗδον παννύχιοι."),
]


def _add_text(db, rows=APP_LINES, filename="iliad.txt", references=True):
    text_id = insert_text(
        db, "Iliad", "Homer", filename, f"/data/{filename}", len(rows)
    )
    insert_lines(
        db,
        text_id,
        [content for _, content in rows],
        [ref for ref, _ in rows] if references else None,
    )
    return text_id


def _import(db, text_id):
    result = align_text(text_id, get_lines(db, text_id), parse_treebank(FIXTURE))
    save_treebank(db, text_id, result.sentences, result.parses)
    return result


@pytest.fixture
def iliad(db):
    return db, _add_text(db)


def _count(db, table, text_id):
    return db.execute(
        f"SELECT count(*) FROM {table} WHERE text_id=?", (text_id,)
    ).fetchone()[0]


def test_save_stores_parses_and_sentences(iliad):
    db, text_id = iliad
    _import(db, text_id)
    parses = list_word_parses(db, text_id)
    assert len(parses) == 32  # 31 words, one of them in two parts
    assert (parses[0].form, parses[0].lemma) == ("μῆνιν", "μῆνις")
    assert [
        (s.sentence_id, s.first_line, s.last_line)
        for s in list_tb_sentences(db, text_id)
    ] == [
        (100, 1, 3),
        (200, 5, 5),
        (300, 6, 7),
    ]


def test_list_word_parses_for_a_line_range(iliad):
    db, text_id = iliad
    _import(db, text_id)
    parses = list_word_parses(db, text_id, 5, 5)
    assert [(p.token_index, p.part, p.lemma) for p in parses] == [
        (0, 0, "οὐ"),
        (0, 1, "δέ"),
        (1, 0, "ἐθέλω"),
        (2, 0, None),
    ]


def test_missing_parses_are_recorded(iliad):
    db, text_id = iliad
    _import(db, text_id)
    missing = list_missing_parses(db, text_id)
    assert [p.form for p in missing] == [
        "Ἄϊδι",
        "ἔρδον",
        "ἀθανάτοισι",
        "τεληέσσας",
        "ἑκατόμβας",
        "σὺ",
    ]
    # the untagged treebank word keeps its sentence; the others have none
    assert [p.sentence_id for p in missing] == [100, None, None, None, None, None]


def test_rerunning_the_import_replaces_treebank_rows(iliad):
    db, text_id = iliad
    _import(db, text_id)
    _import(db, text_id)
    assert _count(db, "word_parses", text_id) == 32
    assert _count(db, "tb_sentences", text_id) == 3


def test_rerun_keeps_a_model_parse_for_a_word_the_treebank_lacks(iliad):
    db, text_id = iliad
    _import(db, text_id)
    db.execute(
        """UPDATE word_parses SET lemma='σύ', postag='p-s---mn-', source='model',
           model_used='claude-opus-5-5'
           WHERE text_id=? AND line_number=5 AND token_index=2""",
        (text_id,),
    )
    db.commit()
    _import(db, text_id)
    [p] = [p for p in list_word_parses(db, text_id, 5, 5) if p.token_index == 2]
    assert (p.lemma, p.source, p.model_used) == ("σύ", "model", "claude-opus-5-5")
    assert len(list_missing_parses(db, text_id)) == 5


def test_rerun_prefers_a_treebank_parse_over_a_model_parse(iliad):
    db, text_id = iliad
    _import(db, text_id)
    db.execute(
        """UPDATE word_parses SET lemma='x', source='model', model_used='m'
           WHERE text_id=? AND line_number=1 AND token_index=0""",
        (text_id,),
    )
    db.commit()
    _import(db, text_id)
    p = list_word_parses(db, text_id, 1, 1)[0]
    assert (p.lemma, p.source, p.model_used) == ("μῆνις", "treebank", None)


def test_model_source_requires_model_used(iliad):
    db, text_id = iliad
    _import(db, text_id)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE word_parses SET source='model' WHERE text_id=? AND line_number=1",
            (text_id,),
        )


def test_other_texts_are_untouched(iliad):
    db, text_id = iliad
    other = _add_text(db, filename="iliad-copy.txt")
    _import(db, other)
    _import(db, text_id)
    _import(db, text_id)
    assert _count(db, "word_parses", other) == 32


def test_deleting_a_text_deletes_its_treebank_data(iliad):
    db, text_id = iliad
    _import(db, text_id)
    db.execute("DELETE FROM texts WHERE id=?", (text_id,))
    db.commit()
    assert _count(db, "word_parses", text_id) == 0
    assert _count(db, "tb_sentences", text_id) == 0


# --- CLI ---------------------------------------------------------------------


@pytest.fixture
def run_cli(db, monkeypatch, tmp_path):
    monkeypatch.setattr("greek.cli.study_cmd.get_connection", lambda: db)
    monkeypatch.setenv("GREEK_DATA_DIR", str(tmp_path))

    def run(*args):
        result = CliRunner().invoke(study_cmd, [str(a) for a in args])
        # Rich wraps long lines at the terminal width
        result.text = " ".join(result.output.split())
        return result

    return run


def test_cli_imports_and_reports_counts(iliad, run_cli):
    db, text_id = iliad
    result = run_cli("import-treebank", text_id, "--file", FIXTURE)
    assert result.exit_code == 0, result.output
    assert "Identical form │ 25 │ 80.65%" in result.text
    assert "No treebank word │ 5 │ 16.13%" in result.text
    assert "All │ 31 │ 100.00%" in result.text
    assert "1.4: ἔρδον ἀθανάτοισι τεληέσσας ἑκατόμβας" in result.text
    assert "1.5: σὺ" in result.text
    assert "without lemma or parse: 1" in result.text
    assert "Saved 32 word parses, 3 sentences and 33 nodes" in result.text
    assert _count(db, "word_parses", text_id) == 32


def test_cli_lists_untagged_words(iliad, run_cli):
    _, text_id = iliad
    result = run_cli("import-treebank", text_id, "--file", FIXTURE, "--list-missing")
    assert "1.3: Ἄϊδι" in result.text


def test_cli_downloads_to_data_sources(iliad, run_cli, monkeypatch, tmp_path):
    _, text_id = iliad
    calls = []

    def fake_download(work, sources_dir, refresh=False):
        calls.append((work, sources_dir, refresh))
        return FIXTURE

    monkeypatch.setattr("greek.cli.study_cmd.download_treebank", fake_download)
    result = run_cli("import-treebank", text_id, "--refresh")
    assert result.exit_code == 0, result.output
    assert calls == [("iliad", tmp_path / "sources", True)]


def test_cli_reports_network_errors(iliad, run_cli, monkeypatch):
    _, text_id = iliad

    def fail(work, sources_dir, refresh=False):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr("greek.cli.study_cmd.download_treebank", fail)
    result = run_cli("import-treebank", text_id)
    assert result.exit_code == 1
    assert "Network error" in result.text


def test_cli_refuses_a_treebank_for_another_text(db, run_cli):
    text_id = _add_text(
        db, [("1.1", "ἄνδρα μοι ἔννεπε μοῦσα πολύτροπον")], filename="odyssey.txt"
    )
    result = run_cli("import-treebank", text_id, "--file", FIXTURE)
    assert result.exit_code == 1
    assert "Nothing was saved" in result.text
    assert _count(db, "word_parses", text_id) == 0


def test_cli_rejects_unknown_text_and_lines_without_references(db, run_cli):
    assert run_cli("import-treebank", 999, "--file", FIXTURE).exit_code == 1
    text_id = _add_text(db, filename="plain.txt", references=False)
    result = run_cli("import-treebank", text_id, "--file", FIXTURE)
    assert result.exit_code == 1
    assert "without references" in result.text


# --- Treebank nodes (word details, Pilot 1) ----------------------------------


def _import_with_nodes(db, text_id):
    treebank = read_treebank(FIXTURE)
    result = align_text(text_id, get_lines(db, text_id), treebank.words)
    nodes = [replace(n, text_id=text_id) for n in treebank.nodes]
    save_treebank(db, text_id, result.sentences, result.parses, nodes)
    return result


def _node_ids(db, text_id):
    return {
        (p.line_number, p.token_index, p.part): p.node_id
        for p in list_word_parses(db, text_id)
    }


def test_every_node_is_saved_with_punctuation_and_elliptic_nodes(iliad):
    db, text_id = iliad
    _import_with_nodes(db, text_id)
    assert count_tb_nodes(db, text_id) == 33
    nodes = {n.node_id: n for n in list_sentence_nodes(db, text_id, 100)}
    assert len(nodes) == 20
    assert (nodes[1].form, nodes[1].head, nodes[1].relation) == ("μῆνιν", 2, "OBJ")
    assert (nodes[7].form, nodes[7].relation) == (",", "AuxX")
    assert nodes[18].artificial and nodes[18].form == "[0]"
    assert nodes[18].lemma is None and nodes[18].postag is None
    assert nodes[17].lemma is None  # Ἄϊδι, untagged in the treebank


def test_words_are_linked_to_their_nodes(iliad):
    db, text_id = iliad
    _import_with_nodes(db, text_id)
    ids = _node_ids(db, text_id)
    assert ids[(1, 0, 0)] == 1  # μῆνιν
    assert ids[(2, 5, 0)] == 12  # ἔθηκε
    assert ids[(5, 0, 0)] == 1 and ids[(5, 0, 1)] == 2  # οὐ + δ̓ of οὐδʼ
    assert ids[(4, 0, 0)] is None  # 1.4: no treebank word
    assert node_positions(db, text_id, 100)[12] == (2, 5)


def _model_parse(db, text_id):
    """A model's parse of Ἄϊδι (1.3), which the treebank leaves untagged."""
    db.execute(
        """UPDATE word_parses SET lemma='Ἀΐδης', postag='n-s---md-', source='model',
           model_used='claude-opus-5-5' WHERE text_id=? AND line_number=3
           AND token_index=4""",
        (text_id,),
    )
    db.commit()


def test_a_reimport_keeps_a_model_parse_and_links_it(iliad):
    db, text_id = iliad
    _import_with_nodes(db, text_id)
    _model_parse(db, text_id)
    _import_with_nodes(db, text_id)
    [p] = [p for p in list_word_parses(db, text_id, 3, 3) if p.token_index == 4]
    assert (p.lemma, p.source, p.node_id) == ("Ἀΐδης", "model", 17)


@pytest.fixture
def imported_without_nodes(iliad):
    """A text imported before nodes were kept, with a model's parse."""
    db, text_id = iliad
    _import(db, text_id)
    db.execute("UPDATE word_parses SET node_id=NULL")
    db.execute("DELETE FROM tb_nodes")
    db.commit()
    _model_parse(db, text_id)
    return db, text_id


def test_cli_nodes_only_links_saved_words(imported_without_nodes, run_cli):
    db, text_id = imported_without_nodes
    before = [
        (p.form, p.lemma, p.postag, p.source, p.model_used)
        for p in list_word_parses(db, text_id)
    ]
    result = run_cli("import-treebank", text_id, "--file", FIXTURE, "--nodes-only")
    assert result.exit_code == 0, result.output
    assert "Nodes stored │ 33" in result.text
    assert "of them elliptic │ 2" in result.text
    assert "Word rows linked (parts of split words separately) │ 27" in result.text
    assert "Words linked │ 26" in result.text
    assert "Words without a node │ 5" in result.text  # 1.4, and σὺ for σὰ
    assert count_tb_nodes(db, text_id) == 33
    ids = _node_ids(db, text_id)
    assert ids[(1, 0, 0)] == 1 and ids[(3, 4, 0)] == 17
    # lemmas, parses and the model's parse are unchanged
    after = [
        (p.form, p.lemma, p.postag, p.source, p.model_used)
        for p in list_word_parses(db, text_id)
    ]
    assert after == before


def test_cli_nodes_only_refuses_when_saved_words_differ(
    imported_without_nodes, run_cli
):
    db, text_id = imported_without_nodes
    db.execute(
        """UPDATE word_parses SET lemma='ἄλλο' WHERE text_id=? AND line_number=1
           AND token_index=0""",
        (text_id,),
    )
    db.commit()
    result = run_cli("import-treebank", text_id, "--file", FIXTURE, "--nodes-only")
    assert result.exit_code == 1
    assert "1.1 word 1: the treebank has μῆνιν (μῆνις), the database μῆνιν (ἄλλο)" in (
        result.text
    )
    assert "nothing was saved" in result.text
    assert count_tb_nodes(db, text_id) == 0
    assert all(node_id is None for node_id in _node_ids(db, text_id).values())


def test_cli_nodes_only_needs_an_import_first(iliad, run_cli):
    _, text_id = iliad
    result = run_cli("import-treebank", text_id, "--file", FIXTURE, "--nodes-only")
    assert result.exit_code == 1
    assert "has no treebank data" in result.text
