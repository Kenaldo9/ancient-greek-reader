"""
A word's role in its sentence, read from the Perseus treebank's dependency
tree (AGDT 2.1 relations): a short English phrase ("object of ἄειδε"), the
word it points to, so the reader can highlight it, and for a verb its
subject, objects and predicate words.

A word's head is often not the word its role is about: a coordinating
conjunction or comma (COORD) joins its members (relations ending in _CO), an
apposition node (APOS) its members (_AP), and a preposition (AuxP) or
subordinating conjunction (AuxC) stands between a word and what it depends
on. The walk passes through them to the content word, and an elliptic node
(an understood word the treebank adds) is named as such. Particles and
punctuation (AuxY, AuxZ, AuxX, AuxK) get no role. GLAUx also puts APOS on a
word without members (a word or clause in apposition to its head).

The same walk gives a sentence's outline (SentenceSyntax.outline), nested
rows with short roles, for the passage box's Structure view.

Nothing here reads the database: the caller gives the sentence's nodes, its
words and their lines (greek/web/reader_data.py, greek/cli/study_cmd.py).
"""

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from greek.display.analysis import _PUNCT_STRIP
from greek.fetch.treebank import bare
from greek.models import Line, TreebankNode, WordParse
from greek.search import citation_form

# Relations with no role row: particles, punctuation and leftovers. GLAUx
# adds MWE (the second part of a word it splits, as -δʼ of οὐδʼ), GAP,
# UNLINKED and PUNCT.
NO_ROLE = {
    "AuxY",
    "AuxZ",
    "AuxX",
    "AuxK",
    "AuxG",
    "AuxV",
    "XSEG",
    "UNDEFINED",
    "MWE",
    "GAP",
    "UNLINKED",
    "PUNCT",
}
# GLAUx relations spelled differently from the Perseus treebank's, or misspelt
_RELATION_FIXES = {"EXD": "ExD", "OVJ": "OBJ"}
# Relations that only join other nodes
_STRUCTURE = {"COORD", "APOS"}
# Relations a word's head can have that stand between it and its content word
_PASS_THROUGH = {"AuxP", "AuxC"}
# Dependents that are not arguments or members
_MINOR = NO_ROLE | {""}
# How many coordinated words a phrase names before "and N more"
MAX_NAMED = 3

_CASES = {"g": "genitive", "d": "dative"}
# How an attribute's phrase begins, by how it relates to its head
_ATTRIBUTE = {
    "relative": "verb of a relative clause on ",
    "agrees": "agrees with ",
    "apposition": "in apposition to ",
    "genitive": "genitive with ",
    "modifies": "modifies ",
}
# Elision marks: the app's ʼ, the treebank's combining comma, and others
_ELISION = "ʼ’'᾽̓"


@dataclass(frozen=True)
class Mention:
    """
    A word a role names: its form as printed in the text (a conjunction's
    lemma), its place (None for a node without a word of the text), its
    line's reference when that is not the line of the word clicked, and
    whether it is the word the role points to (a link that highlights it).
    """

    form: str
    line_number: Optional[int] = None
    token_index: Optional[int] = None
    reference: str = ""
    link: bool = False


Phrase = List[Union[str, Mention]]


@dataclass
class RoleRow:
    """One row of the Syntax block: "Role", "Subject", "Objects", ..."""

    label: str
    phrase: Phrase

    @property
    def text(self) -> str:
        """The phrase as plain text: "object of ἄειδε", "with ἐς (1.6)"."""
        return "".join(
            p
            if isinstance(p, str)
            else p.form + (f" ({p.reference})" if p.reference else "")
            for p in self.phrase
        )


@dataclass
class Role:
    rows: List[RoleRow] = field(default_factory=list)

    @property
    def target(self) -> Optional[Mention]:
        """The word the role points to, if it is a word of the text."""
        for row in self.rows:
            for p in row.phrase:
                if isinstance(p, Mention) and p.link and p.line_number is not None:
                    return p
        return None

    @property
    def text(self) -> str:
        """All rows as "Role: …; Subject: …", for tests and reviews."""
        return "; ".join(f"{r.label}: {r.text}" for r in self.rows)


# (line_number, token_index, form as printed, reference) of a node's word
WordPlace = Tuple[int, int, str, str]


def split_relation(relation: str) -> Tuple[str, Tuple[str, ...]]:
    """
    'OBJ_AP_CO' → ('OBJ', ('AP', 'CO')). GLAUx's bare 'CO' (a coordinated
    member whose own relation it leaves out) → ('', ('CO',)).
    """
    base, *flags = (relation or "").replace("°", "_").split("_")
    if base == "CO":
        return "", ("CO", *flags)
    return _RELATION_FIXES.get(base, base), tuple(flags)


def _pos(node: TreebankNode) -> str:
    return (node.postag or "-")[0]


def _case(node: TreebankNode) -> str:
    tag = node.postag or ""
    return tag[7] if len(tag) > 7 else "-"


def _mood(node: TreebankNode) -> str:
    tag = node.postag or ""
    return tag[4] if len(tag) > 4 else "-"


def _is_finite_verb(node: TreebankNode) -> bool:
    return _pos(node) == "v" and _mood(node) in ("i", "s", "o", "m")


class SentenceSyntax:
    """
    A treebank sentence's tree, for the roles of its words. words maps a
    node_id to the place of its word in the text (nodes without a word, such
    as punctuation and elliptic nodes, are missing).
    """

    def __init__(self, nodes: Sequence[TreebankNode], words: Mapping[int, WordPlace]):
        self.nodes: Dict[int, TreebankNode] = {n.node_id: n for n in nodes}
        self.words = words
        self.children: Dict[int, List[TreebankNode]] = defaultdict(list)
        for n in sorted(nodes, key=lambda n: n.node_id):
            self.children[n.head].append(n)

    # --- Mentions --------------------------------------------------------------

    def _mention(self, node: TreebankNode, line: int, link: bool = False) -> Mention:
        place = self.words.get(node.node_id)
        if place is None:
            return Mention(node.form, link=link)
        line_number, token_index, form, reference = place
        return Mention(
            form,
            line_number,
            token_index,
            reference if line_number != line else "",
            link,
        )

    def _marker(self, node: TreebankNode, line: int) -> Mention:
        """
        A conjunction or preposition as printed, in its citation form (a
        final grave accent made acute: δέ for δὲ, αὐτάρ for αὐτὰρ), or by its
        lemma when elided (δέ for δʼ, ἐπί for ἐπʼ) or part of a word the
        treebank splits.
        """
        m = self._mention(node, line)
        printed = m.form if m.line_number is not None else node.form
        if printed[-1:] in _ELISION or bare(printed) != bare(node.form):
            printed = node.lemma or printed
        else:
            printed = citation_form(printed)
        return Mention(printed, m.line_number, m.token_index, m.reference)

    def _named(
        self, nodes: Sequence[TreebankNode], line: int, link_first=False
    ) -> Phrase:
        """'A', 'A and B', 'A, B and C', 'A, B, C and 2 more'."""
        shown = list(nodes[:MAX_NAMED])
        more = len(nodes) - len(shown)
        phrase: Phrase = []
        for i, n in enumerate(shown):
            if i:
                last = i == len(shown) - 1 and not more
                phrase.append(" and " if last else ", ")
            if n.artificial:
                phrase.append(self._understood(n))
            else:
                phrase.append(self._mention(n, line, link=link_first and i == 0))
        if more:
            phrase.append(f" and {more} more")
        return phrase

    def _understood(self, node: TreebankNode) -> str:
        """
        How an elliptic node reads: "an understood verb" when it is a main
        verb or has a subject, object or predicate word; else "… word".
        """
        relations = {
            split_relation(m.relation)[0]
            for c in self.children[node.node_id]
            for m in self._content(c)
        }
        if split_relation(node.relation)[0] == "PRED" or relations & {
            "SBJ",
            "OBJ",
            "PNOM",
        }:
            return "an understood verb"
        return "an understood word"

    # --- The tree --------------------------------------------------------------

    def members(self, node: TreebankNode, flag: str) -> List[TreebankNode]:
        """
        The members of a COORD (flag "CO") or APOS (flag "AP") node, with a
        coordination nested in a coordination as one member (_joins_in).
        """
        return [
            c
            for c in self.children[node.node_id]
            if flag in split_relation(c.relation)[1]
            or (flag == "CO" and self._joins_in(c, node))
        ]

    def _joins_in(self, node: TreebankNode, outer: TreebankNode) -> bool:
        """
        Whether a coordination under another is one of its members, as when
        καί joins ἀπέθανε to ἔθαψαν καὶ ἐτίμησαν (Herodotus 1.30.5): the
        treebanks give it the bare relation COORD, and its own members have
        the other members' relation. Under a coordination of verbs, a
        coordination of objects they share is no member.
        """
        if node.node_id == outer.node_id or split_relation(node.relation) != (
            "COORD",
            (),
        ):
            return False
        if split_relation(outer.relation)[0] != "COORD":
            return False

        def relations(n):
            return {
                split_relation(c.relation)[0]
                for c in self.children[n.node_id]
                if "CO" in split_relation(c.relation)[1]
            }

        others = relations(outer)
        return not others or bool(relations(node) & others)

    def _content(self, node: TreebankNode, seen=frozenset()) -> List[TreebankNode]:
        """
        A node as the words it stands for: a COORD node its members, an APOS
        node its first member, otherwise itself.
        """
        base, _ = split_relation(node.relation)
        seen = seen | {node.node_id}
        if base == "COORD":
            members = [m for m in self.members(node, "CO") if m.node_id not in seen]
            return [m for n in members for m in self._content(n, seen)] or [node]
        if base == "APOS":
            members = [m for m in self.members(node, "AP") if m.node_id not in seen]
            return self._content(members[0], seen) if members else [node]
        return [node]

    def _group(self, node: TreebankNode):
        """
        The top of the coordination and apposition a node is a member of, and
        the COORD and APOS nodes on the way: (top, coord, apos).
        """
        _, flags = split_relation(node.relation)
        top, coord, apos = node, None, None
        seen = {node.node_id}
        while True:
            head = self.nodes.get(top.head)
            if head is None or head.node_id in seen:  # the treebank has cycles
                break
            seen.add(head.node_id)
            head_base, _ = split_relation(head.relation)
            if head_base == "COORD" and coord is None and "CO" in flags:
                coord = head
            elif head_base == "APOS" and apos is None and "AP" in flags:
                apos = head
            elif not self._joins_in(top, head):
                break  # a nested coordination belongs to the outer one
            top = head
        return top, coord, apos

    def preposition(self, node_id: int) -> Optional[TreebankNode]:
        """
        The preposition a word's phrase depends on (ἐκ in ἐκ Διὸς καὶ
        Ἥρης, for both nouns), past coordination and apposition; None when
        the nearest word between it and its governor isn't one.
        """
        node = self.nodes.get(node_id)
        if node is None:
            return None
        top, _, _ = self._group(node)
        _, via = self._governor(top)
        if via and split_relation(via[0].relation)[0] == "AuxP":
            return via[0]
        return None

    def _governor(self, top: TreebankNode):
        """
        The node a group's relation is to, past prepositions and
        subordinating conjunctions (returned too, nearest first); None at
        the root.
        """
        via = []
        governor = self.nodes.get(top.head)
        if governor is top:  # its own head, a slip in the treebank
            return None, via
        while governor is not None:
            if split_relation(governor.relation)[0] not in _PASS_THROUGH:
                break
            via.append(governor)
            governor = self.nodes.get(governor.head)
            if governor is not None and (governor in via or governor is top):
                return None, via  # a cycle in the treebank
        return governor, via

    # --- Roles -----------------------------------------------------------------

    def role(self, node_id: int) -> Optional[Role]:
        """The role of a node's word, or None (particles, punctuation, unknown)."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        base, _ = split_relation(node.relation)
        if base in NO_ROLE or not base:
            return None
        line = self._line(node)
        if base == "COORD":
            members = [m for c in self.members(node, "CO") for m in self._content(c)]
            if not members:
                return None
            phrase = ["joins ", *self._named(members, line, link_first=True)]
            if len(members) == 1:
                phrase.append(" to what comes before")
        elif base == "APOS" and self.members(node, "AP"):
            return None
        elif base == "AuxP":
            phrase = ["preposition, governing ", *self._dependents(node, line)]
        elif base == "AuxC":
            phrase = ["introduces the clause of ", *self._dependents(node, line)]
        else:
            phrase = self._relation_phrase(node, base, line)
        if not phrase:
            return None
        role = Role([RoleRow("Role", phrase)])
        if _pos(node) == "v":
            role.rows += self._arguments(node, line)
        return role

    def _line(self, node: TreebankNode) -> int:
        place = self.words.get(node.node_id)
        return place[0] if place else -1

    def _dependents(self, node: TreebankNode, line: int) -> Phrase:
        deps = [
            c
            for c in self.children[node.node_id]
            if split_relation(c.relation)[0] not in _MINOR
        ]
        content = [m for d in deps for m in self._content(d)]
        return self._named(content, line, link_first=True) if content else []

    def _targets(self, node: TreebankNode):
        """
        What a word's relation is to: (targets, via, coord, apos), the words
        it is about past coordination, apposition and prepositions (none at
        the root); None for a word under a particle or punctuation, a
        treebank slip.
        """
        top, coord, apos = self._group(node)
        governor, via = self._governor(top)
        if governor is not None and split_relation(governor.relation)[0] in NO_ROLE:
            return None
        if governor is None:
            targets: List[TreebankNode] = []
        elif split_relation(governor.relation)[0] in _STRUCTURE:
            targets = [t for t in self._content(governor) if t.node_id != node.node_id]
        else:
            targets = [governor]
        return targets, via, coord, apos

    def agrees(self, node_id: int) -> bool:
        """
        Whether a word's role is to agree with a noun or to stand in
        apposition to it ("agrees with μῆνιν", "in apposition to
        Ἀγαμέμνονι"): it shares that noun's case and use.
        """
        node = self.nodes.get(node_id)
        if node is None or split_relation(node.relation)[0] != "ATR":
            return False
        found = self._targets(node)
        if not found or not found[0]:
            return False
        targets = found[0]
        return self._attribute_kind(node, targets[0]) in ("agrees", "apposition")

    def _relation_phrase(self, node: TreebankNode, base: str, line: int) -> Phrase:
        found = self._targets(node)
        if found is None:
            return []
        targets, via, coord, apos = found
        target: Phrase = []
        if targets:
            target = self._named(targets, line, link_first=True)

        relation = self._base_phrase(node, base, targets, target, bool(via))
        phrase: Phrase = []
        if via:
            marker = self._marker(via[0], line)
            if split_relation(via[0].relation)[0] == "AuxC" and _pos(node) == "v":
                # the verb of a subordinate clause: "verb of the clause with
                # ὅτε, adverbial to ἵκανεν"
                phrase += ["verb of the clause with ", marker]
                if relation and base != "PRED":
                    phrase += [", ", *relation]
            else:
                phrase += ["with ", marker, ", ", *relation]
        else:
            phrase += relation

        if coord is not None:
            others = [
                c
                for m in self.members(coord, "CO")
                for c in self._content(m)
                if c.node_id != node.node_id
            ]
            if others:
                # a conjunction is named; a comma or an elliptic node isn't
                if not coord.artificial and _pos(coord) not in ("u", "-"):
                    phrase += [", joined by ", self._marker(coord, line), " with "]
                else:
                    phrase += [", joined with "]
                phrase += self._named(others, line, not targets)
        if apos is not None:
            others = [m for m in self.members(apos, "AP") if m.node_id != node.node_id]
            if others and others[0].node_id < node.node_id:
                phrase += [", in apposition to ", *self._named(others[:1], line)]
        return phrase

    def _base_phrase(
        self,
        node: TreebankNode,
        base: str,
        targets: Sequence[TreebankNode],
        target: Phrase,
        via: bool,
    ) -> Phrase:
        """The phrase for a relation to its target ("object of ἄειδε")."""
        pos = _pos(node)
        to = " to " if via else ", with "
        if base == "PRED":
            return ["main verb"] if not target else ["main verb, with ", *target]
        if base == "ExD":
            return [
                "addressed" if _case(node) == "v" else "stands apart from the sentence"
            ]
        if not target:
            return []
        if base == "SBJ":
            return ["subject of ", *target]
        if base == "OBJ":
            if via:  # a prepositional phrase the verb takes
                return ["complement of ", *target]
            case = _CASES.get(_case(node)) if pos in "nap" else None
            return [f"{case} object of " if case else "object of ", *target]
        if base == "PNOM":  # a prepositional phrase is neither noun nor adjective
            kind = (
                None
                if via
                else {"n": "noun", "a": "adjective", "v": "adjective"}.get(pos)
            )
            return [f"predicate {kind} with " if kind else "predicate with ", *target]
        if base in ("ATV", "AtvV"):
            return ["predicative", to, *target]
        if base == "OCOMP":
            return ["object complement of ", *target]
        if base == "ADV":
            return ["adverbial", to, *target]
        if base == "APOS":  # GLAUx: a word in apposition, without members
            return ["in apposition to ", *target]
        if base == "ATR":  # coordinated heads: compared with the first
            return self._attribute(node, targets[0], target)
        return ["depends on ", *target]

    def _attribute_kind(self, node: TreebankNode, head: Optional[TreebankNode]) -> str:
        """How an attribute (ATR) relates to its head: one of _ATTRIBUTE's keys."""
        pos, case = _pos(node), _case(node)
        head_case = _case(head) if head is not None else "-"
        head_nominal = head is not None and _pos(head) in "napml"
        if pos == "v" and _is_finite_verb(node) and head_nominal:
            return "relative"
        if pos in "aplmv" and case != "-" and (case == head_case or head_case == "-"):
            return "agrees"
        if pos == "n" and case not in "-g" and case == head_case and head_nominal:
            return "apposition"
        if case == "g":
            return "genitive"
        return "modifies"

    def _attribute(
        self, node: TreebankNode, head: Optional[TreebankNode], target: Phrase
    ) -> Phrase:
        return [_ATTRIBUTE[self._attribute_kind(node, head)], *target]

    def _arguments(self, verb: TreebankNode, line: int) -> List[RoleRow]:
        """A verb's subject, objects, predicate words and the person addressed."""
        dependents = list(self.children[verb.node_id])
        _, coord, _ = self._group(verb)
        if coord is not None:  # dependents shared by coordinated verbs
            members = {m.node_id for m in self.members(coord, "CO")}
            dependents += [
                c for c in self.children[coord.node_id] if c.node_id not in members
            ]
        found: Dict[str, List[TreebankNode]] = defaultdict(list)
        for d in sorted(dependents, key=lambda n: n.node_id):
            base, _ = split_relation(d.relation)
            if base in _STRUCTURE:
                groups = [(split_relation(m.relation)[0], m) for m in self._content(d)]
            else:
                groups = [(base, d)]
            for rel, n in groups:
                if n.artificial or n.node_id not in self.words:
                    continue  # an understood word, or a reading the text lacks
                if rel == "ExD" and _case(n) != "v":
                    continue
                if rel in ("SBJ", "OBJ", "PNOM", "ExD"):
                    found[rel].append(n)
        rows = []
        for rel, one, many in (
            ("SBJ", "Subject", "Subjects"),
            ("OBJ", "Object", "Objects"),
            ("PNOM", "Predicate", "Predicate"),
            ("ExD", "Addressed", "Addressed"),
        ):
            nodes = found.get(rel)
            if nodes:
                label = one if len(nodes) == 1 else many
                rows.append(RoleRow(label, self._named_list(nodes, line)))
        return rows

    def _named_list(self, nodes: Sequence[TreebankNode], line: int) -> Phrase:
        """'A, B, C' (up to MAX_NAMED, then 'and N more')."""
        phrase: Phrase = []
        for i, n in enumerate(nodes[:MAX_NAMED]):
            if i:
                phrase.append(", ")
            phrase.append(self._mention(n, line))
        if len(nodes) > MAX_NAMED:
            phrase.append(f" and {len(nodes) - MAX_NAMED} more")
        return phrase

    def outline(self) -> List["OutlineRow"]:
        """The sentence as nested rows, for the passage box's Structure view."""
        return _Outliner(self).rows()


# --- Outlines --------------------------------------------------------------------

# The order of a row's dependents, by relation: subject, objects, predicate
# words, the person addressed, adverbials, then attributes
_OUTLINE_RANK = {
    "SBJ": 0,
    "OBJ": 1,
    "PNOM": 2,
    "OCOMP": 3,
    "ATV": 4,
    "AtvV": 4,
    "ExD": 5,
    "ADV": 6,
    "ATR": 7,
}
# An outline row's short role, by relation (the word pane's phrase without
# the word it is about, which is the row above)
_SHORT_ROLES = {
    "PRED": "main verb",
    "SBJ": "subject",
    "PNOM": "predicate",
    "ATV": "predicative",
    "AtvV": "predicative",
    "OCOMP": "object complement",
    "ADV": "adverbial",
    "APOS": "in apposition",
}
_SHORT_ATTRIBUTES = {
    "relative": "relative clause",
    "agrees": "agrees",
    "apposition": "in apposition",
    "genitive": "genitive",
    "modifies": "modifies",
}
# How a dependent shared by coordinated words names them: "subject of ἔθηκε,
# προΐαψεν and τεῦχε", "agrees with …"; None names none ("addressed")
_SHARED_LINKS = {
    "agrees": " with ",
    "genitive": " with ",
    "predicate": " with ",
    "in apposition": " to ",
    "adverbial": " to ",
    "predicative": " to ",
    "modifies": " ",
    "relative clause": " on ",
    "depends": " on ",
    "addressed": None,
    "apart": None,
    "main verb": None,
    "clause": None,
    "preposition": None,
}
# A row folds when this many rows or more hang under it
FOLD_ROWS = 3


def _shared_role(role: str, named: str) -> str:
    """ "subject" → "subject of ἔθηκε and τεῦχε" (before any ", joined by …")."""
    base, joined, rest = role.partition(", joined")
    link = _SHARED_LINKS.get(base.rsplit(", ", 1)[-1], " of ")
    return role if link is None else base + link + named + joined + rest


@dataclass
class OutlineWord:
    """A word on an outline row as printed, and its place (None for an understood word)."""

    form: str
    line_number: Optional[int] = None
    token_index: Optional[int] = None
    understood: bool = False

    @property
    def place(self) -> Optional[Tuple[int, int]]:
        if self.line_number is None or self.token_index is None:
            return None
        return self.line_number, self.token_index


@dataclass
class OutlineRow:
    """
    One row of a sentence's outline: a word, a preposition with its object
    ("ἐξ οὗ") or a subordinating conjunction with its verb ("ὅτε … ἵκανεν"),
    its short role ("object", "relative clause, joined by δέ"), and the rows
    of the words that depend on it. key numbers the sentence's rows in order.
    The caller fills in gloss (lsj when it is a dictionary's), and use.
    """

    words: List[OutlineWord]
    role: str
    depth: int
    key: int = 0
    joiner: str = " "
    clause: bool = False  # a main verb, or the verb of a relative or other clause
    children: List["OutlineRow"] = field(default_factory=list)
    gloss: str = ""
    lsj: bool = False
    use: str = ""

    @property
    def greek(self) -> str:
        return self.joiner.join(w.form for w in self.words)

    @property
    def word(self) -> Optional[OutlineWord]:
        """The word the row stands for: a preposition's object, a conjunction's verb."""
        placed = [w for w in self.words if w.place]
        return placed[-1] if placed else None

    @property
    def places(self) -> List[Tuple[int, int]]:
        return [w.place for w in self.words if w.place]

    def descendants(self) -> List["OutlineRow"]:
        return [d for c in self.children for d in (c, *c.descendants())]

    @property
    def foldable(self) -> bool:
        """Below the top, a row with FOLD_ROWS rows or more under it can fold."""
        return self.depth > 0 and len(self.descendants()) >= FOLD_ROWS

    @property
    def hidden_words(self) -> int:
        """How many words a folded row hides."""
        return sum(len(d.places) for d in self.descendants())


class _Outliner:
    """
    The walk behind SentenceSyntax.outline(), the same as the roles': a
    coordination's members sit side by side, after the dependents they share
    (whose role names every member); a preposition is folded into its
    object's row and a subordinating conjunction into its verb's; an
    elliptic node is an understood word; particles and punctuation have no
    row, and words a slip in the treebank put under one move up to its head.
    """

    def __init__(self, syntax: SentenceSyntax):
        self.syn = syntax
        self.seen: set = set()  # the treebank has cycles
        self.count = 0

    def rows(self) -> List[OutlineRow]:
        found: List[OutlineRow] = []
        for root in self.order(self.content(0)):
            found += self.walk(root, 0)
        return found

    # --- the tree --------------------------------------------------------------

    def content(self, node_id: int) -> List[TreebankNode]:
        """A node's dependents that get rows, past particles and punctuation."""
        found = []
        for c in self.syn.children[node_id]:
            if c.node_id == node_id or c.node_id in self.seen:
                continue
            base, flags = split_relation(c.relation)
            if base in NO_ROLE or not (base or flags):
                self.seen.add(c.node_id)
                found += self.content(c.node_id)
            else:
                found.append(c)
        return found

    def structure(self, node: TreebankNode) -> List[TreebankNode]:
        """The members of a coordination or apposition node (none for a lone word)."""
        flag = {"COORD": "CO", "APOS": "AP"}.get(split_relation(node.relation)[0])
        if not flag:
            return []
        return [
            m
            for m in self.syn.members(node, flag)
            if m.node_id != node.node_id
            and split_relation(m.relation)[0] not in NO_ROLE
        ]

    def effective(self, node: TreebankNode, depth: int = 0) -> str:
        """The relation a dependent's row shows (a preposition's object's)."""
        base, _ = split_relation(node.relation)
        if depth > 20:
            return base
        members = self.structure(node)
        if members:
            return self.effective(members[0], depth + 1)
        if base in _PASS_THROUGH:
            inner = [c for c in self.syn.children[node.node_id] if c is not node]
            inner = [c for c in inner if split_relation(c.relation)[0] not in NO_ROLE]
            return self.effective(inner[0], depth + 1) if inner else base
        return base

    def order(self, nodes: Iterable[TreebankNode]) -> List[TreebankNode]:
        return sorted(
            nodes, key=lambda n: (_OUTLINE_RANK.get(self.effective(n), 8), n.node_id)
        )

    # --- rows ------------------------------------------------------------------

    def word(self, node: TreebankNode) -> OutlineWord:
        if node.artificial:
            return OutlineWord(self.understood(node), understood=True)
        place = self.syn.words.get(node.node_id)
        if place is None:
            return OutlineWord(node.form)
        line_number, token_index, form, _ = place
        return OutlineWord(form, line_number, token_index)

    def understood(self, node: TreebankNode) -> str:
        return self.syn._understood(node).removeprefix("an ")

    def role(self, node: TreebankNode, head: Optional[TreebankNode], via_prep=False):
        base, _ = split_relation(node.relation)
        if base == "OBJ":
            if via_prep:
                return "complement"
            case = _CASES.get(_case(node)) if _pos(node) in "nap" else None
            return f"{case} object" if case else "object"
        if base == "ExD":
            return "addressed" if _case(node) == "v" else "apart"
        if base == "ATR":
            return _SHORT_ATTRIBUTES[self.syn._attribute_kind(node, head)]
        return _SHORT_ROLES.get(base, "depends")

    def row(self, words, role, depth, clause=False, joiner=" ") -> OutlineRow:
        row = OutlineRow(words, role, depth, self.count, joiner, clause)
        self.count += 1
        return row

    def walk(
        self,
        node: TreebankNode,
        depth: int,
        head: Optional[TreebankNode] = None,
        suffix: str = "",
    ) -> List[OutlineRow]:
        """The rows of a node: usually one, with its dependents under it."""
        if node.node_id in self.seen:
            return []
        self.seen.add(node.node_id)
        base, _ = split_relation(node.relation)
        members = self.structure(node)
        if base == "COORD":
            if members:
                return self.coordination(node, members, depth, head, suffix)
            # a conjunction without members: its dependents stand in its place
            rows: List[OutlineRow] = []
            for c in self.order(self.content(node.node_id)):
                rows += self.walk(c, depth, head, suffix)
            return rows
        if base == "APOS" and members:
            return self.apposition(node, members, depth, head, suffix)
        inner = self.content(node.node_id)
        single = [n for n in inner if not self.structure(n)]
        if base in _PASS_THROUGH and len(inner) == 1 and single:
            # "ἐξ οὗ": the preposition on its object's row; "ὅτε … ἵκανεν"
            folded = single[0]
            self.seen.add(folded.node_id)
            words = [self.word(node), self.word(folded)]
            if base == "AuxP":
                row = self.row(words, self.role(folded, head, via_prep=True), depth)
            else:
                role = "clause, " + self.role(folded, head)
                row = self.row(words, role, depth, clause=True, joiner=" … ")
            row.role += suffix
            for c in self.order(self.content(folded.node_id)):
                row.children += self.walk(c, depth + 1, folded)
            return [row]
        if base == "AuxP":
            role, clause = "preposition", False
        elif base == "AuxC":
            role, clause = "clause", True
        else:
            role = self.role(node, head)
            clause = base == "PRED" or role == "relative clause"
        row = self.row([self.word(node)], role + suffix, depth, clause)
        under = head if base in _PASS_THROUGH else node
        for c in self.order(inner):
            row.children += self.walk(c, depth + 1, under)
        return [row]

    def apposition(self, node, members, depth, head, suffix):
        """The first member's row; the other members and the rest under it."""
        rows = self.walk(members[0], depth, head, suffix)
        others = [
            c
            for c in self.content(node.node_id)
            if "AP" not in split_relation(c.relation)[1]
        ]
        if not rows:  # the first member was already shown
            for m in members[1:]:
                rows += self.walk(m, depth, head, suffix)
            return rows
        for m in members[1:]:
            rows[0].children += self.walk(
                m, depth + 1, members[0], suffix=", in apposition"
            )
        for c in self.order(others):
            rows[0].children += self.walk(c, depth + 1, members[0])
        return rows

    def coordination(self, node, members, depth, head, suffix="") -> List[OutlineRow]:
        """
        Coordinated words side by side, after the dependents they share:
        "subject of ἔθηκε, προΐαψεν and τεῦχε", then "ἔθηκε", "προΐαψεν,
        joined by δέ", "τεῦχε, joined by δέ". A nested coordination's first
        member takes the suffix its coordination had.
        """
        joined = {m.node_id for m in members}
        common = [c for c in self.content(node.node_id) if c.node_id not in joined]
        names = []
        for m in members:
            lead = self.word(self.lead(m))
            names.append(f"[{lead.form}]" if lead.understood else lead.form)
        named = names[0]
        if len(names) > 1:
            named = ", ".join(names[:-1]) + " and " + names[-1]
        rows: List[OutlineRow] = []
        for c in self.order(common):
            for row in self.walk(c, depth, members[0]):
                row.role = _shared_role(row.role, named)
                rows.append(row)
        marker = ""
        if not node.artificial and _pos(node) not in ("u", "-"):
            marker = self.syn._marker(node, -1).form
        for k, m in enumerate(members):
            if k:
                suffix = f", joined by {marker}" if marker else ", joined"
            rows += self.walk(m, depth, head, suffix)
        return rows

    def lead(self, node: TreebankNode) -> TreebankNode:
        """The word a member is named by (a nested coordination's first)."""
        seen = set()
        while self.structure(node) and node.node_id not in seen:
            seen.add(node.node_id)
            node = self.structure(node)[0]
        return node


# --- Building trees --------------------------------------------------------------


def build_syntax(
    nodes: Sequence[TreebankNode],
    parses: Iterable[WordParse],
    lines: Mapping[int, Line],
) -> SentenceSyntax:
    """
    A sentence's tree, with where each node's word is in the text: parses
    are the sentence's words linked to its nodes, lines the text's lines by
    number. A node the treebank left untagged takes a model's parse of its
    word.
    """
    linked = [p for p in parses if p.node_id is not None]
    model_tags = {p.node_id: p.postag for p in linked if p.source == "model"}
    nodes = [
        replace(n, postag=model_tags[n.node_id])
        if not n.postag and model_tags.get(n.node_id)
        else n
        for n in nodes
    ]
    words: Dict[int, WordPlace] = {}
    for p in linked:
        line = lines.get(p.line_number)
        tokens = line.content.split() if line else []
        if p.token_index < len(tokens):
            token = tokens[p.token_index]
            words[p.node_id] = (
                p.line_number,
                p.token_index,
                token.strip(_PUNCT_STRIP) or token,
                line.reference or str(line.line_number),
            )
    return SentenceSyntax(nodes, words)


def text_syntax(
    nodes: Iterable[TreebankNode],
    parses: Iterable[WordParse],
    lines: Mapping[int, Line],
) -> Dict[int, SentenceSyntax]:
    """The trees of several sentences of a text, by sentence_id (build_syntax)."""
    by_sentence: Dict[int, List[TreebankNode]] = defaultdict(list)
    for n in nodes:
        by_sentence[n.sentence_id].append(n)
    words: Dict[int, List[WordParse]] = defaultdict(list)
    for p in parses:
        if p.sentence_id is not None:
            words[p.sentence_id].append(p)
    return {
        sid: build_syntax(sentence, words[sid], lines)
        for sid, sentence in by_sentence.items()
    }
