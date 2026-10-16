"""`greek update-text`: bringing an imported text up to date with its TEI file
in place, and the repository functions it uses."""

import pytest
from click.testing import CliRunner
from rich.console import Console

from greek.cli.update_text_cmd import update_text_cmd
from greek.db.repos.lines import (
    get_lines,
    insert_lines,
    lines_with_saved_data,
    search_lines,
    update_line_contents,
)
from greek.db.repos.parses import save_tb_source
from greek.db.repos.texts import insert_text
from greek.fetch.catalog import CatalogEntry
from greek.models import TreebankSource

# Thucydides as an older Perseus file printed it: 1.11.2 ends with a copy of
# 1.11.3, and 1.9.4 starts with a citation glued to its first word
SAVED = [
    ("1.9.4", "Hom. Il. 2.108οὐκ ἂν οὖν νήσων ἔξω"),
    ("1.11.1", "αἴτιον δʼ ἦν οὐχ ἡ ὀλιγανθρωπία"),
    ("1.11.2", "περιουσίαν δὲ εἰ ἦλθον. ἀλλὰ δι’ ἀχρηματίαν"),
    ("1.11.3", "ἀλλὰ δι’ ἀχρηματίαν"),
]
SECTIONS = [
    ("9", "4", '<bibl n="Hom. Il. 2.108">Hom. Il. 2.108</bibl>οὐκ ἂν οὖν νήσων ἔξω'),
    ("11", "1", "αἴτιον δʼ ἦν οὐχ ἡ ὀλιγανθρωπία"),
    ("11", "2", "περιουσίαν δὲ εἰ ἦλθον."),
    ("11", "3", "ἀλλὰ δι’ ἀχρηματίαν"),
]
FILENAME = "thucydides_history_of_the_peloponnesian_war.txt"


def _tei(sections=SECTIONS) -> str:
    chapters = {}
    for chapter, section, text in sections:
        chapters.setdefault(chapter, []).append(
            f'<div type="textpart" subtype="section" n="{section}"><p>{text}</p></div>'
        )
    body = "".join(
        f'<div type="textpart" subtype="chapter" n="{c}">{"".join(s)}</div>'
        for c, s in chapters.items()
    )
    return (
        '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
        '<div type="edition" n="urn:cts:greekLit:tlg0003.tlg001.perseus-grc2">'
        f'<div type="textpart" subtype="book" n="1">{body}</div></div>'
        "</body></text></TEI>"
    )


@pytest.fixture
def thucydides(db, tmp_path):
    text_id = insert_text(
        db,
        "History of the Peloponnesian War",
        "Thucydides",
        FILENAME,
        f"/data/texts/{FILENAME}",
        len(SAVED),
    )
    insert_lines(db, text_id, [c for _, c in SAVED], [r for r, _ in SAVED])
    (tmp_path / "texts").mkdir()
    (tmp_path / "texts" / FILENAME).write_text(
        "\n".join(f"{r}\t{c}" for r, c in SAVED), encoding="utf-8"
    )
    tei = tmp_path / "tlg0003.tlg001.perseus-grc2.xml"
    tei.write_text(_tei(), encoding="utf-8")
    return text_id, tei


@pytest.fixture
def run(db, monkeypatch, tmp_path):
    monkeypatch.setattr("greek.cli.update_text_cmd.get_connection", lambda: db)
    monkeypatch.setenv("GREEK_DATA_DIR", str(tmp_path))

    # Wide enough that the table's cells don't wrap
    monkeypatch.setattr("greek.cli.update_text_cmd.console", Console(width=200))

    def invoke(*args, input=None):
        result = CliRunner().invoke(
            update_text_cmd, [str(a) for a in args], input=input
        )
        result.text = " ".join(result.output.split())
        return result

    return invoke


def _contents(db, text_id):
    return [(line.reference, line.content) for line in get_lines(db, text_id)]


def test_it_updates_the_changed_lines_in_place(db, thucydides, run, tmp_path):
    text_id, tei = thucydides
    before = {line.reference: line.line_number for line in get_lines(db, text_id)}

    result = run(text_id, "--file", tei, "--yes")

    assert result.exit_code == 0, result.output
    assert "Updated 2 lines" in result.text
    assert _contents(db, text_id) == [
        ("1.9.4", "οὐκ ἂν οὖν νήσων ἔξω"),
        ("1.11.1", "αἴτιον δʼ ἦν οὐχ ἡ ὀλιγανθρωπία"),
        ("1.11.2", "περιουσίαν δὲ εἰ ἦλθον."),
        ("1.11.3", "ἀλλὰ δι’ ἀχρηματίαν"),
    ]
    # Line numbers stay, so a reader's saved place stays right
    assert {
        line.reference: line.line_number for line in get_lines(db, text_id)
    } == before
    # The report shows the words removed from each changed line
    assert "Hom. Il. 2.108οὐκ" in result.text
    assert "ἀλλὰ δι’ ἀχρηματίαν" in result.text
    # The file copy follows
    copy = (tmp_path / "texts" / FILENAME).read_text(encoding="utf-8")
    assert copy.splitlines()[0] == "1.9.4\tοὐκ ἂν οὖν νήσων ἔξω"
    assert copy.splitlines()[2] == "1.11.2\tπεριουσίαν δὲ εἰ ἦλθον."


def test_search_follows_the_update(db, thucydides, run):
    text_id, tei = thucydides
    lines, total = search_lines(db, text_id, "αχρηματιαν")
    assert [line.reference for line in lines] == ["1.11.2", "1.11.3"]

    run(text_id, "--file", tei, "--yes")

    lines, total = search_lines(db, text_id, "αχρηματιαν")
    assert [line.reference for line in lines] == ["1.11.3"]
    assert search_lines(db, text_id, "hom")[1] == 0


def test_a_text_that_matches_the_file_is_left_alone(db, thucydides, run):
    text_id, tei = thucydides
    run(text_id, "--file", tei, "--yes")

    result = run(text_id, "--file", tei, "--yes")

    assert result.exit_code == 0
    assert "is up to date" in result.text


def test_it_refuses_when_references_differ(db, thucydides, run, tmp_path):
    text_id, _ = thucydides
    tei = tmp_path / "longer.xml"
    tei.write_text(_tei(SECTIONS + [("11", "4", "καὶ ἔτι")]), encoding="utf-8")

    result = run(text_id, "--file", tei, "--yes")

    assert result.exit_code == 1
    assert "would move line numbers" in result.text
    assert _contents(db, text_id) == SAVED


@pytest.mark.parametrize(
    "sql, params",
    [
        (
            """INSERT INTO translation_units(text_id, first_line, last_line,
                   fluent, model_used) VALUES (?, 2, 3, 'f', 'claude-opus-5-5')""",
            (),
        ),
        (
            """INSERT INTO word_study(text_id, line_number, token_index, gloss,
                   note, model_used) VALUES (?, 3, 0, 'surplus', '', 'claude-opus-5-5')""",
            (),
        ),
        (
            """INSERT INTO word_parses(text_id, line_number, token_index, form,
                   lemma, postag, source, model_used)
               VALUES (?, 1, 0, 'οὐκ', 'οὐ', 'd--------', 'model', 'claude-opus-5-5')""",
            (),
        ),
    ],
    ids=["fluent translation", "study data", "a model's parse"],
)
def test_it_refuses_when_a_changed_line_has_saved_data(
    db, thucydides, run, sql, params
):
    text_id, tei = thucydides
    db.execute(sql, (text_id, *params))
    db.commit()

    result = run(text_id, "--file", tei, "--yes")

    assert result.exit_code == 1
    assert "nothing was changed" in result.text
    assert _contents(db, text_id) == SAVED


def test_saved_data_on_unchanged_lines_does_not_stop_it(db, thucydides, run):
    text_id, tei = thucydides
    db.execute(
        """INSERT INTO line_translations(text_id, line_number, literal, model_used)
               VALUES (?, 4, 'but through lack of money', 'claude-opus-5-5')""",
        (text_id,),
    )
    db.commit()

    result = run(text_id, "--file", tei, "--yes")

    assert result.exit_code == 0, result.output
    assert "Updated 2 lines" in result.text


def test_it_asks_first(db, thucydides, run):
    text_id, tei = thucydides

    result = run(text_id, "--file", tei, input="n\n")

    assert "Update 2 lines?" in result.text
    assert "Nothing was changed." in result.text
    assert _contents(db, text_id) == SAVED


def test_it_says_to_import_the_treebank_again(db, thucydides, run):
    text_id, tei = thucydides
    db.execute(
        """INSERT INTO word_parses(text_id, line_number, token_index, form, lemma,
               postag, source) VALUES (?, 3, 0, 'περιουσίαν', 'περιουσία',
               'n-s---fa-', 'treebank')""",
        (text_id,),
    )
    save_tb_source(
        db,
        TreebankSource(text_id, "thucydides", "glaux", "0003-001.xml", "CC BY-SA 4.0"),
    )

    result = run(text_id, "--file", tei, "--yes")

    assert result.exit_code == 0, result.output
    assert f"greek study import-treebank {text_id} --work thucydides" in result.text


def test_it_downloads_the_texts_perseus_file(db, thucydides, run, monkeypatch):
    text_id, tei = thucydides
    entry = CatalogEntry(
        "Thucydides",
        "History of the Peloponnesian War",
        "tlg0003",
        "tlg001",
        "tlg0003.tlg001.perseus-grc2.xml",
    )
    other = CatalogEntry("Homer", "Iliad", "tlg0012", "tlg001", "x.xml")
    monkeypatch.setattr(
        "greek.cli.update_text_cmd.load_catalog", lambda data_dir: [other, entry]
    )
    fetched = []

    def fake_fetch(url, timeout=30):
        fetched.append(url)
        return tei.read_bytes()

    monkeypatch.setattr("greek.cli.update_text_cmd.fetch_url", fake_fetch)

    result = run(text_id, "--yes")

    assert result.exit_code == 0, result.output
    assert fetched == [entry.raw_url]
    assert "Updated 2 lines" in result.text


def test_a_text_not_from_perseus_needs_a_file(db, thucydides, run, monkeypatch):
    text_id, _ = thucydides
    monkeypatch.setattr("greek.cli.update_text_cmd.load_catalog", lambda data_dir: [])

    result = run(text_id, "--yes")

    assert result.exit_code == 1
    assert "pass its TEI file with --file" in result.text


def test_lines_with_saved_data_names_each_kind(db):
    text_id = insert_text(db, "Iliad", "Homer", "iliad.txt", "/d/iliad.txt", 6)
    insert_lines(db, text_id, ["α", "β", "γ", "δ", "ε", "ζ"], None)
    db.executescript(
        f"""
        INSERT INTO line_translations(text_id, line_number, literal, model_used)
            VALUES ({text_id}, 1, 'a', 'm');
        INSERT INTO word_uses(text_id, line_number, token_index, model_used,
            use_label, effort) VALUES ({text_id}, 2, 0, 'm', 'dative of disadvantage',
            'high');
        INSERT INTO word_parses(text_id, line_number, token_index, form, source)
            VALUES ({text_id}, 3, 0, 'γ', 'treebank');
        INSERT INTO translation_units(text_id, first_line, last_line, fluent,
            model_used) VALUES ({text_id}, 4, 5, 'f', 'm');
        INSERT INTO word_study(text_id, line_number, token_index, gloss, note,
            model_used) VALUES ({text_id}, 6, 0, 'g', '', 'm');
        """
    )

    assert lines_with_saved_data(db, text_id, [1, 2, 3, 5, 6]) == {
        "study glosses and notes": [6],
        "literal translations": [1],
        "use labels": [2],
        "fluent translations": [5],
    }
    # A treebank's parse alone is no reason to refuse (an import replaces it)
    assert lines_with_saved_data(db, text_id, [3]) == {}


def test_update_line_contents_keeps_the_search_index_in_step(db):
    text_id = insert_text(db, "Iliad", "Homer", "iliad.txt", "/d/iliad.txt", 2)
    insert_lines(db, text_id, ["μῆνιν ἄειδε θεά", "οὐλομένην"], ["1.1", "1.2"])

    update_line_contents(db, text_id, {2: "οὐλομένην, ἣ μυρίʼ"})

    assert _contents(db, text_id) == [
        ("1.1", "μῆνιν ἄειδε θεά"),
        ("1.2", "οὐλομένην, ἣ μυρίʼ"),
    ]
    assert [line.reference for line in search_lines(db, text_id, "μυρι")[0]] == ["1.2"]
