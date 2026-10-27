"""The reader with treebank parses and LSJ entries: word strip and "Grammar".

Model calls are never made; analyses, glosses and job states are saved or set
up directly.
"""

import re

import pytest

from greek.db.repos.lexicon import save_lexicon
from greek.db.repos.lines import insert_lines
from greek.db.repos.parses import save_treebank
from greek.db.repos.texts import insert_text
from greek.db.repos.verbs import save_verb_parts
from greek.models import LexiconEntry, WordParse
from greek.web import reader_data

LINES = [
    ("1.1", "μῆνιν ἄειδε θεὰ"),
    ("1.2", "οὐδʼ Ἄϊδι Ἑλένη"),
]
OPUS = "claude-opus-5-5"

# The titles of the tags for the study model and LSJ, as a page has them
AI_TITLE = (
    "Claude Opus 5.5, Anthropic&#39;s model: made once and saved; it can be wrong"
)
LSJ_TITLE = "Liddell, Scott, Jones, A Greek-English Lexicon · Perseus Digital Library"


def tag(name, title=AI_TITLE, automatic=False):
    """A source's tag as the reader draws it (_macros.html src)."""
    cls = "src is-auto" if automatic else "src"
    return (
        f'<span class="{cls}" title="{title}">'
        f'<span class="visually-hidden">source: </span>{name}</span>'
    )


PERSEUS = tag("Perseus", reader_data.PERSEUS_CREDIT.title)


def _parse(tid, line, token, form, lemma, postag, part=0, **extra):
    return WordParse(tid, line, token, part, form, lemma, postag, 100, **extra)


@pytest.fixture
def iliad(web_db):
    """Two lines with treebank parses and LSJ entries, like the real Iliad import."""
    db = web_db
    tid = insert_text(db, "Iliad", "Homer", "iliad.txt", "/d/iliad.txt", 2)
    insert_lines(db, tid, [c for _, c in LINES], [r for r, _ in LINES])
    save_treebank(
        db,
        tid,
        [],
        [
            _parse(tid, 1, 0, "μῆνιν", "μῆνις", "n-s---fa-"),
            _parse(tid, 1, 1, "ἄειδε", "ἀείδω", "v2spma---"),
            _parse(tid, 1, 2, "θεὰ", "θεά", "n-s---fv-"),
            _parse(tid, 2, 0, "οὐ", "οὐ", "d--------"),
            _parse(tid, 2, 0, "δ̓", "δέ", "g--------", part=1),
            _parse(tid, 2, 1, "Ἄϊδι", None, None),  # untagged in the treebank
            _parse(tid, 2, 2, "Ἑλένη", "Ἑλένη", "n-s---fn-"),
        ],
    )

    def entry(key, headword, short_def):
        html = f'<p><b lang="grc">{headword}</b>, <em>{short_def}</em></p>'
        return LexiconEntry("LSJ", key, headword, short_def, html)

    save_lexicon(
        db,
        "LSJ",
        [
            entry("mh=nis", "μῆνις", "wrath"),
            entry("a)ei/dw", "ἀείδω", "sing"),
            entry("ou)", "οὐ", "not"),
            entry("de/1", "δέ", "but; nay"),
        ],
        {
            "μῆνις": ("mh=nis", "headword"),
            "ἀείδω": ("a)ei/dw", "headword"),
            "θεά": None,
            "οὐ": ("ou)", "headword"),
            "δέ": ("de/1", "headword"),
            "Ἑλένη": None,
        },
    )
    db.execute(
        """UPDATE lemma_entries SET model_def='Helen, wife of Menelaus',
           model_used=? WHERE lemma='Ἑλένη'""",
        (OPUS,),
    )
    db.commit()
    return db, tid


def _word(client, tid, line, i):
    resp = client.get(f"/texts/{tid}/word", params={"line": line, "i": i})
    assert resp.status_code == 200
    return resp.text


def _passage(client, tid, lines):
    resp = client.get(f"/texts/{tid}/passage", params={"lines": lines})
    assert resp.status_code == 200
    return resp.text


# --- word strip ------------------------------------------------------------------


def test_word_strip_shows_treebank_parsing(client, iliad):
    _, tid = iliad
    html = _word(client, tid, 1, 0)
    assert f'<h3 class="label">Parsing&nbsp;{PERSEUS}</h3>' in html
    assert "<dt>Case</dt><dd>accusative</dd>" in html
    assert "<dt>Gender</dt><dd>feminine</dd>" in html
    assert '<span class="chip">noun</span>' in html
    assert 'from <span class="greek" lang="grc">μῆνις</span>' in html


def test_word_strip_shows_the_lsj_entry(client, iliad):
    _, tid = iliad
    html = _word(client, tid, 1, 0)
    assert "word-strip has-word has-dict" in html
    assert '<h2 class="label">Dictionary</h2>' in html
    assert '<span class="greek" lang="grc">μῆνις</span> wrath</p>' in html
    assert (
        f'{tag("LSJ", LSJ_TITLE)} <span class="greek" lang="grc">μῆνις</span>' in html
    )
    assert f'<h3 class="label">Full entry&nbsp;{tag("LSJ", LSJ_TITLE)}</h3>' in html
    # one button opens the full entry, however the layout shows it
    assert (
        'data-action="toggle-dict" aria-expanded="false" aria-controls="ws-lsj"' in html
    )
    assert '<span class="t-open">Full LSJ entry</span>' in html
    assert '<div class="ws-lsj" id="ws-lsj">' in html
    assert '<p><b lang="grc">μῆνις</b>, <em>wrath</em></p>' in html
    assert 'class="lsj-head' not in html  # one entry needs no headword of its own
    assert "Perseus Digital Library, CC BY-SA 4.0" in html
    assert "Glosses and notes for the Iliad are not yet available." in html


def test_word_strip_verb_without_saved_principal_parts(client, iliad):
    _, tid = iliad
    html = _word(client, tid, 1, 1)
    assert "<dt>Person</dt><dd>2nd</dd>" in html
    assert "<dt>Mood</dt><dd>imperative</dd>" in html
    # Nothing saved for ἀείδω: no parts, and nowhere to look them up
    assert "pp-list" not in html and "/verbs" not in html


def test_word_strip_parse_summary(client, iliad):
    _, tid = iliad
    html = _word(client, tid, 1, 1)
    assert (
        f'<p class="ws-summary">{PERSEUS} 2nd person · singular · present · '
        "imperative · active</p>" in html
    )


PARTS = [
    {"part": n, "name": name, "form": form, "notes": note}
    for n, (name, form, note) in enumerate(
        [
            ("Present active", "ἀείδω", "Attic ᾄδω"),
            ("Future active", "ἀείσομαι", ""),
            ("Aorist active", "ἤεισα", ""),
            ("Perfect active", "—", ""),
            ("Perfect middle/passive", "ᾖσμαι", "rare"),
            ("Aorist passive", "ᾔσθην", ""),
        ],
        1,
    )
]


def test_word_strip_shows_saved_principal_parts(client, iliad):
    db, tid = iliad
    save_verb_parts(db, "ἀείδω", OPUS, PARTS)
    html = _word(client, tid, 1, 1)
    assert '<ol class="pp-list" aria-label="Principal parts of ἀείδω">' in html
    assert html.count('<li class="pp') == 6
    # ἄειδε, a present imperative, comes from the first part
    assert html.count('<li class="pp is-current">') == 1
    assert (
        '<span class="pp-name">Present<span class="pp-this">this form</span></span>'
        in html
    )
    for label in ["Future", "Aorist", "Perfect", "Perfect mid./pass."]:
        assert f'<span class="pp-name">{label}</span>' in html
    assert (
        '<span class="pp-form" lang="grc">ἀείδω</span> '
        '<span class="pp-note">Attic ᾄδω</span>' in html
    )
    assert '<span class="pp-form" lang="grc">—</span></span>' in html  # no note
    assert '<mark class="pp-cur">ἀείδω</mark>, ἀείσομαι, ἤεισα, —, ᾖσμαι, ᾔσθην' in html
    assert f'<h3 class="label">Principal parts&nbsp;{tag("AI")}</h3>' in html
    assert f"Principal parts&nbsp;{tag('AI')}</span>" in html  # the summary's
    assert "ws-parts-source" not in html  # in the work's own dialect
    assert "/verbs" not in html


def test_word_strip_prefers_the_study_models_parts(client, iliad):
    db, tid = iliad
    other = [dict(p, form="x") for p in PARTS]
    save_verb_parts(db, "ἀείδω", "claude-sonnet-5-5", other)
    html = _word(client, tid, 1, 1)
    assert '<span class="pp-form" lang="grc">x</span>' in html  # any model's lookup
    save_verb_parts(db, "ἀείδω", OPUS, PARTS)
    save_verb_parts(db, "ἀείδω", "claude-sonnet-5-5", other)  # newer, but not Opus
    html = _word(client, tid, 1, 1)
    assert '<span class="pp-form" lang="grc">ἤεισα</span>' in html
    assert '<span class="pp-form" lang="grc">x</span>' not in html


def test_saved_principal_parts_reads_old_lookups():
    import sqlite3

    from greek.db.connection import run_migrations

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    run_migrations(db)
    assert reader_data.saved_principal_parts(db, "") is None
    assert reader_data.saved_principal_parts(db, "λύω") is None
    save_verb_parts(db, "λύω", "gpt-6", [{"part": "x", "name": "Odd", "form": ""}])
    parts = reader_data.saved_principal_parts(db, "λύω")
    assert [(p.name, p.form, p.note) for p in parts.parts] == [("Present", "—", "")]
    save_verb_parts(db, "λύω", "gpt-6", [])
    assert reader_data.saved_principal_parts(db, "λύω") is None


def test_principal_parts_only_for_verbs(client, iliad):
    db, tid = iliad
    save_verb_parts(db, "μῆνις", OPUS, PARTS)  # never shown for a noun
    assert "pp-list" not in _word(client, tid, 1, 0)


def test_word_strip_split_word_shows_both_parts(client, iliad):
    _, tid = iliad
    html = _word(client, tid, 2, 0)
    assert 'from <span class="greek" lang="grc">οὐ + δέ</span>' in html
    assert '<span class="chip">adverb + particle</span>' in html
    assert html.count('<div class="ws-part">') == 2
    assert (
        '<span class="greek" lang="grc">δέ</span><span class="chip">particle</span>'
        in html
    )
    assert html.count("Not inflected.") == 2
    # one dictionary line and full entry for each lemma
    assert '<span class="greek" lang="grc">οὐ</span> not</p>' in html
    assert '<span class="greek" lang="grc">δέ</span> but; nay</p>' in html
    assert '<h4 class="lsj-head greek" lang="grc">οὐ</h4>' in html
    assert '<h4 class="lsj-head greek" lang="grc">δέ</h4>' in html
    assert html.count('data-action="toggle-dict"') == 1


def test_word_strip_without_treebank_parse(client, iliad):
    _, tid = iliad
    html = _word(client, tid, 2, 1)
    assert "The treebank has no parse for this word." in html
    assert "Dictionary" not in html
    assert "has-dict" not in html


def test_word_strip_lemma_not_in_lsj(client, iliad):
    _, tid = iliad
    html = _word(client, tid, 1, 2)
    assert '<span class="greek" lang="grc">θεά</span> is not in LSJ.' in html


def test_word_strip_model_identification(client, iliad):
    _, tid = iliad
    html = _word(client, tid, 2, 2)
    assert (
        f'<p class="ws-dict-line">{tag("AI")} <span class="greek" lang="grc">Ἑλένη'
        "</span> Helen, wife of Menelaus</p>" in html
    )
    assert '<p class="ws-source">Not in LSJ.</p>' in html
    assert ">LSJ</span>" not in html  # a model's definition isn't labelled LSJ


def test_word_strip_model_parse_is_labelled(client, iliad):
    db, tid = iliad
    db.execute(
        """UPDATE word_parses SET lemma='Ἅιδης', postag='n-s---md-', source='model',
           model_used=? WHERE line_number=2 AND token_index=1""",
        (OPUS,),
    )
    db.commit()
    html = _word(client, tid, 2, 1)
    assert f'<h3 class="label">Parsing&nbsp;{tag("AI")}</h3>' in html
    assert '<p class="ws-source ws-source-top">Not in the treebank.</p>' in html
    assert "Perseus" not in html
    assert "<dt>Case</dt><dd>dative</dd>" in html


# --- passage box ---------------------------------------------------------------


def test_a_selection_without_study_data_has_all_words_from_the_treebank(client, iliad):
    _, tid = iliad
    html = _passage(client, tid, "1.1-1.2")
    assert "passage-box is-none has-tabs" in html
    assert "Grammar (6)" in html
    # the Fluent and Literal tabs say the text has no translations yet
    assert 'data-tab="literal" disabled' not in html
    assert (
        '<p class="pb-lead">Translations of the Iliad are not yet available</p>' in html
    )
    assert "Literal translations of the Iliad are not yet available" in html
    assert (
        "Every word's parsing, role and dictionary meaning are under Grammar." in html
    )
    assert "Analyze" not in html


def test_all_words_rows_from_the_treebank_and_lsj(client, iliad):
    _, tid = iliad
    html = _passage(client, tid, "1.1-1.2")
    assert 'data-line="1" data-i="0"' in html
    assert '<span class="wt-dict" title="Dictionary meaning (LSJ)">wrath</span>' in html
    assert "noun · acc. sg. fem." in html
    assert "verb · 2nd sg. pres. imper. act." in html
    # the split word: both lemmas, both parts of speech, both meanings
    assert '<span class="wt-lemma" lang="grc">οὐ + δέ</span>' in html
    assert "adverb + particle" in html
    assert ">not + but</span>" in html  # the first meaning of each part
    # a model's identification stands in for a lemma LSJ lacks
    assert ">Helen, wife of Menelaus</span>" in html


def _sources(html):
    """Grammar's legend as it reads: "AI meanings · Perseus parsing"."""
    found = re.search(r'<p class="wt-sources">(.*?)</p>', html, re.S)
    if not found:
        return None
    items = re.findall(
        r'<span class="wt-source">(.*?) ([^<>]*)</span>(?=<|$)', found[1]
    )
    return " · ".join(
        f"{re.sub(r'<[^>]+>|source: ', '', t)} {gives}" for t, gives in items
    )


def _parse_tags(html):
    """The tags at the start of Grammar's parse cells."""
    return re.findall(
        r'<span class="wt-parse" role="cell">(<span class="src[^>]*>'
        r'<span class="visually-hidden">source: </span>[^<]*</span>)',
        html,
    )


def test_all_words_names_lsj_and_the_treebank(client, iliad):
    _, tid = iliad
    html = _passage(client, tid, "1.1-1.2")
    assert _sources(html) == ("AI meanings LSJ lacks · Perseus parsing · LSJ meanings")
    assert _parse_tags(html) == []  # every parse is the treebank's


def test_all_words_labels_a_models_parse(client, iliad):
    db, tid = iliad
    db.execute(
        """UPDATE word_parses SET lemma='Ἅιδης', postag='n-s---md-', source='model',
           model_used=? WHERE line_number=2 AND token_index=1""",
        (OPUS,),
    )
    db.commit()
    html = _passage(client, tid, "1.1-1.2")
    # the legend names the treebank; the model's parse carries its own tag
    assert _sources(html).endswith("Perseus parsing · LSJ meanings")
    row = html[html.index('data-line="2" data-i="1"') :]
    row = row[: row.index('<span class="wt-note"')]
    assert f'<span class="wt-parse" role="cell">{tag("AI")} noun' in row
    assert _parse_tags(html) == [tag("AI")]


def test_long_selection_is_cut_with_a_count(client, iliad, monkeypatch):
    _, tid = iliad
    monkeypatch.setattr(reader_data, "MAX_STUDY_WORDS", 4)
    html = _passage(client, tid, "1.1-1.2")
    assert "Grammar (6)" in html  # every word is counted
    assert "3 more words in this selection." in html
    assert 'data-line="2"' not in html


def test_reader_page_enables_all_words_for_treebank_lines(client, iliad):
    _, tid = iliad
    html = client.get(f"/texts/{tid}/read", params={"lines": "1.1-1.2"}).text
    tab = html[html.index('id="reader-tab-words"') :].split("</button>")[0]
    assert "disabled" not in tab and "Grammar (6)" in tab
    # it says the text has no translations yet
    literal = html[html.index('id="reader-tab-literal"') :].split("</button>")[0]
    assert "disabled" not in literal


def test_text_without_treebank_has_no_tabs(client, web_db):
    tid = insert_text(web_db, "Odyssey", "Homer", "od.txt", "/d/od.txt", 1)
    insert_lines(web_db, tid, ["ἄνδρα μοι ἔννεπε"], ["1.1"])
    html = _passage(client, tid, "1.1-1.1")
    assert "passage-box is-none" in html and "has-tabs" not in html
    assert "Grammar (" not in html
    assert "Translations of the Odyssey are not yet available" in html
    assert "under Grammar" not in html
    word = _word(client, tid, 1, 0)
    assert "Glosses and notes for the Odyssey are not yet available." in word
    assert "Dictionary" not in word


def test_reader_has_a_dictionary_tab_and_a_passage_bar(client, iliad):
    _, tid = iliad
    html = client.get(f"/texts/{tid}/read", params={"lines": "1.1-1.2"}).text
    # reader.js enables the tab when a word with an LSJ entry is shown
    assert (
        'id="reader-tab-dictionary" data-reader-tab="dictionary" '
        'aria-controls="panel-word" aria-selected="false" tabindex="-1" disabled>'
        "Dictionary</button>" in html
    )
    assert '<div class="panel passage-bar" id="passage-bar">' in html
    assert 'data-action="close-dict"' in html


def test_selected_lines_can_expand_and_the_word_unfold(client, iliad):
    _, tid = iliad
    # reader.js shows the button on a laptop and paints its state
    html = _passage(client, tid, "1.1-1.2")
    assert (
        'class="btn btn-small pb-expand" data-action="expand-passage" '
        'aria-expanded="false" aria-controls="passage-box"' in html
    )
    assert "expand-passage" not in client.get(f"/texts/{tid}/passage").text
    page = client.get(f"/texts/{tid}/read", params={"lines": "1.1-1.2"}).text
    assert 'class="btn btn-small word-unfold" data-action="show-word"' in page
