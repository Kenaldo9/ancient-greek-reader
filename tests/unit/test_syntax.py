"""A word's role in its sentence from the treebank's tree (greek/syntax.py)."""

import pytest

from greek.display.analysis import _PUNCT_STRIP
from greek.models import TreebankNode
from greek.syntax import Mention, SentenceSyntax, split_relation

# Iliad 1.1–1.7 as the app prints it, and the Perseus treebank's tree of it
# (sentence 2274106): (node_id, form, lemma, postag, head, relation).
LINES = [
    ("1.1", "μῆνιν ἄειδε θεὰ Πηληϊάδεω Ἀχιλῆος"),
    ("1.2", "οὐλομένην, ἣ μυρίʼ Ἀχαιοῖς ἄλγεʼ ἔθηκε,"),
    ("1.3", "πολλὰς δʼ ἰφθίμους ψυχὰς Ἄϊδι προΐαψεν"),
    ("1.4", "ἡρώων, αὐτοὺς δὲ ἑλώρια τεῦχε κύνεσσιν"),
    ("1.5", "οἰωνοῖσί τε πᾶσι, Διὸς δʼ ἐτελείετο βουλή,"),
    ("1.6", "ἐξ οὗ δὴ τὰ πρῶτα διαστήτην ἐρίσαντε"),
    ("1.7", "Ἀτρεΐδης τε ἄναξ ἀνδρῶν καὶ δῖος Ἀχιλλεύς."),
]
PROEM = [
    (1, "μῆνιν", "μῆνις", "n-s---fa-", 2, "OBJ"),
    (2, "ἄειδε", "ἀείδω", "v2spma---", 32, "PRED_CO"),
    (3, "θεὰ", "θεά", "n-s---fv-", 2, "ExD"),
    (4, "Πηληϊάδεω", "Πηληιάδης", "n-s---mg-", 5, "ATR"),
    (5, "Ἀχιλῆος", "Ἀχιλλεύς", "n-s---mg-", 1, "ATR"),
    (6, "οὐλομένην", "οὐλόμενος", "a-s---fa-", 1, "ATR"),
    (7, ",", ",", "u--------", 23, "AuxX"),
    (8, "ἣ", "ὅς", "p-s---fn-", 23, "SBJ"),
    (9, "μυρί̓", "μυρίος", "a-p---na-", 11, "ATR"),
    (10, "Ἀχαιοῖς", "Ἀχαιός", "a-p---md-", 12, "OBJ"),
    (11, "ἄλγἐ", "ἄλγος", "n-p---na-", 12, "OBJ"),
    (12, "ἔθηκε", "τίθημι", "v3saia---", 23, "ATR_CO"),
    (13, ",", ",", "u--------", 23, "AuxX"),
    (14, "πολλὰς", "πολύς", "a-p---fa-", 17, "ATR"),
    (15, "δ̓", "δέ", "g--------", 23, "AuxY"),
    (16, "ἰφθίμους", "ἴφθιμος", "a-p---ma-", 17, "ATR"),
    (17, "ψυχὰς", "ψυχή", "n-p---fa-", 19, "OBJ"),
    (18, "Ἄϊδι", "", "", 19, "OBJ"),
    (19, "προΐαψεν", "προιάπτω", "v3saia---", 23, "ATR_CO"),
    (20, "ἡρώων", "ἥρως", "n-p---mg-", 17, "ATR"),
    (21, ",", ",", "u--------", 23, "AuxX"),
    (22, "αὐτοὺς", "αὐτός", "a-p---ma-", 25, "OBJ"),
    (23, "δὲ", "δέ", "g--------", 1, "COORD"),
    (24, "ἑλώρια", "ἑλώριον", "n-p---na-", 25, "OCOMP"),
    (25, "τεῦχε", "τεύχω", "v3siia---", 23, "ATR_CO"),
    (26, "κύνεσσιν", "κύων", "n-p---md-", 28, "OBJ_CO"),
    (27, "οἰωνοῖσί", "οἰωνός", "n-p---md-", 28, "OBJ_CO"),
    (28, "τε", "τε", "g--------", 25, "COORD"),
    (29, "πᾶσι", "πᾶς", "a-p---md-", 27, "ATR"),
    (30, ",", ",", "u--------", 32, "AuxX"),
    (31, "Διὸς", "Ζεύς", "n-s---mg-", 34, "ATR"),
    (32, "δ̓", "δέ", "g--------", 0, "COORD"),
    (33, "ἐτελείετο", "τελέω", "v3siie---", 32, "PRED_CO"),
    (34, "βουλή", "βουλή", "n-s---fn-", 33, "SBJ"),
    (35, ",", ",", "u--------", 41, "AuxX"),
    (36, "ἐξ", "ἐκ", "r--------", 41, "AuxP"),
    (37, "οὗ", "ὅς", "p-s---mg-", 36, "ADV"),
    (38, "δὴ", "δή", "g--------", 41, "AuxY"),
    (39, "τὰ", "ὁ", "l-p---na-", 40, "ATR"),
    (40, "πρῶτα", "πρῶτος", "a-p---na-", 41, "ADV"),
    (41, "διαστήτην", "διίστημι", "v3daia---", 33, "ADV"),
    (42, "ἐρίσαντε", "ἐρίζω", "v-dapamn-", 41, "ADV"),
    (43, "Ἀτρεΐδης", "Ἀτρείδης", "n-s---mn-", 47, "SBJ_CO"),
    (44, "τε", "τε", "g--------", 47, "AuxY"),
    (45, "ἄναξ", "ἄναξ", "n-s---mn-", 43, "ATR"),
    (46, "ἀνδρῶν", "ἀνήρ", "n-p---mg-", 45, "ATR"),
    (47, "καὶ", "καί", "c--------", 41, "COORD"),
    (48, "δῖος", "δῖος", "a-s---mn-", 49, "ATR"),
    (49, "Ἀχιλλεύς", "Ἀχιλλεύς", "n-s---mn-", 47, "SBJ_CO"),
    (50, ".", ".", "u--------", 0, "AuxK"),
]


def _nodes(rows, artificial=()):
    return [
        TreebankNode(
            text_id=12,
            sentence_id=1,
            node_id=n,
            head=head,
            relation=relation,
            form=form,
            lemma=lemma or None,
            postag=postag or None,
            artificial=n in artificial,
        )
        for n, form, lemma, postag, head, relation in rows
    ]


def _words(rows, lines, artificial=()):
    """node_id → (line_number, token_index, form, reference), words in order."""
    tokens = [
        (n, i, token.strip(_PUNCT_STRIP), ref)
        for n, (ref, content) in enumerate(lines, 1)
        for i, token in enumerate(content.split())
    ]
    words = [
        row[0]
        for row in rows
        if not (row[3] or "").startswith("u") and row[0] not in artificial
    ]
    assert len(words) == len(tokens)
    return dict(zip(words, tokens))


@pytest.fixture(scope="module")
def proem():
    return SentenceSyntax(_nodes(PROEM), _words(PROEM, LINES))


def _role(syntax, node_id):
    role = syntax.role(node_id)
    return role.text if role else None


def test_split_relation():
    assert split_relation("OBJ_AP_CO") == ("OBJ", ("AP", "CO"))
    assert split_relation("PRED") == ("PRED", ())
    assert split_relation("") == ("", ())


def test_object_subject_and_the_word_pointed_to(proem):
    role = proem.role(1)
    assert role.text == "Role: object of ἄειδε"
    assert role.target == Mention("ἄειδε", 1, 1, "", link=True)
    assert _role(proem, 34) == "Role: subject of ἐτελείετο"


def test_dative_and_genitive_objects(proem):
    assert _role(proem, 10) == "Role: dative object of ἔθηκε"
    assert _role(proem, 26).startswith("Role: dative object of τεῦχε")


def test_attributes(proem):
    # an adjective agreeing with its noun, which is on another line
    role = proem.role(6)
    assert role.text == "Role: agrees with μῆνιν (1.1)"
    assert role.target == Mention("μῆνιν", 1, 0, "1.1", link=True)
    assert _role(proem, 39) == "Role: agrees with πρῶτα"  # the article
    assert _role(proem, 5) == "Role: genitive with μῆνιν"
    # two genitives: possession or apposition, so only "genitive with"
    assert _role(proem, 4) == "Role: genitive with Ἀχιλῆος"
    # a noun in the same case as its noun
    assert _role(proem, 45) == "Role: in apposition to Ἀτρεΐδης"


def test_a_relative_clause_verb_with_its_coordinated_verbs_and_arguments(proem):
    assert proem.role(12).text == (
        "Role: verb of a relative clause on μῆνιν (1.1), joined by δέ (1.4) with "
        "προΐαψεν (1.3) and τεῦχε (1.4); Subject: ἣ; Objects: Ἀχαιοῖς, ἄλγεʼ"
    )
    # the shared subject depends on the conjunction, and names every verb
    assert _role(proem, 8) == "Role: subject of ἔθηκε, προΐαψεν (1.3) and τεῦχε (1.4)"


def test_a_main_verb_points_to_the_verb_it_is_joined_with(proem):
    role = proem.role(2)
    assert role.text == (
        "Role: main verb, joined by δέ (1.5) with ἐτελείετο (1.5); "
        "Object: μῆνιν; Addressed: θεὰ"
    )
    assert role.target == Mention("ἐτελείετο", 5, 5, "1.5", link=True)


def test_vocative(proem):
    role = proem.role(3)
    assert role.text == "Role: addressed"
    assert role.target is None


def test_coordinated_subjects_and_their_verb(proem):
    assert _role(proem, 43) == (
        "Role: subject of διαστήτην (1.6), joined by καί with Ἀχιλλεύς"
    )
    assert _role(proem, 41) == (
        "Role: adverbial, with ἐτελείετο (1.5); Subjects: Ἀτρεΐδης (1.7), "
        "Ἀχιλλεύς (1.7)"
    )


def test_conjunctions(proem):
    assert _role(proem, 47) == "Role: joins Ἀτρεΐδης and Ἀχιλλεύς"
    assert _role(proem, 23) == ("Role: joins ἔθηκε (1.2), προΐαψεν (1.3) and τεῦχε")


def test_a_preposition_and_the_word_it_governs(proem):
    assert _role(proem, 36) == "Role: preposition, governing οὗ"
    assert _role(proem, 37) == "Role: with ἐξ, adverbial to διαστήτην"


def test_object_complement(proem):
    assert _role(proem, 24) == "Role: object complement of τεῦχε"


def test_particles_and_punctuation_have_no_role(proem):
    assert proem.role(15) is None  # δ̓, AuxY
    assert proem.role(38) is None  # δὴ
    assert proem.role(7) is None  # a comma
    assert proem.role(99) is None  # not in the sentence


def test_an_untagged_word_still_has_a_role(proem):
    assert _role(proem, 18) == "Role: object of προΐαψεν"


# --- Small trees for what the proem lacks ------------------------------------------


def _syntax(rows, line_words, artificial=()):
    lines = [(ref, " ".join(words)) for ref, words in line_words]
    return SentenceSyntax(_nodes(rows, artificial), _words(rows, lines, artificial))


def test_a_subordinate_clause_verb():
    # ὅτε ἦλθε, χαῖρε: "verb of the clause with ὅτε"
    syntax = _syntax(
        [
            (1, "ὅτε", "ὅτε", "c--------", 3, "AuxC"),
            (2, "ἦλθε", "ἔρχομαι", "v3saia---", 1, "ADV"),
            (3, "χαῖρε", "χαίρω", "v3siia---", 0, "PRED"),
        ],
        [("1.1", ["ὅτε", "ἦλθε,"]), ("1.2", ["χαῖρε"])],
    )
    assert (
        _role(syntax, 2)
        == "Role: verb of the clause with ὅτε, adverbial to χαῖρε (1.2)"
    )
    assert _role(syntax, 1) == "Role: introduces the clause of ἦλθε"


def test_a_word_under_an_elliptic_verb():
    # θεοί τε εὗδον [ἦσαν] παννύχιοι: an understood verb
    syntax = _syntax(
        [
            (1, "θεοί", "θεός", "n-p---mn-", 2, "SBJ"),
            (2, "τε", "τε", "g--------", 0, "COORD"),
            (3, "εὗδον", "εὕδω", "v3piia---", 2, "PRED_CO"),
            (4, "παννύχιοι", "παννύχιος", "a-p---mn-", 5, "ATV"),
            (5, "[0]", "", "", 2, "PRED_CO"),
        ],
        [("2.1", ["θεοί", "τε", "εὗδον", "παννύχιοι."])],
        artificial={5},
    )
    assert _role(syntax, 4) == "Role: predicative, with an understood verb"
    assert _role(syntax, 3) == (
        "Role: main verb, joined by τε with an understood verb; Subject: θεοί"
    )
    assert _role(syntax, 1) == "Role: subject of εὗδον and an understood verb"


def test_apposition_through_an_elliptic_node():
    syntax = _syntax(
        [
            (1, "μῆνιν", "μῆνις", "n-s---fa-", 2, "OBJ"),
            (2, "ἄειδε", "ἀείδω", "v2spma---", 0, "PRED"),
            (3, "Πηληϊάδεω", "Πηληιάδης", "n-s---mg-", 5, "ATR_AP"),
            (4, "Ἀχιλῆος", "Ἀχιλλεύς", "n-s---mg-", 5, "ATR_AP"),
            (5, "[0]", "", "", 1, "APOS"),
        ],
        [("1.1", ["μῆνιν", "ἄειδε", "Πηληϊάδεω", "Ἀχιλῆος"])],
        artificial={5},
    )
    assert _role(syntax, 3) == "Role: genitive with μῆνιν"
    assert _role(syntax, 4) == "Role: genitive with μῆνιν, in apposition to Πηληϊάδεω"


def test_a_prepositional_phrase_the_verb_takes_and_an_elided_preposition():
    syntax = _syntax(
        [
            (1, "ἐπ̓", "ἐπί", "r--------", 3, "AuxP"),
            (2, "ὤμων", "ὦμος", "n-p---mg-", 1, "OBJ"),
            (3, "ἔκλαγξαν", "κλάζω", "v3paia---", 0, "PRED"),
        ],
        [("1.46", ["ἐπʼ", "ὤμων", "ἔκλαγξαν"])],
    )
    assert _role(syntax, 2) == "Role: with ἐπί, complement of ἔκλαγξαν"


def test_a_conjunction_with_one_member_and_asyndeton():
    syntax = _syntax(
        [
            (1, "βῆ", "βαίνω", "v3saia---", 2, "PRED_CO"),
            (2, "δ̓", "δέ", "g--------", 0, "COORD"),
            (3, "ἴθι", "εἶμι", "v2spma---", 5, "PRED_CO"),
            (4, "ἐρέθιζε", "ἐρεθίζω", "v2spma---", 5, "PRED_CO"),
            (5, ",", ",", "u--------", 2, "COORD"),
        ],
        [("1.34", ["βῆ", "δʼ", "ἴθι", "ἐρέθιζε"])],
    )
    assert _role(syntax, 2).startswith("Role: joins βῆ")
    assert _role(syntax, 3) == "Role: main verb, joined with ἐρέθιζε"
    one = _syntax(
        [
            (1, "βῆ", "βαίνω", "v3saia---", 2, "PRED_CO"),
            (2, "δ̓", "δέ", "g--------", 0, "COORD"),
        ],
        [("1.34", ["βῆ", "δʼ"])],
    )
    assert _role(one, 2) == "Role: joins βῆ to what comes before"
    assert _role(one, 1) == "Role: main verb"


def test_predicate_words_and_a_verb_with_one():
    syntax = _syntax(
        [
            (1, "αἴτιοί", "αἴτιος", "a-p---mn-", 2, "PNOM"),
            (2, "εἰσιν", "εἰμί", "v3ppia---", 0, "PRED"),
        ],
        [("1.153", ["αἴτιοί", "εἰσιν"])],
    )
    assert _role(syntax, 1) == "Role: predicate adjective with εἰσιν"
    assert _role(syntax, 2) == "Role: main verb; Predicate: αἴτιοί"


def test_a_word_under_a_particle_gets_no_role():
    # a slip in the treebank (βοῦς under δὲ, 1.154): no role rather than a wrong one
    syntax = _syntax(
        [
            (1, "βοῦς", "βοῦς", "n-p---fa-", 2, "OBJ"),
            (2, "δὲ", "δέ", "g--------", 3, "AuxY"),
            (3, "ἤλασαν", "ἐλαύνω", "v3paia---", 0, "PRED"),
        ],
        [("1.154", ["βοῦς", "δὲ", "ἤλασαν"])],
    )
    assert syntax.role(1) is None


def test_cycles_in_the_treebank_do_not_hang():
    syntax = _syntax(
        [
            (1, "ἐν", "ἐν", "r--------", 2, "AuxP"),
            (2, "νηυσί", "ναῦς", "n-p---fd-", 1, "AuxP"),  # a cycle of prepositions
            (3, "δ̓", "δέ", "g--------", 3, "COORD"),  # its own head
            (4, "ἔβη", "βαίνω", "v3saia---", 3, "PRED_CO"),
        ],
        [("1.1", ["ἐν", "νηυσί", "δʼ", "ἔβη"])],
    )
    for node_id in (1, 2, 3, 4):
        syntax.role(node_id)  # returns
    assert _role(syntax, 4) == "Role: main verb"


def test_a_word_without_a_place_in_the_text_is_named_without_a_link():
    syntax = SentenceSyntax(
        _nodes(
            [
                (1, "μῆνιν", "μῆνις", "n-s---fa-", 2, "OBJ"),
                (2, "ἄειδε", "ἀείδω", "v2spma---", 0, "PRED"),
            ]
        ),
        {1: (1, 0, "μῆνιν", "1.1")},
    )
    role = syntax.role(1)
    assert role.text == "Role: object of ἄειδε"
    assert role.target is None


def test_split_relation_reads_glaux_spellings():
    # a bare CO is a coordinated member whose own relation GLAUx leaves out
    assert split_relation("CO") == ("", ("CO",))
    assert split_relation("EXD") == ("ExD", ())
    assert split_relation("OVJ") == ("OBJ", ())
    assert split_relation("OBJ°CO") == ("OBJ", ("CO",))
    assert split_relation("MWE_CO") == ("MWE", ("CO",))


def test_glaux_only_relations_have_no_role():
    nodes = [
        TreebankNode(0, 1, 1, 0, "PRED", "ἔφη", "φημί", "v3siia---"),
        TreebankNode(0, 1, 2, 1, "MWE", "-δ’", "δέ", "b--------"),
        TreebankNode(0, 1, 3, 1, "UNLINKED", "τις", "τις", "p-s---mn-"),
        TreebankNode(0, 1, 4, 1, "CO", "ἤδη", "ἤδη", "d--------"),
    ]
    words = {n: (1, n - 1, nodes[n - 1].form, "1.1") for n in (1, 2, 3, 4)}
    tree = SentenceSyntax(nodes, words)
    assert tree.role(1) is not None
    assert [tree.role(n) for n in (2, 3, 4)] == [None, None, None]


def test_glaux_apposition_without_members_is_in_apposition():
    # GLAUx: ὡς ἐπολέμησαν, "how they fought", in apposition to πόλεμον
    # (Thucydides 1.1.1), and a noun in apposition without a clause
    syntax = _syntax(
        [
            (1, "ξυνέγραψε", "συγγράφω", "v3saia---", 0, "PRED"),
            (2, "πόλεμον", "πόλεμος", "n-s---ma-", 1, "OBJ"),
            (3, "ὡς", "ὡς", "c--------", 2, "AuxC"),
            (4, "ἐπολέμησαν", "πολεμέω", "v3paia---", 3, "APOS"),
            (5, "πρὸς", "πρός", "r--------", 4, "AuxP"),
            (6, "ἀλλήλους", "ἀλλήλων", "p-p---ma-", 5, "ADV"),
            (7, "ἔργον", "ἔργον", "n-s---na-", 2, "APOS"),
        ],
        [
            ("1.1.1", ["ξυνέγραψε", "πόλεμον", "ὡς", "ἐπολέμησαν", "πρὸς"]),
            ("1.1.2", ["ἀλλήλους", "ἔργον"]),
        ],
    )
    assert _role(syntax, 4) == (
        "Role: verb of the clause with ὡς, in apposition to πόλεμον"
    )
    assert _role(syntax, 7) == "Role: in apposition to πόλεμον (1.1.1)"
    assert _outline(syntax) == [
        "ξυνέγραψε · main verb",
        "  πόλεμον · object",
        "    ὡς … ἐπολέμησαν · clause, in apposition",
        "      πρὸς ἀλλήλους · adverbial",
        "    ἔργον · in apposition",
    ]


# --- Outlines (the passage box's Structure view) ----------------------------------


def _outline(syntax):
    """The outline as indented lines: "  ἔθηκε · relative clause"."""
    found = []

    def add(rows):
        for r in rows:
            found.append("  " * r.depth + f"{r.greek} · {r.role}")
            add(r.children)

    add(syntax.outline())
    return found


def _rows(syntax):
    """Every row of the outline, in order."""
    return [r for top in syntax.outline() for r in (top, *top.descendants())]


def test_the_outline_of_the_proem(proem):
    assert _outline(proem) == [
        "ἄειδε · main verb",
        "  μῆνιν · object",
        "    Ἀχιλῆος · genitive",
        "      Πηληϊάδεω · genitive",
        "    οὐλομένην · agrees",
        # a dependent the coordinated verbs share comes first and names them
        "    ἣ · subject of ἔθηκε, προΐαψεν and τεῦχε",
        "    ἔθηκε · relative clause",
        "      Ἀχαιοῖς · dative object",
        "      ἄλγεʼ · object",
        "        μυρίʼ · agrees",
        "    προΐαψεν · relative clause, joined by δέ",
        "      ψυχὰς · object",
        "        πολλὰς · agrees",
        "        ἰφθίμους · agrees",
        "        ἡρώων · genitive",
        "      Ἄϊδι · object",
        "    τεῦχε · relative clause, joined by δέ",
        "      αὐτοὺς · object",
        "      κύνεσσιν · dative object",
        "      οἰωνοῖσί · dative object, joined by τε",
        "        πᾶσι · agrees",
        "      ἑλώρια · object complement",
        "  θεὰ · addressed",
        "ἐτελείετο · main verb, joined by δέ",
        "  βουλή · subject",
        "    Διὸς · genitive",
        "  διαστήτην · adverbial",
        "    Ἀτρεΐδης · subject",
        "      ἄναξ · in apposition",
        "        ἀνδρῶν · genitive",
        "    Ἀχιλλεύς · subject, joined by καί",
        "      δῖος · agrees",
        # the preposition on its object's row
        "    ἐξ οὗ · adverbial",
        "    πρῶτα · adverbial",
        "      τὰ · agrees",
        "    ἐρίσαντε · adverbial",
    ]


def test_outline_rows_carry_their_words_and_fold(proem):
    rows = _rows(proem)
    assert [r.key for r in rows] == list(range(len(rows)))  # numbered in order
    # particles, conjunctions and punctuation have no row; every other word has
    # one (44 words, 7 of them particles and conjunctions)
    words = {p for r in rows for p in r.places}
    assert len(words) == 37
    assert (3, 1) not in words  # δʼ
    by_greek = {r.greek: r for r in rows}
    ex = by_greek["ἐξ οὗ"]
    assert ex.places == [(6, 0), (6, 1)]
    assert ex.word.form == "οὗ" and ex.word.place == (6, 1)  # the row opens οὗ
    # clauses: main verbs and relative clauses, joined ones too
    assert [r.greek for r in rows if r.clause] == [
        "ἄειδε",
        "ἔθηκε",
        "προΐαψεν",
        "τεῦχε",
        "ἐτελείετο",
    ]
    # a row below the top with three rows or more under it folds
    assert by_greek["μῆνιν"].foldable
    assert by_greek["μῆνιν"].hidden_words == 20
    assert by_greek["ψυχὰς"].foldable and not by_greek["Ἀχιλῆος"].foldable
    assert not by_greek["ἄειδε"].foldable  # a sentence's top row doesn't
    assert not by_greek["Ἀτρεΐδης"].foldable  # two rows under it


def test_outline_of_a_subordinate_clause():
    syntax = _syntax(
        [
            (1, "ὅτε", "ὅτε", "c--------", 4, "AuxC"),
            (2, "ἦλθε", "ἔρχομαι", "v3saia---", 1, "ADV"),
            (3, "οἴκαδε", "οἴκαδε", "d--------", 2, "ADV"),
            (4, "χαῖρε", "χαίρω", "v3siia---", 0, "PRED"),
        ],
        [("1.1", ["ὅτε", "ἦλθε", "οἴκαδε,"]), ("1.2", ["χαῖρε"])],
    )
    assert _outline(syntax) == [
        "χαῖρε · main verb",
        "  ὅτε … ἦλθε · clause, adverbial",
        "    οἴκαδε · adverbial",
    ]
    clause = syntax.outline()[0].children[0]
    assert clause.clause and clause.joiner == " … "
    assert clause.word.form == "ἦλθε"


def test_outline_with_an_understood_verb():
    syntax = _syntax(
        [
            (1, "θεοί", "θεός", "n-p---mn-", 2, "SBJ"),
            (2, "τε", "τε", "g--------", 0, "COORD"),
            (3, "εὗδον", "εὕδω", "v3piia---", 2, "PRED_CO"),
            (4, "παννύχιοι", "παννύχιος", "a-p---mn-", 5, "ATV"),
            (5, "[0]", "", "", 2, "PRED_CO"),
        ],
        [("2.1", ["θεοί", "τε", "εὗδον", "παννύχιοι."])],
        artificial={5},
    )
    assert _outline(syntax) == [
        "θεοί · subject of εὗδον and [understood verb]",
        "εὗδον · main verb",
        "understood verb · main verb, joined by τε",
        "  παννύχιοι · predicative",
    ]
    understood = syntax.outline()[2]
    assert understood.words[0].understood and understood.word is None
    assert understood.places == []


def test_outline_of_an_apposition():
    syntax = _syntax(
        [
            (1, "μῆνιν", "μῆνις", "n-s---fa-", 2, "OBJ"),
            (2, "ἄειδε", "ἀείδω", "v2spma---", 0, "PRED"),
            (3, "Πηληϊάδεω", "Πηληιάδης", "n-s---mg-", 5, "ATR_AP"),
            (4, "Ἀχιλῆος", "Ἀχιλλεύς", "n-s---mg-", 5, "ATR_AP"),
            (5, "[0]", "", "", 1, "APOS"),
        ],
        [("1.1", ["μῆνιν", "ἄειδε", "Πηληϊάδεω", "Ἀχιλῆος"])],
        artificial={5},
    )
    assert _outline(syntax) == [
        "ἄειδε · main verb",
        "  μῆνιν · object",
        "    Πηληϊάδεω · genitive",
        "      Ἀχιλῆος · genitive, in apposition",
    ]


def test_outline_of_a_preposition_with_coordinated_objects():
    # ἐκ Διὸς καὶ Ἥρης: the preposition has its own row
    syntax = _syntax(
        [
            (1, "ἦλθεν", "ἔρχομαι", "v3saia---", 0, "PRED"),
            (2, "ἐκ", "ἐκ", "r--------", 1, "AuxP"),
            (3, "Διὸς", "Ζεύς", "n-s---mg-", 4, "ADV_CO"),
            (4, "καὶ", "καί", "c--------", 2, "COORD"),
            (5, "Ἥρης", "Ἥρα", "n-s---fg-", 4, "ADV_CO"),
        ],
        [("1.1", ["ἦλθεν", "ἐκ", "Διὸς", "καὶ", "Ἥρης"])],
    )
    assert _outline(syntax) == [
        "ἦλθεν · main verb",
        "  ἐκ · preposition",
        "    Διὸς · adverbial",
        "    Ἥρης · adverbial, joined by καί",
    ]


def test_shared_dependents_name_the_words_they_belong_to():
    syntax = _syntax(
        [
            (1, "εἶδε", "ὁράω", "v3saia---", 0, "PRED"),
            (2, "πολλοὺς", "πολύς", "a-p---ma-", 4, "ATR"),
            (3, "ἵππους", "ἵππος", "n-p---ma-", 4, "OBJ_CO"),
            (4, "καὶ", "καί", "c--------", 1, "COORD"),
            (5, "ἄνδρας", "ἀνήρ", "n-p---ma-", 4, "OBJ_CO"),
            (6, "ὦ", "ὦ", "e--------", 7, "AuxZ"),
            (7, "φίλε", "φίλος", "a-s---mv-", 9, "ExD"),
            (8, "ἦλθε", "ἔρχομαι", "v3saia---", 9, "PRED_CO"),
            (9, "δὲ", "δέ", "g--------", 0, "COORD"),
            (10, "ἔβη", "βαίνω", "v3saia---", 9, "PRED_CO"),
        ],
        [
            ("1.1", ["εἶδε", "πολλοὺς", "ἵππους", "καὶ", "ἄνδρας."]),
            ("1.2", ["ὦ", "φίλε,", "ἦλθε", "δὲ", "ἔβη"]),
        ],
    )
    assert _outline(syntax) == [
        "εἶδε · main verb",
        "  πολλοὺς · agrees with ἵππους and ἄνδρας",
        "  ἵππους · object",
        "  ἄνδρας · object, joined by καί",
        "φίλε · addressed",  # names no words
        "ἦλθε · main verb",
        "ἔβη · main verb, joined by δέ",
    ]


def test_outline_skips_particles_and_lifts_the_words_under_them():
    syntax = _syntax(
        [
            (1, "βοῦς", "βοῦς", "n-p---fa-", 2, "OBJ"),
            (2, "δὲ", "δέ", "g--------", 3, "AuxY"),
            (3, "ἤλασαν", "ἐλαύνω", "v3paia---", 0, "PRED"),
            (4, ".", ".", "u--------", 0, "AuxK"),
        ],
        [("1.154", ["βοῦς", "δὲ", "ἤλασαν."])],
    )
    assert _outline(syntax) == ["ἤλασαν · main verb", "  βοῦς · object"]


def test_outline_of_a_conjunction_without_members_and_glaux_relations():
    nodes = [
        TreebankNode(0, 1, 1, 0, "PRED", "ἔφη", "φημί", "v3siia---"),
        TreebankNode(0, 1, 2, 1, "MWE", "-δ’", "δέ", "b--------"),
        TreebankNode(0, 1, 3, 1, "UNLINKED", "τις", "τις", "p-s---mn-"),
        TreebankNode(0, 1, 4, 1, "COORD", "καὶ", "καί", "b--------"),
        TreebankNode(0, 1, 5, 4, "ADV", "ταχέως", "ταχέως", "d--------"),
    ]
    words = {n: (1, n - 1, nodes[n - 1].form, "1.1") for n in range(1, 6)}
    assert _outline(SentenceSyntax(nodes, words)) == [
        "ἔφη · main verb",
        "  ταχέως · adverbial",
    ]


def test_an_outline_of_a_tree_with_cycles_returns():
    syntax = _syntax(
        [
            (1, "ἔβη", "βαίνω", "v3saia---", 0, "PRED"),
            (2, "ἐν", "ἐν", "r--------", 3, "AuxP"),  # a cycle of prepositions
            (3, "νηυσί", "ναῦς", "n-p---fd-", 2, "AuxP"),
            (4, "δʼ", "δέ", "g--------", 4, "COORD"),  # its own head
        ],
        [("1.1", ["ἔβη", "ἐν", "νηυσί", "δʼ"])],
    )
    assert _outline(syntax) == ["ἔβη · main verb"]


# --- Found checking GLAUx's Herodotus Book 1 ------------------------------------


def test_a_coordination_nested_in_a_coordination_is_a_member():
    # Herodotus 1.30.5: καί joins ἀπέθανε to ἔθαψαν καὶ ἐτίμησαν, a
    # coordination whose relation is a bare COORD
    syntax = _syntax(
        [
            (1, "ἀπέθανε", "ἀποθνῄσκω", "v3saia---", 2, "PRED_CO"),
            (2, "καί", "καί", "c--------", 0, "COORD"),
            (3, "μιν", "μιν", "p3s---ma-", 4, "OBJ"),
            (4, "ἔθαψαν", "θάπτω", "v3paia---", 5, "PRED_CO"),
            (5, "καὶ", "καί", "c--------", 2, "COORD"),
            (6, "ἐτίμησαν", "τιμάω", "v3paia---", 5, "PRED_CO"),
        ],
        [("1.30.5", ["ἀπέθανε", "καί", "μιν", "ἔθαψαν", "καὶ", "ἐτίμησαν"])],
    )
    assert _role(syntax, 1) == "Role: main verb, joined by καί with ἔθαψαν and ἐτίμησαν"
    assert _role(syntax, 2) == "Role: joins ἀπέθανε, ἔθαψαν and ἐτίμησαν"
    assert _role(syntax, 4) == (
        "Role: main verb, joined by καί with ἐτίμησαν; Object: μιν"
    )
    assert _outline(syntax) == [
        "ἀπέθανε · main verb",
        "ἔθαψαν · main verb, joined by καί",
        "  μιν · object",
        "ἐτίμησαν · main verb, joined by καί",
    ]


def test_a_coordination_the_members_share_is_no_member():
    # vocatives shared by two coordinated verbs (as in Iliad 3.276)
    syntax = _syntax(
        [
            (1, "Ζεῦ", "Ζεύς", "n-s---mv-", 2, "ExD_CO"),
            (2, "καὶ", "καί", "c--------", 5, "COORD"),
            (3, "Ἠέλιε", "Ἥλιος", "n-s---mv-", 2, "ExD_CO"),
            (4, "ἔστε", "εἰμί", "v2ppia---", 5, "PRED_CO"),
            (5, "δὲ", "δέ", "g--------", 0, "COORD"),
            (6, "φυλάσσετε", "φυλάσσω", "v2ppma---", 5, "PRED_CO"),
        ],
        [("3.276", ["Ζεῦ", "καὶ", "Ἠέλιε,", "ἔστε", "δὲ", "φυλάσσετε"])],
    )
    assert _role(syntax, 5) == "Role: joins ἔστε and φυλάσσετε"
    assert _role(syntax, 4) == (
        "Role: main verb, joined by δέ with φυλάσσετε; Addressed: Ζεῦ, Ἠέλιε"
    )
    assert _outline(syntax) == [
        "Ζεῦ · addressed",
        "Ἠέλιε · addressed, joined by καί",
        "ἔστε · main verb",
        "φυλάσσετε · main verb, joined by δέ",
    ]


def test_an_attribute_of_coordinated_words_is_compared_with_the_first():
    # Herodotus 1.82.4: Ἀργείων with Ἀλκήνωρ τε καὶ Χρομίος is a genitive
    syntax = _syntax(
        [
            (1, "Ἀργείων", "Ἀργεῖος", "a-p---mg-", 4, "ATR"),
            (2, "Ἀλκήνωρ", "Ἀλκήνωρ", "n-s---mn-", 4, "SBJ_CO"),
            (3, "τε", "τε", "g--------", 2, "AuxY"),
            (4, "καὶ", "καί", "c--------", 6, "COORD"),
            (5, "Χρομίος", "Χρομίος", "n-s---mn-", 4, "SBJ_CO"),
            (6, "ὑπελείποντο", "ὑπολείπω", "v3piim---", 0, "PRED"),
            (7, "ἀγαθοὶ", "ἀγαθός", "a-p---mn-", 9, "ATR"),
            (8, "ἄνδρες", "ἀνήρ", "n-p---mn-", 9, "SBJ_CO"),
            (9, "καὶ", "καί", "c--------", 11, "COORD"),
            (10, "ἵπποι", "ἵππος", "n-p---mn-", 9, "SBJ_CO"),
            (11, "ἦλθον", "ἔρχομαι", "v3paia---", 0, "PRED"),
        ],
        [
            ("1.82.4", ["Ἀργείων", "Ἀλκήνωρ", "τε", "καὶ", "Χρομίος", "ὑπελείποντο."]),
            ("1.82.5", ["ἀγαθοὶ", "ἄνδρες", "καὶ", "ἵπποι", "ἦλθον"]),
        ],
    )
    assert _role(syntax, 1) == "Role: genitive with Ἀλκήνωρ and Χρομίος"
    assert not syntax.agrees(1)
    assert _role(syntax, 7) == "Role: agrees with ἄνδρες and ἵπποι"
    assert syntax.agrees(7)


def test_a_prepositional_predicate_is_neither_noun_nor_adjective():
    # Herodotus 1.93.3: ἦσαν ἐπὶ τοῦ σήματος
    syntax = _syntax(
        [
            (1, "ἦσαν", "εἰμί", "v3piia---", 0, "PRED"),
            (2, "ἐπὶ", "ἐπί", "r--------", 1, "AuxP"),
            (3, "σήματος", "σῆμα", "n-s---ng-", 2, "PNOM"),
        ],
        [("1.93.3", ["ἦσαν", "ἐπὶ", "σήματος"])],
    )
    assert _role(syntax, 3) == "Role: with ἐπί, predicate with ἦσαν"
