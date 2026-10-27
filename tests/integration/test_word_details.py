"""Word details: migration 016, lemma
frequency, a word's role read from the saved treebank nodes, and the word
strip that shows them with the dictionary form and principal-part slot, a
verb's construction and a word's use label."""

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from greek.db.connection import _MIGRATIONS_DIR, run_migrations
from greek.db.repos.lexicon import save_lexicon
from greek.db.repos.lines import book_span, get_lines, insert_lines
from greek.db.repos.parses import lemma_frequency, list_word_parses, save_treebank
from greek.db.repos.study import save_forms_result, save_uses_result
from greek.db.repos.texts import get_text, insert_text
from greek.db.repos.verbs import save_verb_parts
from greek.fetch.treebank import align_text, read_treebank
from greek.models import LemmaForms, LexiconEntry, WordUse
from greek.search import plain
from greek.web.reader_data import (
    PERSEUS_CREDIT,
    saved_principal_parts,
    word_frequency,
    word_role,
)
from tests.integration.test_web_study import tag

PERSEUS = tag("Perseus", PERSEUS_CREDIT.title)

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


def _import_excerpt(db):
    text_id = insert_text(db, "Iliad", "Homer", "iliad.txt", "/data/iliad.txt", 7)
    insert_lines(db, text_id, [c for _, c in APP_LINES], [r for r, _ in APP_LINES])
    treebank = read_treebank(FIXTURE)
    result = align_text(text_id, get_lines(db, text_id), treebank.words)
    nodes = [replace(n, text_id=text_id) for n in treebank.nodes]
    save_treebank(db, text_id, result.sentences, result.parses, nodes)
    return db, text_id


@pytest.fixture
def iliad(db):
    return _import_excerpt(db)


def _parses(db, text_id, line, token):
    return [
        p for p in list_word_parses(db, text_id, line, line) if p.token_index == token
    ]


# --- Migration 016 --------------------------------------------------------------


def test_migration_016_keeps_batches_and_allows_forms_and_uses():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("greek_plain", 1, plain, deterministic=True)
    conn.execute("CREATE TABLE _migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if path.name >= "016":
            break
        conn.executescript(path.read_text())
        conn.execute("INSERT INTO _migrations(name) VALUES (?)", (path.name,))
    conn.executescript(
        """
        INSERT INTO texts(id, title, author, filename, filepath, line_count)
        VALUES (12, 'Iliad', 'Homer', 'iliad.txt', '/data/iliad.txt', 1);
        INSERT INTO lines(text_id, line_number, content, reference)
        VALUES (12, 1, 'μῆνιν ἄειδε', '1.1');
        INSERT INTO word_parses(text_id, line_number, token_index, form, lemma,
                                postag, sentence_id, source)
        VALUES (12, 1, 0, 'μῆνιν', 'μῆνις', 'n-s---fa-', 7, 'treebank');
        INSERT INTO study_batches(id, text_id, scope, model, status, request_count,
                                  cost_usd, collected_at, kind, effort, pilot)
        VALUES ('msgbatch_notes', 12, 'Iliad 1.1–1.8', 'claude-opus-5-5', 'ended',
                1, 0.12, '2026-10-08 20:00:00', 'notes', 'high', 1);
        INSERT INTO study_requests(batch_id, custom_id, first_line, last_line,
                                   word_count)
        VALUES ('msgbatch_notes', '12_1-8', 1, 8, 52);
        INSERT INTO study_batches(id, text_id, scope, model, status, request_count,
                                  kind)
        VALUES ('msgbatch_parts', 12, 'parts', 'claude-opus-5-5', 'ended', 1,
                'parts');
        INSERT INTO parts_requests(batch_id, custom_id, verbs, verb_count)
        VALUES ('msgbatch_parts', '12_parts_1', '["ἀείδω"]', 1);
        """
    )
    conn.commit()
    run_migrations(conn)

    rows = conn.execute(
        """SELECT id, kind, effort, pilot, cost_usd, collected_at FROM study_batches
           ORDER BY id"""
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("msgbatch_notes", "notes", "high", 1, 0.12, "2026-10-08 20:00:00"),
        ("msgbatch_parts", "parts", "medium", 0, 0.0, None),
    ]
    assert conn.execute("SELECT count(*) FROM study_requests").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM parts_requests").fetchone()[0] == 1
    # existing words have no node yet
    assert conn.execute("SELECT node_id FROM word_parses").fetchone()[0] is None

    # forms and uses are kinds now; others still aren't
    conn.execute("UPDATE study_batches SET kind='forms' WHERE id='msgbatch_parts'")
    conn.execute("UPDATE study_batches SET kind='uses' WHERE id='msgbatch_notes'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE study_batches SET kind='other'")

    # the foreign keys are on again, and lemma_requests go with their batch
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.execute(
        """INSERT INTO lemma_requests(batch_id, custom_id, lemmas, lemma_count)
           VALUES ('msgbatch_parts', '12_forms_1', '["μῆνις"]', 1)"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO lemma_requests(batch_id, custom_id, lemmas, lemma_count)
               VALUES ('msgbatch_none', '12_forms_1', '["μῆνις"]', 1)"""
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """UPDATE lemma_requests SET status='failed' WHERE custom_id='12_forms_1'"""
        )
    conn.execute("DELETE FROM study_batches")
    for table in ("study_requests", "parts_requests", "lemma_requests"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0

    # lemma_forms is keyed by lemma and model; tb_nodes go with their text
    conn.execute(
        """INSERT INTO lemma_forms(lemma, model_used, pos, headword)
           VALUES ('μῆνις', 'claude-opus-5-5', 'noun', 'μῆνις, -ιος, ἡ')"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO lemma_forms(lemma, model_used, pos, headword)
               VALUES ('μῆνις', 'claude-opus-5-5', 'noun', 'x')"""
        )
    conn.execute(
        """INSERT INTO tb_nodes(text_id, sentence_id, node_id, head, relation, form)
           VALUES (12, 7, 1, 0, 'OBJ', 'μῆνιν')"""
    )
    conn.execute("DELETE FROM texts")
    assert conn.execute("SELECT count(*) FROM tb_nodes").fetchone()[0] == 0


# --- Frequency ----------------------------------------------------------------------


def test_book_span(iliad):
    db, text_id = iliad
    assert book_span(db, text_id, 3) == ("1", 1, 5)
    assert book_span(db, text_id, 7) == ("2", 6, 7)
    assert book_span(db, text_id, 99) is None


def test_lemma_frequency_per_text_and_book(iliad):
    db, text_id = iliad
    assert lemma_frequency(db, text_id, "μῆνις", 1, 5) == (1, 1)
    assert lemma_frequency(db, text_id, "δέ", 1, 5) == (2, 2)  # δʼ and δ̓ of οὐδʼ
    assert lemma_frequency(db, text_id, "δέ", 6, 7) == (2, 0)
    assert lemma_frequency(db, text_id, "λόγος", 1, 5) == (0, 0)


def test_lemma_frequency_ignores_a_homograph_digit(iliad):
    db, text_id = iliad
    db.execute(
        "UPDATE word_parses SET lemma='θεός2' WHERE text_id=? AND lemma='θεός'",
        (text_id,),
    )
    db.execute(
        "UPDATE word_parses SET lemma='θεός1' WHERE text_id=? AND lemma='θεά'",
        (text_id,),
    )
    db.commit()
    assert lemma_frequency(db, text_id, "θεός", 1, 5) == (2, 1)
    assert lemma_frequency(db, text_id, "θεός2", 6, 7) == (2, 1)


def test_word_frequency(iliad):
    db, text_id = iliad
    text = get_text(db, text_id)
    freq = word_frequency(db, text, 1, "μῆνις")
    assert (freq.total, freq.in_book, freq.book, freq.title) == (1, 1, "1", "Iliad")
    assert freq.only_here
    freq = word_frequency(db, text, 6, "δέ")
    assert (freq.total, freq.in_book, freq.book, freq.only_here) == (2, 0, "2", False)
    assert word_frequency(db, text, 1, "") is None
    assert word_frequency(db, text, 1, "λόγος") is None


# --- Roles from the saved nodes -----------------------------------------------------


def test_a_words_role_from_the_saved_tree(iliad):
    db, text_id = iliad
    role = word_role(db, _parses(db, text_id, 2, 0))  # οὐλομένην
    assert role.text == "Role: agrees with Μῆνιν (1.1)"
    assert (role.target.line_number, role.target.token_index) == (1, 0)
    assert word_role(db, _parses(db, text_id, 2, 5)).text == (
        "Role: verb of a relative clause on Μῆνιν (1.1), joined by δέ (1.3) with "
        "προΐαψεν (1.3); Subject: ἣ; Objects: Ἀχαιοῖς, ἄλγεʼ"
    )
    # through an elliptic apposition node
    assert word_role(db, _parses(db, text_id, 1, 4)).text == (
        "Role: genitive with Μῆνιν, in apposition to Πηληϊάδεω"
    )
    # under an elliptic verb
    assert word_role(db, _parses(db, text_id, 7, 1)).text == (
        "Role: predicative, with an understood verb"
    )


def test_a_split_word_takes_the_role_of_its_first_part_with_one(iliad):
    db, text_id = iliad
    # οὐδʼ = οὐ (AuxZ) + δ̓ (AuxY): particles, no role
    assert word_role(db, _parses(db, text_id, 5, 0)) is None
    assert word_role(db, _parses(db, text_id, 5, 1)).text == "Role: main verb"


def test_words_without_a_node_have_no_role(iliad):
    db, text_id = iliad
    assert word_role(db, _parses(db, text_id, 4, 0)) is None  # 1.4, not in it
    assert word_role(db, []) is None


def test_an_untagged_node_takes_the_models_parse(iliad):
    db, text_id = iliad
    assert word_role(db, _parses(db, text_id, 3, 4)).text == "Role: object of προΐαψεν"
    db.execute(
        """UPDATE word_parses SET lemma='Ἀΐδης', postag='n-s---md-', source='model',
           model_used='claude-opus-5-5' WHERE text_id=? AND line_number=3
           AND token_index=4""",
        (text_id,),
    )
    db.commit()
    assert word_role(db, _parses(db, text_id, 3, 4)).text == (
        "Role: dative object of προΐαψεν"
    )


# --- The word strip ---------------------------------------------------------------

OPUS = "claude-opus-5-5"
TITHEMI = [
    {"part": n, "name": "", "form": form, "notes": ""}
    for n, form in enumerate(
        ["τίθημι", "θήσω", "ἔθηκα", "τέθηκα", "τέθειμαι", "ἐτέθην"], 1
    )
]


@pytest.fixture
def reader(web_db):
    """The excerpt with an LSJ entry for μῆνις, three dictionary forms and the
    principal parts of τίθημι (ἔθηκε, 1.2)."""
    db, text_id = _import_excerpt(web_db)
    save_lexicon(
        db,
        "LSJ",
        [LexiconEntry("LSJ", "mh=nis", "μῆνις", "wrath", "<p>wrath</p>")],
        {"μῆνις": ("mh=nis", "headword")},
    )
    save_forms_result(
        db,
        [
            LemmaForms("μῆνις", OPUS, "noun", "μῆνις, -ιος, ἡ"),
            LemmaForms(
                "οὐλόμενος",
                OPUS,
                "adjective",
                "οὐλόμενος, -η, -ον",
                "epic for ὀλόμενος",
            ),
            LemmaForms(
                "πολύς", OPUS, "adjective", "πολύς, πολλή, πολύ", "", "πλείων, πλεῖστος"
            ),
        ],
    )
    save_verb_parts(db, "τίθημι", OPUS, TITHEMI)
    return db, text_id


def _strip(client, text_id, line, i):
    resp = client.get(f"/texts/{text_id}/word", params={"line": line, "i": i})
    assert resp.status_code == 200
    return resp.text


def test_word_strip_shows_the_role_and_the_word_it_names(client, reader):
    _, text_id = reader
    html = _strip(client, text_id, 2, 0)  # οὐλομένην
    assert 'data-line="2" data-i="0">' in html  # the strip knows its word
    assert '<div class="ws-col ws-parse has-syntax">' in html
    assert (
        '<dt>Role</dt><dd>agrees with <button type="button" class="syn-ref" '
        'lang="grc" data-line="1" data-i="0" title="Show it in the text">Μῆνιν'
        "</button> (1.1)</dd>" in html
    )
    # Parsing and Syntax tabs (shown on a laptop or tablet), Parsing first
    assert (
        '<button type="button" role="tab" class="ws-ptab" id="ws-ptab-parsing" '
        'aria-controls="ws-ppanel" aria-selected="true"' in html
    )
    assert 'id="ws-ptab-syntax" aria-controls="ws-spanel" aria-selected="false"' in html
    assert '<div class="ws-ppanel" id="ws-ppanel">' in html
    assert '<div class="ws-syntax" id="ws-spanel">' in html
    # each tab's source under it, the headings that carry it hidden then
    assert html.count(f'<p class="ws-panel-tag">{PERSEUS}</p>') == 2
    assert f'<h3 class="label">Syntax&nbsp;{PERSEUS}</h3>' in html


def test_a_verbs_syntax_and_the_principal_part_it_comes_from(client, reader):
    _, text_id = reader
    html = _strip(client, text_id, 2, 5)  # ἔθηκε, aorist active
    assert '<dt>Subject</dt><dd><span lang="grc">ἣ</span></dd>' in html
    assert (
        '<dt>Objects</dt><dd><span lang="grc">Ἀχαιοῖς</span>, '
        '<span lang="grc">ἄλγεʼ</span></dd>' in html
    )
    assert html.count('<li class="pp is-current">') == 1
    assert (
        '<li class="pp is-current"><span class="pp-name">Aorist'
        '<span class="pp-this">this form</span></span>' in html
    )
    assert 'τίθημι, θήσω, <mark class="pp-cur">ἔθηκα</mark>, τέθηκα' in html


def test_saved_principal_parts_marks_the_forms_part(reader):
    db, _ = reader
    aorist = {"tense": "aorist", "mood": "indicative", "voice": "active"}
    assert saved_principal_parts(db, "τίθημι", aorist).current == 3
    assert saved_principal_parts(db, "τίθημι").current is None


def test_word_strip_shows_the_lemmas_frequency(client, reader):
    db, text_id = reader
    assert '<p class="ws-freq">only here in the Iliad</p>' in _strip(
        client, text_id, 1, 0
    )
    html = _strip(client, text_id, 3, 1)  # δʼ, and the δ̓ of οὐδʼ
    assert (
        '<p class="ws-freq"><span class="ws-freq-n">2×</span> in the Iliad · '
        '<span class="ws-freq-n">2×</span> in Book 1</p>' in html
    )
    # thousands are grouped
    db.executemany(
        """INSERT INTO word_parses(text_id, line_number, token_index, part, form,
           lemma, postag, source) VALUES (?, 7, ?, 0, 'μῆνιν', 'μῆνις', 'n-s---fa-',
           'treebank')""",
        [(text_id, i) for i in range(10, 1210)],
    )
    db.commit()
    assert (
        '<span class="ws-freq-n">1,201×</span> in the Iliad · '
        '<span class="ws-freq-n">1×</span> in Book 1' in _strip(client, text_id, 1, 0)
    )


def test_word_strip_shows_the_dictionary_form(client, reader):
    _, text_id = reader
    html = _strip(client, text_id, 1, 0)  # μῆνιν, in LSJ
    assert 'from <span class="greek" lang="grc">μῆνις, -ιος, ἡ</span>' in html
    assert (
        'LSJ</span> <span class="greek" lang="grc">μῆνις, -ιος, ἡ</span> wrath</p>'
        in html
    )
    assert f'<p class="ws-dict-forms">{tag("AI")} dictionary form</p>' in html
    html = _strip(client, text_id, 2, 0)  # οὐλομένην, not in LSJ
    assert (
        '<p class="ws-muted"><span class="greek" lang="grc">οὐλόμενος, -η, -ον</span>'
        " is not in LSJ.</p>" in html
    )
    assert (
        f'<p class="ws-dict-forms">{tag("AI")} dictionary form · epic for ὀλόμενος'
        "</p>" in html
    )
    html = _strip(client, text_id, 3, 0)  # πολλὰς, irregular comparison
    assert (
        f'<p class="ws-dict-forms">{tag("AI")} dictionary form · comparison: πλείων, '
        "πλεῖστος</p>" in html
    )


def test_word_strip_without_word_details(client, reader):
    _, text_id = reader
    # ἄειδε: no dictionary form (a verb), so the bare lemma
    html = _strip(client, text_id, 1, 1)
    assert 'from <span class="greek" lang="grc">ἀείδω</span>' in html
    assert "ws-dict-forms" not in html and "pp-list" not in html
    assert "ws-constr" not in html and "syn-use" not in html
    # οὐδʼ, split by the treebank into particles: no role, no frequency
    html = _strip(client, text_id, 5, 0)
    assert '<div class="ws-col ws-parse">' in html
    assert "ws-syntax" not in html and "ws-ptabs" not in html
    assert "ws-freq" not in html
    # a line the treebank omits
    html = _strip(client, text_id, 4, 0)
    assert "The treebank has no parse for this word." in html
    assert "ws-syntax" not in html and "ws-freq" not in html
    # a role whose word is an understood verb names no word of the text
    html = _strip(client, text_id, 7, 1)
    assert "<dd>predicative, with an understood verb</dd>" in html
    assert "syn-ref" not in html


def test_a_verbs_construction_row_is_not_a_dictionary_form(client, reader):
    db, text_id = reader
    # a forms batch stores a verb's construction with its lemma as headword
    save_forms_result(
        db,
        [
            LemmaForms("ἀείδω", OPUS, "verb", "not shown", construction="+ acc."),
            LemmaForms("τίθημι", OPUS, "verb", "not shown", construction="+ acc."),
        ],
    )
    html = _strip(client, text_id, 1, 1)  # ἄειδε
    assert 'from <span class="greek" lang="grc">ἀείδω</span>' in html
    assert "ws-dict-forms" not in html and "not shown" not in html
    html = _strip(client, text_id, 2, 5)  # ἔθηκε: the lemma, not in LSJ
    assert '<span class="greek" lang="grc">τίθημι</span> is not in LSJ.' in html
    assert "ws-dict-forms" not in html and "not shown" not in html


def _use(text_id, line, i, label, extra=False):
    return WordUse(text_id, line, i, OPUS, label, "high", extra)


def test_word_strip_shows_the_use_and_the_construction(client, reader):
    db, text_id = reader
    save_uses_result(
        db,
        text_id,
        OPUS,
        1,
        2,
        [
            _use(text_id, 1, 1, "imperative of prayer: invocation of the Muse"),
            _use(text_id, 2, 3, "dative of disadvantage with ἔθηκε"),
        ],
    )
    save_forms_result(
        db,
        [
            LemmaForms(
                "τίθημι",
                OPUS,
                "verb",
                "τίθημι",
                construction="+ acc. (κῆρ, θυμόν), set in one's heart",
            )
        ],
    )
    html = _strip(client, text_id, 1, 1)  # ἄειδε: a role and a use
    assert '<div class="ws-col ws-parse has-syntax">' in html
    # the role is the treebank's (the heading's tag), the use the model's
    assert (
        f'<dt class="syn-use">Use</dt><dd>{tag("AI")} imperative of prayer: '
        "invocation of the Muse</dd>" in html
    )
    assert html.index("<dt>Role</dt>") < html.index('<dt class="syn-use">')
    assert f'<h3 class="label">Syntax&nbsp;{PERSEUS}</h3>' in html
    html = _strip(client, text_id, 2, 3)  # Ἀχαιοῖς: Greek in the label
    assert (
        f'<dd>{tag("AI")} dative of disadvantage with <span lang="grc">ἔθηκε</span>'
        "</dd>" in html
    )
    html = _strip(client, text_id, 2, 5)  # ἔθηκε: its construction, no use
    assert "syn-use" not in html
    assert (
        f'<div class="ws-constr">\n      <h3 class="label">Construction&nbsp;'
        f'{tag("AI")}</h3>\n      <p>+ acc. (<span lang="grc">κῆρ</span>, '
        '<span lang="grc">θυμόν</span>), set in one&#39;s heart</p>' in html
    )
    # under the principal parts, before the LSJ entry
    assert html.index('class="pp-list"') < html.index('class="ws-constr"')


def test_a_use_without_a_role_gets_a_syntax_block(client, reader):
    db, text_id = reader
    # οὐδʼ has no role; the model labelled it anyway (an extra)
    save_uses_result(
        db, text_id, OPUS, 5, 5, [_use(text_id, 5, 0, "negative of the clause", True)]
    )
    html = _strip(client, text_id, 5, 0)
    assert '<div class="ws-col ws-parse has-syntax">' in html
    assert 'id="ws-ptab-syntax"' in html  # the tabs, on a laptop or tablet
    assert (
        f'<dt class="syn-use">Use</dt><dd>{tag("AI")} negative of the clause</dd>'
        in html
    )
    assert "<dt>Role</dt>" not in html
    # no role, so no treebank's tag on Syntax or under it
    assert '<h3 class="label">Syntax</h3>' in html
    assert html.count('<p class="ws-panel-tag">') == 1  # Parsing's


def test_word_strip_of_a_text_without_a_treebank(client, db_text):
    text_id = db_text[1]
    html = _strip(client, text_id, 1, 0)
    assert "Μῆνιν" in html
    assert "No parsing is saved for this word." in html
    assert "Glosses and notes for the Iliad are not yet available." in html
    for name in (
        "ws-syntax",
        "ws-ptabs",
        "ws-freq",
        "ws-dict-forms",
        "is-current",
        "syn-use",
        "ws-constr",
    ):
        assert name not in html
