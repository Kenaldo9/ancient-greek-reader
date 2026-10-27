import json
import os
import re
import urllib.error
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional

import click
from rich.console import Console
from rich.table import Table

from greek.api.forms import (
    DEFAULT_OUTPUT_PER_LEMMA,
    FORMS_SHOWN,
    LEMMAS_PER_REQUEST,
    FormsLemma,
    build_forms_request,
    estimate_forms,
    forms_custom_id,
    group_lemmas,
    headword_articles,
    lsj_article,
    lsj_start,
    read_forms_result,
)
from greek.api.parts import (
    DEFAULT_OUTPUT_PER_VERB,
    VERBS_PER_REQUEST,
    build_parts_request,
    estimate_parts,
    group_verbs,
    parts_custom_id,
    read_parts_result,
)
from greek.api.study import (
    DEFAULT_NOTES_OUTPUT_PER_WORD,
    DEFAULT_OUTPUT_PER_WORD,
    EFFORT,
    EFFORTS,
    MAX_TOKENS,
    MODEL,
    NOTE_VERSION,
    batch_client,
    build_notes_request,
    build_request,
    custom_id,
    estimate,
    first_in_book,
    group_units,
    parse_custom_id,
    parse_label,
    read_notes_result,
    read_result,
    study_words,
)
from greek.api.uses import (
    KINDS,
    build_uses_request,
    default_output_per_label,
    read_uses_result,
    use_words,
)
from greek.api.works import DIALECTS, WorkProfile, work_profile
from greek.cli.fetch_cmd import _describe_network_error
from greek.display.analysis import _PUNCT_STRIP
from greek.db.connection import get_connection
from greek.db.repos.lexicon import get_lemma_entries, lemma_counts, save_lexicon
from greek.db.repos.lines import get_lines, resolve_line_range
from greek.db.repos.parses import (
    get_tb_source,
    lemma_forms_seen,
    lemmas_with_pos,
    list_tb_nodes,
    list_tb_sentences,
    list_word_parses,
    save_tb_nodes,
    save_tb_source,
    save_treebank,
    verb_lemmas,
)
from greek.db.repos.study import (
    covered_units,
    fail_lemma_request,
    fail_parts_request,
    fail_study_request,
    finish_collect,
    get_study_batch,
    lemmas_with_forms,
    list_lemma_requests,
    list_parts_requests,
    list_study_batches,
    list_study_requests,
    list_line_translations,
    list_translation_units,
    labelled_words,
    list_word_study,
    measured_output_per_lemma,
    measured_output_per_verb,
    measured_output_per_word,
    pending_forms_lemmas,
    pending_parts_verbs,
    pending_ranges,
    save_forms_result,
    save_notes_result,
    save_parts_result,
    save_study_result,
    save_submitted_batch,
    save_submitted_forms_batch,
    save_submitted_parts_batch,
    save_uses_result,
    set_batch_status,
    succeed_study_request,
    units_with_notes,
)
from greek.db.repos.texts import get_text
from greek.db.repos.verbs import verbs_with_parts
from greek.fetch.lsj import (
    LSJ_BASE,
    LSJ_FILES,
    LsjIndex,
    download_lsj,
    read_entries,
    read_heads,
    resolve_lemma,
)
from greek.fetch.treebank import (
    MIN_ALIGNED_SHARE,
    WORKS,
    align_text,
    download_treebank,
    read_treebank,
)
from greek.search import plain
from greek.models import (
    LemmaForms,
    LemmaRequest,
    Line,
    LineTranslation,
    PartsRequest,
    StudyBatch,
    StudyRequest,
    TextMeta,
    TranslationUnit,
    TreebankSource,
    Usage,
    WordParse,
    WordStudy,
    WordUse,
)
from greek.syntax import SentenceSyntax, text_syntax

console = Console()


def _data_dir() -> Path:
    return Path(os.environ.get("GREEK_DATA_DIR", "./data"))


def _sources_dir() -> Path:
    return _data_dir() / "sources"


def _by_line(words):
    """(reference, word) pairs grouped as "ref: word word" lines."""
    grouped = defaultdict(list)
    for ref, word in words:
        grouped[ref].append(word)
    return [f"{ref}: {' '.join(ws)}" for ref, ws in grouped.items()]


@click.group("study")
def study_cmd():
    """Saved study data: treebank grammar and LSJ definitions for every word."""


@study_cmd.command("import-treebank")
@click.argument("text_id", type=int)
@click.option(
    "--work",
    type=click.Choice(sorted(WORKS)),
    default="iliad",
    show_default=True,
    help="Which treebank to align with the text",
)
@click.option(
    "--file",
    "file_",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Use a local treebank file instead of the download",
)
@click.option("--refresh", is_flag=True, help="Download again even if a copy is saved")
@click.option(
    "--list-missing",
    is_flag=True,
    help="List every treebank word without a lemma or parse",
)
@click.option(
    "--nodes-only",
    is_flag=True,
    help="Only save the treebank's nodes and link the saved words to them",
)
def import_treebank_cmd(text_id, work, file_, refresh, list_missing, nodes_only):
    """Align a treebank with a text and save each word's parse.

    \b
    The Iliad and the Odyssey use the Perseus treebank (AGDT 2.1, CC BY-SA
    3.0); Herodotus and Thucydides use GLAUx (CC BY-SA 4.0; its hand
    annotation of them is CC BY-NC-SA), where some sentences were annotated
    by hand and the rest by GLAUx's parser. The file is downloaded once to
    data/sources/. Rerunning replaces the saved treebank data; parses a
    model supplied for words the treebank lacks are kept. Every node of the
    treebank is saved too (punctuation and elliptic nodes included), with
    each word linked to its node, for the word's role in its sentence.

    \b
    --nodes-only saves the nodes and links for a text imported before nodes
    were kept: it aligns again, checks that every word's form and treebank
    lemma match the saved ones, and changes nothing else. It also records
    which treebank the text uses.

    \b
    Example:
      greek study import-treebank 12 --work iliad
      greek study import-treebank 12 --nodes-only
      greek study import-treebank 16 --work herodotus
    """
    conn = get_connection()
    text = get_text(conn, text_id)
    if text is None:
        console.print(f"[red]No text with ID {text_id}.[/red]")
        raise SystemExit(1)
    lines = get_lines(conn, text_id)
    if not lines or any(line.reference is None for line in lines):
        console.print(
            f"[red]'{text.title}' has lines without references; "
            "the treebank is aligned book by book.[/red]"
        )
        raise SystemExit(1)

    if file_ is None:
        url = WORKS[work].url
        console.print(f"[dim]Treebank: {url}[/dim]")
        try:
            file_ = download_treebank(work, _sources_dir(), refresh=refresh)
        except urllib.error.URLError as e:
            console.print(f"[red]Error: {_describe_network_error(e, url)}[/red]")
            raise SystemExit(1)
    try:
        treebank = read_treebank(file_)
    except ET.ParseError as e:
        console.print(f"[red]Error: could not parse {file_}: {e}[/red]")
        raise SystemExit(1)

    result = align_text(text_id, lines, treebank.words, treebank.auto)
    nodes = [replace(n, text_id=text_id) for n in treebank.nodes]
    source = TreebankSource(
        text_id, work, WORKS[work].corpus, file_.name, WORKS[work].licence
    )
    if nodes_only:
        _link_nodes(conn, text, result, nodes, work)
        save_tb_source(conn, source)
        return
    table = Table(title=f"Treebank alignment: {text.title} (ID {text_id})")
    table.add_column("Words of the text", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Share", justify="right")
    for label, count in [
        ("Identical form", result.identical),
        ("Split in the treebank (e.g. οὐδʼ = οὐ + δ̓)", result.split),
        ("No treebank word", len(result.unmatched)),
        ("All", result.words),
    ]:
        share = f"{100 * count / result.words:.2f}%" if result.words else "–"
        table.add_row(label, f"{count:,}", share)
    console.print(table)

    if result.aligned_share < MIN_ALIGNED_SHARE:
        console.print(
            f"[red]Only {100 * result.aligned_share:.1f}% of the words align; "
            f"is this the {work.title()} treebank for '{text.title}'? "
            "Nothing was saved.[/red]"
        )
        raise SystemExit(1)

    save_treebank(conn, text_id, result.sentences, result.parses, nodes)
    save_tb_source(conn, source)
    if treebank.auto:
        _annotation_report(lines, result)
    console.print(
        f"Treebank words without lemma or parse: {len(result.untagged):,}; "
        f"treebank words not in the text: {result.unused:,}"
    )
    if result.unmatched:
        console.print("[bold]Words without a treebank word:[/bold]")
        for row in _by_line(result.unmatched):
            console.print(f"  {row}")
    if list_missing and result.untagged:
        console.print("[bold]Treebank words without lemma or parse:[/bold]")
        for row in _by_line(result.untagged):
            console.print(f"  {row}")
    missing = len(result.unmatched) + len(result.untagged)
    console.print(
        f"[green]Saved[/green] {len(result.parses):,} word parses, "
        f"{len(result.sentences):,} sentences and {len(nodes):,} nodes; "
        f"{missing:,} words or parts have no parse yet."
    )


def _annotation_report(lines, result):
    """
    Per book: the text's words, those without a treebank word, and the share
    of words in sentences annotated by hand (a GLAUx treebank).
    """
    book_of = {ln.line_number: (ln.reference or "").split(".")[0] for ln in lines}
    annotation = {s.sentence_id: s.annotation for s in result.sentences}
    words, unmatched, manual = Counter(), Counter(), Counter()
    for p in result.parses:
        if p.part:
            continue
        book = book_of[p.line_number]
        words[book] += 1
        if p.sentence_id is None:
            unmatched[book] += 1
        elif annotation.get(p.sentence_id) == "manual":
            manual[book] += 1
    table = Table(title="By book")
    for column in ("Book", "Words", "No treebank word", "By hand"):
        table.add_column(column, justify="right")
    for book in sorted(words, key=lambda b: (len(b), b)):
        table.add_row(
            book,
            f"{words[book]:,}",
            f"{unmatched[book]:,}",
            f"{100 * manual[book] / words[book]:.0f}%",
        )
    total = sum(words.values())
    table.add_row(
        "All",
        f"{total:,}",
        f"{sum(unmatched.values()):,}",
        f"{100 * sum(manual.values()) / total:.0f}%" if total else "–",
    )
    console.print(table)
    auto = sum(1 for s in result.sentences if s.annotation == "auto")
    console.print(
        f"Sentences annotated by GLAUx's parser: {auto:,} of "
        f"{len(result.sentences):,}; the rest by hand."
    )


def _link_nodes(conn, text: TextMeta, result, nodes, work: str):
    """
    --nodes-only: save a text's treebank nodes and link its saved words to
    them, once the new alignment is checked against the saved rows.
    """
    if result.aligned_share < MIN_ALIGNED_SHARE:
        _fail(
            f"Only {100 * result.aligned_share:.1f}% of the words align; is this "
            f"the {work.title()} treebank for '{text.title}'? Nothing was saved."
        )
    saved = {
        (p.line_number, p.token_index, p.part): p
        for p in list_word_parses(conn, text.id)
    }
    if not saved:
        _fail(
            f"'{text.title}' has no treebank data; run "
            "'greek study import-treebank' without --nodes-only."
        )
    refs = {line.line_number: line.reference for line in get_lines(conn, text.id)}
    differ = []
    for p in result.parses:
        row = saved.pop((p.line_number, p.token_index, p.part), None)
        if (
            row is None
            or row.form != p.form
            or row.sentence_id != p.sentence_id
            or (row.source == "treebank" and row.lemma != p.lemma)
        ):
            found = f"{row.form} ({row.lemma})" if row else "nothing saved"
            differ.append(
                f"{refs[p.line_number]} word {p.token_index + 1}: the treebank has "
                f"{p.form} ({p.lemma}), the database {found}"
            )
    for (line_number, token_index, _), row in saved.items():
        differ.append(
            f"{refs.get(line_number, line_number)} word {token_index + 1}: "
            f"{row.form} is saved but not in the new alignment"
        )
    if differ:
        for row in differ[:20]:
            console.print(f"  {row}", soft_wrap=True)
        _fail(
            f"{len(differ):,} saved words differ from the treebank; nothing was "
            "saved. Import the treebank again without --nodes-only."
        )

    links = [
        (p.line_number, p.token_index, p.part, p.node_id)
        for p in result.parses
        if p.node_id is not None
    ]
    linked = save_tb_nodes(conn, text.id, nodes, links)
    words = {(p.line_number, p.token_index) for p in result.parses}
    with_node = {(p.line_number, p.token_index) for p in result.parses if p.node_id}
    table = Table(title=f"Treebank nodes: {text.title} (ID {text.id})")
    table.add_column("", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Nodes stored", f"{len(nodes):,}")
    table.add_row("  of them elliptic", f"{sum(n.artificial for n in nodes):,}")
    table.add_row("Word rows linked (parts of split words separately)", f"{linked:,}")
    table.add_row("Words linked", f"{len(with_node):,}")
    table.add_row("Words without a node", f"{len(words - with_node):,}")
    console.print(table)
    console.print(
        "[green]Saved[/green] the nodes; lemmas, parses and model parses are unchanged."
    )


# How lemma_entries.matched_by values read in the import report
_MATCH_LABELS = {
    "headword": "Headword (accents may differ)",
    "variant": "Spelling listed in an entry's head",
    "form": "Form listed in an entry (or its v. X)",
    "alias": "Alias",
    "spelling": "Spelling rule (Ionic η for ᾱ, ει for ι, …)",
    "cross-reference": "Cross-reference (= X, v. X)",
}


@study_cmd.command("import-lsj")
@click.option("--refresh", is_flag=True, help="Download LSJ again even if saved")
@click.option(
    "--list-unmatched", is_flag=True, help="List every lemma LSJ lacks, names too"
)
def import_lsj_cmd(refresh, list_unmatched):
    """Find the LSJ entry for every treebank lemma and save its definitions.

    \b
    LSJ (Perseus, CC BY-SA 4.0; 27 files, 271 MB) is downloaded once to
    data/sources/lsj/. Only entries the saved lemmas need are stored, with
    a short definition and the full entry. Rerun after importing another
    treebank; a model's identification of a lemma LSJ lacks is kept.
    """
    conn = get_connection()
    counts = lemma_counts(conn)
    if not counts:
        console.print(
            "[red]No treebank lemmas saved yet; run "
            "'greek study import-treebank' first.[/red]"
        )
        raise SystemExit(1)

    console.print(f"[dim]LSJ: {LSJ_BASE} ({len(LSJ_FILES)} files)[/dim]")
    try:
        paths = download_lsj(_sources_dir(), refresh=refresh)
    except urllib.error.URLError as e:
        console.print(f"[red]Error: {_describe_network_error(e, LSJ_BASE)}[/red]")
        raise SystemExit(1)
    try:
        console.print("[dim]Indexing entries...[/dim]")
        index = LsjIndex(read_heads(paths))
        matches = {lemma: resolve_lemma(lemma, index) for lemma in counts}
        console.print("[dim]Building definitions...[/dim]")
        entries = read_entries(
            paths, {m.head.entry_id for m in matches.values() if m.head}
        )
    except ET.ParseError as e:
        console.print(f"[red]Error: could not parse LSJ: {e}[/red]")
        raise SystemExit(1)

    save_lexicon(
        conn,
        "LSJ",
        list(entries.values()),
        {
            lemma: (entries[m.head.entry_id].key, m.matched_by) if m.head else None
            for lemma, m in matches.items()
        },
    )

    total_words = sum(counts.values())
    table = Table(title=f"LSJ entries for {len(counts):,} treebank lemmas")
    table.add_column("Found by", style="bold")
    table.add_column("Lemmas", justify="right")
    table.add_column("Words", justify="right")
    for how, label in [*_MATCH_LABELS.items(), (None, "Not in LSJ")]:
        lemmas = [lemma for lemma, m in matches.items() if m.matched_by == how]
        words = sum(counts[lemma] for lemma in lemmas)
        table.add_row(label, f"{len(lemmas):,}", f"{words:,}")
    found = [lemma for lemma, m in matches.items() if m.head]
    found_words = sum(counts[lemma] for lemma in found)
    table.add_row(
        "Found in all",
        f"{100 * len(found) / len(counts):.1f}%",
        f"{100 * found_words / total_words:.2f}%",
    )
    console.print(table)

    missing = sorted(
        (lemma for lemma, m in matches.items() if not m.head),
        key=lambda lemma: (-counts[lemma], lemma),
    )
    names = [lemma for lemma in missing if lemma[:1].isupper()]
    others = [lemma for lemma in missing if not lemma[:1].isupper()]
    if others:
        console.print(f"[bold]Not in LSJ ({len(others)}, most frequent first):[/bold]")
        console.print(", ".join(f"{lemma} ({counts[lemma]})" for lemma in others))
    if names:
        if list_unmatched:
            console.print(f"[bold]Names not in LSJ ({len(names)}):[/bold]")
            console.print(", ".join(f"{lemma} ({counts[lemma]})" for lemma in names))
        else:
            console.print(
                f"Names not in LSJ: {len(names):,} (list them with --list-unmatched)"
            )
    console.print(
        f"[green]Saved[/green] {len(entries):,} LSJ entries for {len(found):,} lemmas; "
        f"{len(missing):,} lemmas are left for a short identification by a model."
    )


# --- greek study batch ------------------------------------------------------


@study_cmd.group("batch")
def batch_cmd():
    """Glosses, notes and translations from Claude Opus 5.5, in Message Batches.

    \b
    For a text with treebank data: a gloss and a note for every word, a
    literal translation of every line, and a fluent translation of every
    translation unit (whole sentences, up to 10 lines of verse, or whole
    prose sections within a chapter up to 80 words; one request each). Each
    work has its own prompts (greek/api/works.py); a prose request marks the
    words GLAUx's parser annotated and leaves out editorial marks and
    speakers' labels. Batches cost half the standard price. Only `submit
    --yes` spends money;
    run `estimate` (or `submit` without --yes) and approve the estimate first.
    --effort sets Opus 5.5's thinking depth. `status` and `collect` also
    follow principal-parts batches (greek study parts), notes batches
    (greek study notes), dictionary-forms batches (greek study forms) and
    uses batches (greek study uses).

    \b
    Example:
      greek study batch estimate 12 --books 2-7 --effort high
      greek study batch submit 12 --books 2-7 --effort high --yes
      greek study batch status
      greek study batch collect msgbatch_...
    """


@dataclass
class _Plan:
    """The requests a submit would send, and what was left out."""

    text: TextMeta
    units: List[TranslationUnit]
    requests: List[dict]
    word_counts: List[int]
    work: WorkProfile = field(default_factory=lambda: work_profile(None))
    line_count: int = 0
    unparsed: int = 0  # words without a treebank parse
    skipped_saved: int = 0
    skipped_pending: int = 0
    largest: Optional[TranslationUnit] = None
    largest_words: int = 0
    scope: str = ""
    skipped_unsaved: int = 0  # notes: units without saved study data
    automatic: int = 0  # words parsed by GLAUx's parser, not by hand


def _fail(message: str):
    console.print(f"[red]{message}[/red]")
    raise SystemExit(1)


def _book_number(reference: Optional[str]) -> Optional[int]:
    book = (reference or "").split(".")[0]
    return int(book) if book.isdigit() else None


def _book_range(books: str):
    match = re.match(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$", books)
    if not match:
        _fail(f'Invalid --books "{books}". Use a book or a range, e.g. 1 or 2-7.')
    first = int(match.group(1))
    return first, int(match.group(2) or first)


def _read_retry_file(path: Path, text_id: int) -> List[TranslationUnit]:
    units = []
    for raw in path.read_text().splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        parsed = parse_custom_id(value)
        if parsed is None:
            _fail(f'{path}: "{value}" is not a request id (like 12_1-7).')
        if parsed[0] != text_id:
            _fail(f"{path}: {value} is a request for text {parsed[0]}, not {text_id}.")
        unit = TranslationUnit(text_id, parsed[1], parsed[2])
        if unit not in units:
            units.append(unit)
    if not units:
        _fail(f"{path} lists no requests.")
    return units


def _work(conn, text_id: int) -> WorkProfile:
    """The study profile of a text's treebank work (the Iliad's if none is recorded)."""
    source = get_tb_source(conn, text_id)
    return work_profile(source.work if source else None)


def _words_per_line(lines, parses) -> Dict[int, int]:
    """The study words of each line (editorial marks and speakers' labels left out)."""
    return Counter(w.line_number for w in study_words(lines, parses))


def _parses_by_line(parses: List[WordParse]) -> Dict[int, List[WordParse]]:
    by_line: Dict[int, List[WordParse]] = defaultdict(list)
    for p in parses:
        by_line[p.line_number].append(p)
    return by_line


@dataclass
class _Selection:
    """A text's chosen units, its lines by number, and a label for the batch."""

    text: TextMeta
    by_number: Dict[int, Line]
    units: List[TranslationUnit]
    scope: str
    work: WorkProfile
    automatic: set  # sentence ids parsed by GLAUx's parser, not by hand


def _select_units(conn, text_id, books, lines_args, retry) -> _Selection:
    """
    The units of --books, --lines (one or more ranges) or --retry, or all of
    the text's units.
    """
    if sum(bool(x) for x in (books, lines_args, retry)) > 1:
        _fail("Use only one of --books, --lines and --retry.")
    text = get_text(conn, text_id)
    if text is None:
        _fail(f"No text with ID {text_id}.")
    lines = get_lines(conn, text_id)
    sentences = list_tb_sentences(conn, text_id)
    if not sentences:
        _fail(
            f"'{text.title}' has no treebank data; run "
            "'greek study import-treebank' first."
        )
    if any(line.reference is None for line in lines):
        _fail(f"'{text.title}' has lines without references.")
    by_number = {line.line_number: line for line in lines}
    work = _work(conn, text_id)
    units = group_units(text_id, lines, sentences, **work.unit_size)

    if retry is not None:
        units = _read_retry_file(retry, text_id)
        for u in units:
            if u.first_line not in by_number or u.last_line not in by_number:
                _fail(f"{custom_id(u)}: lines {u.first_line}–{u.last_line} not found.")
    elif books is not None:
        first, last = _book_range(books)
        units = [
            u
            for u in units
            if first <= (_book_number(by_number[u.first_line].reference) or 0) <= last
        ]
        if not units:
            _fail(f"'{text.title}' has no book {books}.")
    spans = []
    if lines_args:
        chosen = []
        for arg in lines_args:
            start, end, error = resolve_line_range(conn, text_id, arg)
            if error:
                _fail(error)
            some = [u for u in units if u.first_line <= end and u.last_line >= start]
            if some:
                spans.append((some[0].first_line, some[-1].last_line))
            chosen += [u for u in some if u not in chosen]
        units = sorted(chosen, key=lambda u: u.first_line)
    if not units:
        _fail("No lines to request.")
    if not spans:
        spans = [(units[0].first_line, units[-1].last_line)]
    scope = f"{text.title} " + ", ".join(
        f"{by_number[a].reference}–{by_number[b].reference}" for a, b in spans
    )
    if retry is not None:
        scope += f" (retry of {len(units)})"
    automatic = {s.sentence_id for s in sentences if s.annotation == "auto"}
    return _Selection(text, by_number, units, scope, work, automatic)


def _plan(
    conn, text_id, books, lines_arg, retry, redo, effort=EFFORT, repeat_words=False
) -> _Plan:
    """Choose the units to request, skip those already done, and build requests."""
    lines_args = (lines_arg,) if lines_arg is not None else ()
    chosen = _select_units(conn, text_id, books, lines_args, retry)
    text, by_number, units, work = (
        chosen.text,
        chosen.by_number,
        chosen.units,
        chosen.work,
    )
    scope = f"{chosen.scope}: effort {effort}"
    if repeat_words:
        scope += ", words repeated"
    plan = _Plan(
        text=text, units=[], requests=[], word_counts=[], scope=scope, work=work
    )
    all_parses = list_word_parses(conn, text_id)
    all_lines = list(by_number.values())
    if not redo:
        word_counts = _words_per_line(all_lines, all_parses)
        done = covered_units(conn, units, word_counts, MODEL)
        waiting = pending_ranges(conn, text_id, MODEL)
        kept = []
        for u in units:
            if (u.first_line, u.last_line) in done:
                plan.skipped_saved += 1
            elif any(u.first_line <= b and u.last_line >= a for a, b in waiting):
                plan.skipped_pending += 1
            else:
                kept.append(u)
        units = kept

    marks = first_in_book(study_words(all_lines, all_parses))
    parses = _parses_by_line(all_parses)
    for u in units:
        unit_lines = [by_number[n] for n in range(u.first_line, u.last_line + 1)]
        words = study_words(
            unit_lines,
            [p for line in unit_lines for p in parses[line.line_number]],
            marks,
            chosen.automatic,
        )
        plan.units.append(u)
        plan.requests.append(
            build_request(work, u, unit_lines, words, effort, repeat_words)
        )
        plan.word_counts.append(len(words))
        plan.line_count += len(unit_lines)
        plan.unparsed += sum(w.needs_parse for w in words)
        plan.automatic += sum(w.automatic for w in words)
        if len(words) > plan.largest_words:
            plan.largest, plan.largest_words = u, len(words)
    return plan


def _output_per_word(
    conn, override: Optional[float], kind: str = "study", effort: Optional[str] = None
):
    """Output tokens per word for estimates, and where the number comes from."""
    if override is not None:
        return override, "given with --output-per-word"
    measured = measured_output_per_word(conn, MODEL, kind, effort)
    if measured is not None:
        return measured, "measured over collected batches"
    if kind == "notes":
        return DEFAULT_NOTES_OUTPUT_PER_WORD, "the plan's estimate"
    return DEFAULT_OUTPUT_PER_WORD, "the plan's estimate"


def _print_unsaved(plan: _Plan):
    if plan.skipped_unsaved:
        console.print(
            f"[yellow]Left out: {plan.skipped_unsaved:,} units without saved study "
            "data[/yellow] (a gloss for every word and the unit's fluent "
            "translation); request them with 'greek study batch submit' first."
        )


def _print_estimate(
    plan: _Plan, per_word: float, basis: str, kind: str = "study", effort=EFFORT
):
    est = estimate(plan.requests, plan.line_count, sum(plan.word_counts), per_word)
    title = "Notes batch" if kind == "notes" else "Study batch"
    table = Table(title=f"{title}: {plan.scope} (ID {plan.text.id})")
    table.add_column("", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Batch cost", justify="right")
    table.add_row("Requests (translation units)", f"{est.requests:,}", "")
    table.add_row(f"{plan.work.unit_name.title()}s", f"{est.lines:,}", "")
    table.add_row("Words", f"{est.words:,}", "")
    table.add_row("Words without a treebank parse", f"{plan.unparsed:,}", "")
    if plan.automatic:
        table.add_row("Words parsed automatically", f"{plan.automatic:,}", "")
    table.add_row(
        "Input tokens (estimate)", f"{est.input_tokens:,}", f"${est.input_cost:,.2f}"
    )
    table.add_row(
        f"Output tokens (estimate, {per_word:.0f} per word)",
        f"{est.output_tokens:,}",
        f"${est.output_cost:,.2f}",
    )
    table.add_row("Total", "", f"[bold]${est.cost:,.2f}[/bold]")
    console.print(table)
    console.print(
        f"[dim]Model {MODEL}, effort {effort}, max_tokens {MAX_TOKENS:,}. Input is "
        "estimated from characters, all at the uncached price; output per word "
        f"(thinking included) is {basis}.[/dim]"
    )
    if plan.skipped_saved or plan.skipped_pending:
        saved = (
            f"units whose notes are version {NOTE_VERSION}"
            if kind == "notes"
            else "units already saved"
        )
        console.print(
            f"Left out: {plan.skipped_saved:,} {saved}, "
            f"{plan.skipped_pending:,} waiting in a batch not yet collected "
            "(--redo includes them)."
        )
    _print_unsaved(plan)
    if plan.largest is not None:
        largest_output = plan.largest_words * per_word
        lines = plan.work.count(plan.largest.line_count)
        console.print(
            f"Largest request: {lines}, {plan.largest_words} words, "
            f"about {largest_output:,.0f} output tokens of {MAX_TOKENS:,}."
        )
        if largest_output > MAX_TOKENS / 2:
            console.print(
                "[yellow]The largest request may come near max_tokens; "
                "a truncated result fails and can be resubmitted.[/yellow]"
            )
    return est


def _scope_options(f):
    f = click.option(
        "--retry",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Request the ids listed in a file of failed requests",
    )(f)
    f = click.option(
        "--lines", "lines_arg", help="Units overlapping these lines, e.g. 1.1-1.611"
    )(f)
    f = click.option("--books", help="A book or range of books, e.g. 1 or 2-7")(f)
    f = click.option(
        "--redo",
        is_flag=True,
        help="Include units already saved or waiting in an uncollected batch",
    )(f)
    f = click.option(
        "--effort",
        type=click.Choice(EFFORTS),
        default=EFFORT,
        show_default=True,
        help="Opus 5.5's effort (thinking depth)",
    )(f)
    f = click.option(
        "--output-per-word",
        type=float,
        default=None,
        help="Output tokens per word for the estimate "
        f"(default: measured at this effort, else {DEFAULT_OUTPUT_PER_WORD:.0f})",
    )(f)
    return f


def _repeat_words_option(f):
    return click.option(
        "--repeat-words",
        is_flag=True,
        help="Ask each word's answer to repeat its word, and check it on collect "
        "(a skipped word then fails where it happens)",
    )(f)


def _study_estimate(
    conn, text_id, books, lines_arg, retry, redo, effort, output_per_word, repeat_words
):
    """The plan for a study batch, with its estimate printed (None if empty)."""
    plan = _plan(conn, text_id, books, lines_arg, retry, redo, effort, repeat_words)
    if not plan.requests:
        console.print("Nothing to request: every unit is saved or waiting.")
        return plan, None
    per_word, basis = _output_per_word(conn, output_per_word, "study", effort)
    return plan, _print_estimate(plan, per_word, basis, "study", effort)


@batch_cmd.command("estimate")
@click.argument("text_id", type=int)
@_scope_options
@_repeat_words_option
@click.option("--show", is_flag=True, help="Print the requests' user messages")
def estimate_cmd(
    text_id, books, lines_arg, retry, redo, effort, output_per_word, repeat_words, show
):
    """Estimate a batch's tokens and cost locally (no API call).

    \b
    Without --books, --lines or --retry, the whole text. Units with saved
    results, or waiting in a batch not yet collected, are left out.
    """
    conn = get_connection()
    plan, _ = _study_estimate(
        conn,
        text_id,
        books,
        lines_arg,
        retry,
        redo,
        effort,
        output_per_word,
        repeat_words,
    )
    if show:
        _show(plan.requests)


@batch_cmd.command("submit")
@click.argument("text_id", type=int)
@_scope_options
@_repeat_words_option
@click.option(
    "--yes", is_flag=True, help="Submit (spends money; approve the estimate first)"
)
def submit_cmd(
    text_id, books, lines_arg, retry, redo, effort, output_per_word, repeat_words, yes
):
    """Submit a batch: prints its estimate, and submits only with --yes.

    \b
    Without --books, --lines or --retry, the whole text. --retry FILE
    resubmits the requests a collect listed as failed (give --repeat-words
    again if the batch had it).
    """
    conn = get_connection()
    plan, est = _study_estimate(
        conn,
        text_id,
        books,
        lines_arg,
        retry,
        redo,
        effort,
        output_per_word,
        repeat_words,
    )
    if est is not None:
        _submit_units(conn, text_id, plan, est, yes, "study", effort)


def _show(requests):
    """Print requests' ids and user messages (estimate --show)."""
    for r in requests:
        console.print(f"[bold]{r['custom_id']}[/bold]", soft_wrap=True)
        console.print(
            r["params"]["messages"][0]["content"], soft_wrap=True, markup=False
        )
        console.print()


def _submit_units(
    conn, text_id, plan: _Plan, est, yes, kind="study", effort=EFFORT, pilot=False
):
    """Submit a study or notes plan's requests with --yes, and record the batch."""
    if not yes:
        console.print(
            f"[yellow]Nothing submitted.[/yellow] This batch would cost about "
            f"${est.cost:,.2f}. If you approve the estimate, rerun with --yes."
        )
        return

    import anthropic

    try:
        client = batch_client()
        batch = client.messages.batches.create(requests=plan.requests)
    except RuntimeError as e:
        _fail(f"Error: {e}")
    except anthropic.APIError as e:
        _fail(f"Error: the batch was not submitted: {e}")
    save_submitted_batch(
        conn,
        StudyBatch(
            id=batch.id,
            text_id=text_id,
            scope=plan.scope,
            model=MODEL,
            status=batch.processing_status,
            request_count=len(plan.requests),
            kind=kind,
            effort=effort,
            pilot=pilot,
        ),
        [
            StudyRequest(
                batch_id=batch.id,
                custom_id=custom_id(u),
                first_line=u.first_line,
                last_line=u.last_line,
                word_count=n,
            )
            for u, n in zip(plan.units, plan.word_counts)
        ],
    )
    console.print(
        f"[green]Submitted[/green] batch {batch.id}: {len(plan.requests):,} requests "
        f"({plan.scope}).\nMost batches end within an hour (at most 24 hours). "
        f"Check with 'greek study batch status {batch.id}', then run "
        f"'greek study batch collect {batch.id}'.",
        soft_wrap=True,
    )


def _timestamp(value) -> Optional[str]:
    """An API datetime as SQLite's UTC 'YYYY-MM-DD HH:MM:SS'."""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def _refresh(conn, client, batch_id: str):
    """The batch as the API reports it; its status is saved."""
    import anthropic

    try:
        remote = client.messages.batches.retrieve(batch_id)
    except anthropic.APIError as e:
        _fail(f"Error: could not read batch {batch_id}: {e}")
    set_batch_status(
        conn, batch_id, remote.processing_status, _timestamp(remote.ended_at)
    )
    return remote


@batch_cmd.command("status")
@click.argument("batch_id", required=False)
def status_cmd(batch_id):
    """Show submitted batches, or one batch's request counts (free API calls)."""
    conn = get_connection()
    if batch_id is not None:
        if get_study_batch(conn, batch_id) is None:
            _fail(f"No submitted batch {batch_id}.")
        try:
            client = batch_client()
        except RuntimeError as e:
            _fail(f"Error: {e}")
        remote = _refresh(conn, client, batch_id)
        counts = remote.request_counts
        console.print(
            f"Batch {batch_id}: {remote.processing_status}. Requests: "
            f"{counts.processing} processing, {counts.succeeded} succeeded, "
            f"{counts.errored} errored, {counts.canceled} canceled, "
            f"{counts.expired} expired."
        )
        if remote.processing_status == "ended":
            console.print(
                f"Collect it with 'greek study batch collect {batch_id}'.",
                soft_wrap=True,
            )
        return

    batches = list_study_batches(conn)
    if not batches:
        console.print("No batches submitted yet.")
        return
    waiting = [b for b in batches if b.status != "ended"]
    if waiting:
        try:
            client = batch_client()
        except RuntimeError as e:
            console.print(f"[yellow]Status not refreshed: {e}[/yellow]")
        else:
            for b in waiting:
                _refresh(conn, client, b.id)
            batches = list_study_batches(conn)
    table = Table(title="Study batches")
    table.add_column("Batch", no_wrap=True)  # whole, so it can be copied
    table.add_column("Scope")
    table.add_column("Submitted (UTC)")
    table.add_column("Status")
    table.add_column("Requests", justify="right")
    table.add_column("Cost", justify="right")
    for b in batches:
        table.add_row(
            b.id,
            b.scope,
            b.submitted_at,
            "collected" if b.collected_at else b.status,
            f"{b.request_count:,}",
            f"${b.cost_usd:,.2f}" if b.collected_at else "–",
        )
    console.print(table)


@batch_cmd.command("collect")
@click.argument("batch_id")
@click.option(
    "--failed-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Where to list failed request ids (default data/study-failed-<batch>.txt)",
)
@click.option(
    "--store",
    is_flag=True,
    help="Store a pilot uses batch's labels (a pilot otherwise stores nothing)",
)
def collect_cmd(batch_id, failed_file, store):
    """Store an ended batch's results and list the requests that failed.

    \b
    Each result is checked against its lines: one literal line per line, one
    gloss and note per word, and a parse for each word the treebank lacks.
    Errors, expiry, refusals, truncation and wrong counts fail the request;
    the failed ids go to a file for 'submit --retry'. Collecting again is
    safe: results are replaced.

    \b
    A principal-parts batch (greek study parts) is checked verb by verb: six
    parts for each verb asked for. Its failed verbs are resubmitted with
    'greek study parts submit TEXT_ID --retry BATCH_ID'.

    \b
    A notes batch (greek study notes) is checked word by word: one note per
    word, repeating the word. Only the notes change. The old and new notes go
    to data/notes-<batch>.json; a pilot's notes go only there.

    \b
    A dictionary-forms batch (greek study forms) is checked lemma by lemma:
    a headword for each noun and adjective asked for and a construction for
    each verb, repeating the lemma. Each noun's article is compared with
    LSJ's (differences are listed, not rejected), and the results go to
    data/forms-<batch>.json for review.

    \b
    A uses batch (greek study uses) is checked word by word: a label for
    every marked word, each naming its word by number and form. The labels
    go to data/uses-<batch>.json; a pilot's are stored only with --store,
    which can be given when collecting it again later.
    """
    import anthropic

    conn = get_connection()
    batch = get_study_batch(conn, batch_id)
    if batch is None:
        _fail(f"No submitted batch {batch_id}.")
    if store and batch.kind != "uses":
        _fail(f"--store is for a pilot uses batch; {batch_id} is a {batch.kind} batch.")
    if batch.text_id is None or get_text(conn, batch.text_id) is None:
        _fail(f"The text of batch {batch_id} has been deleted.")
    try:
        client = batch_client()
    except RuntimeError as e:
        _fail(f"Error: {e}")
    remote = _refresh(conn, client, batch_id)
    if remote.processing_status != "ended":
        _fail(
            f"Batch {batch_id} has not ended yet ({remote.processing_status}, "
            f"{remote.request_counts.processing} requests processing)."
        )
    if batch.kind == "parts":
        _collect_parts(conn, client, batch)
        return
    if batch.kind == "notes":
        _collect_notes(conn, client, batch, failed_file)
        return
    if batch.kind == "forms":
        _collect_forms(conn, client, batch)
        return
    if batch.kind == "uses":
        _collect_uses(conn, client, batch, failed_file, store)
        return

    text_id = batch.text_id
    requests = {r.custom_id: r for r in list_study_requests(conn, batch_id)}
    lines = {line.line_number: line for line in get_lines(conn, text_id)}
    parses = _parses_by_line(list_word_parses(conn, text_id))
    failures: Dict[str, str] = {}
    stored = Counter()
    seen = set()
    try:
        for entry in client.messages.batches.results(batch_id):
            request = requests.get(entry.custom_id)
            seen.add(entry.custom_id)
            if request is None:
                console.print(
                    f"[yellow]Skipped an unknown request {entry.custom_id}.[/yellow]"
                )
                continue
            numbers = range(request.first_line, request.last_line + 1)
            unit_lines = [lines[n] for n in numbers if n in lines]
            words = study_words(unit_lines, [p for n in numbers for p in parses[n]])
            outcome = read_result(entry, words, len(numbers), batch.model)
            if len(unit_lines) != len(numbers):
                outcome.failure = "lines of the request are missing"
            if outcome.failure:
                failures[entry.custom_id] = outcome.failure
                fail_study_request(
                    conn, batch_id, entry.custom_id, outcome.failure, outcome.usage
                )
                continue
            stored.update(_save_outcome(conn, batch, request, words, outcome))
    except anthropic.APIError as e:
        _fail(f"Error reading the results of {batch_id}: {e}. Collect again later.")
    for cid in requests.keys() - seen:
        failures[cid] = "no result returned"
        fail_study_request(conn, batch_id, cid, "no result returned")

    batch = finish_collect(conn, batch_id)
    _print_collect(batch, stored, failures)
    if failures:
        path = failed_file or _data_dir() / f"study-failed-{batch_id}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(f"{cid}\n" for cid in sorted(failures, key=_line_order))
        )
        console.print(
            f"[yellow]{len(failures):,} requests failed[/yellow]; their ids are in "
            f"{path}.\nResubmit them with 'greek study batch submit {text_id} "
            f"--retry {path} --effort {batch.effort}' (prints the estimate; add "
            "--yes to submit).",
            soft_wrap=True,
        )


def _save_outcome(conn, batch, request, words, outcome) -> Counter:
    """Store a checked result and mark its request succeeded; counts for the report."""
    out, model, text_id = outcome.output, batch.model, batch.text_id
    numbers = range(request.first_line, request.last_line + 1)
    save_study_result(
        conn,
        TranslationUnit(text_id, numbers[0], numbers[-1], out.fluent, model),
        [LineTranslation(text_id, n, s, model) for n, s in zip(numbers, out.literal)],
        [
            WordStudy(
                text_id,
                w.line_number,
                w.token_index,
                o.gloss,
                o.note,
                o.parse_check,
                model,
                NOTE_VERSION,
            )
            for w, o in zip(words, out.words)
        ],
        [
            (w.line_number, w.token_index, o.lemma, o.postag)
            for w, o in zip(words, out.words)
            if o.lemma and o.postag
        ],
        request,
        outcome.usage,
    )
    return Counter(
        {
            "requests": 1,
            "lines": len(numbers),
            "words": len(words),
            "parse checks": sum(1 for o in out.words if o.parse_check),
            "model parses": sum(1 for o in out.words if o.postag),
            "empty notes": sum(1 for o in out.words if not o.note),
        }
    )


def _line_order(cid: str):
    parsed = parse_custom_id(cid)
    return (parsed[1], parsed[2]) if parsed else (0, 0)


def _print_collect(batch: StudyBatch, stored: Counter, failures: Dict[str, str]):
    table = Table(title=f"Collected {batch.id}: {batch.scope}")
    table.add_column("", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Requests stored", f"{stored['requests']:,}")
    table.add_row("Lines", f"{stored['lines']:,}")
    table.add_row("Words", f"{stored['words']:,}")
    table.add_row("Words parsed by the model", f"{stored['model parses']:,}")
    table.add_row("parse_check remarks", f"{stored['parse checks']:,}")
    table.add_row("Words with an empty note", f"{stored['empty notes']:,}")
    table.add_row("Requests failed", f"{len(failures):,}")
    for reason, n in Counter(
        re.sub(r"\(.*\)$", "", f).strip() if f.startswith("refusal") else f
        for f in failures.values()
    ).most_common():
        table.add_row(f"  {reason}", f"{n:,}")
    console.print(table)
    usage = Usage(
        batch.input_tokens,
        batch.cache_write_tokens,
        batch.cache_read_tokens,
        batch.output_tokens,
        batch.cost_usd,
    )
    console.print(
        f"Tokens: {usage.input_tokens:,} input, {usage.cache_write_tokens:,} cache "
        f"write, {usage.cache_read_tokens:,} cache read, {usage.output_tokens:,} "
        f"output. Cost at batch prices: [bold]${usage.cost_usd:,.2f}[/bold]."
    )
    if stored["words"]:
        console.print(
            f"Per stored word: {batch.output_tokens / stored['words']:,.1f} output "
            f"tokens, ${1000 * batch.cost_usd / stored['words']:,.2f} per 1,000 words "
            "(failed requests included)."
        )


# --- greek study notes --------------------------------------------------------


@study_cmd.group("notes")
def notes_cmd():
    """Notes only, with the revised note rule, from Claude Opus 5.5 in batches.

    \b
    Redoes the notes of units that already have saved study data (glosses and
    translations from 'greek study batch'): longer notes for the words worth
    a closer look. Each request sends the unit's lines, its saved fluent
    translation, and every word with its parse, saved gloss and a mark when
    its lemma is new in the book. Only the notes change; they are stored as
    version 2. Only `submit --yes` spends money; run `estimate` (or `submit`
    without --yes) and approve the estimate first. Follow a batch with
    `greek study batch status | collect`.

    \b
    A --pilot batch stores nothing: collect writes the old and new notes to
    data/notes-<batch>.json for review.

    \b
    Example:
      greek study notes estimate 12 --lines 1.1-1.8 --lines 1.43-1.52 --pilot
      greek study notes submit 12 --books 1 --yes
      greek study batch collect msgbatch_...
    """


def _notes_plan(conn, text_id, books, lines_args, retry, redo, effort, pilot) -> _Plan:
    """Choose the units whose notes to request, skip those done, and build requests."""
    chosen = _select_units(conn, text_id, books, lines_args, retry)
    text, by_number, units = chosen.text, chosen.by_number, chosen.units
    scope = f"{chosen.scope}: notes, effort {effort}" + (", pilot" if pilot else "")
    plan = _Plan(
        text=text, units=[], requests=[], word_counts=[], scope=scope, work=chosen.work
    )
    all_parses = list_word_parses(conn, text_id)
    if not redo:
        word_counts = _words_per_line(list(by_number.values()), all_parses)
        done = units_with_notes(conn, units, word_counts, MODEL, NOTE_VERSION)
        waiting = pending_ranges(
            conn, text_id, MODEL, "notes", pilot, effort if pilot else None
        )
        kept = []
        for u in units:
            if (u.first_line, u.last_line) in done:
                plan.skipped_saved += 1
            elif any(u.first_line <= b and u.last_line >= a for a, b in waiting):
                plan.skipped_pending += 1
            else:
                kept.append(u)
        units = kept

    marks = first_in_book(study_words(list(by_number.values()), all_parses))
    parses = _parses_by_line(all_parses)
    for u in units:
        unit_lines = [by_number[n] for n in range(u.first_line, u.last_line + 1)]
        words = study_words(
            unit_lines,
            [p for line in unit_lines for p in parses[line.line_number]],
            marks,
        )
        saved = {
            (w.line_number, w.token_index): w
            for w in list_word_study(conn, text_id, u.first_line, u.last_line, MODEL)
        }
        fluent = [
            t.fluent
            for t in list_translation_units(
                conn, text_id, u.first_line, u.last_line, MODEL
            )
            if (t.first_line, t.last_line) == (u.first_line, u.last_line)
        ]
        if not fluent or any(
            (w.line_number, w.token_index) not in saved for w in words
        ):
            plan.skipped_unsaved += 1
            continue
        for w in words:
            w.gloss = saved[(w.line_number, w.token_index)].gloss
        plan.units.append(u)
        plan.requests.append(
            build_notes_request(chosen.work, u, unit_lines, fluent[0], words, effort)
        )
        plan.word_counts.append(len(words))
        plan.line_count += len(unit_lines)
        plan.unparsed += sum(w.needs_parse for w in words)
        if len(words) > plan.largest_words:
            plan.largest, plan.largest_words = u, len(words)
    return plan


def _notes_options(f):
    f = click.option(
        "--retry",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Request the ids listed in a file of failed requests",
    )(f)
    f = click.option(
        "--lines",
        "lines_args",
        multiple=True,
        help="Units overlapping these lines, e.g. 1.1-1.8 (repeat for more ranges)",
    )(f)
    f = click.option("--books", help="A book or range of books, e.g. 1 or 2-7")(f)
    f = click.option(
        "--redo",
        is_flag=True,
        help="Include units with version-2 notes or waiting in an uncollected batch",
    )(f)
    f = click.option(
        "--effort",
        type=click.Choice(EFFORTS),
        default=EFFORT,
        show_default=True,
        help="Opus 5.5's effort (thinking depth)",
    )(f)
    f = click.option(
        "--pilot",
        is_flag=True,
        help="Store nothing: collect writes the notes to a review file",
    )(f)
    f = click.option(
        "--output-per-word",
        type=float,
        default=None,
        help="Output tokens per word for the estimate "
        f"(default: measured at this effort, else {DEFAULT_NOTES_OUTPUT_PER_WORD:.0f})",
    )(f)
    return f


def _notes_estimate(
    conn, text_id, books, lines_args, retry, redo, effort, pilot, output_per_word
):
    """The plan for a notes batch, with its estimate printed (None if empty)."""
    plan = _notes_plan(conn, text_id, books, lines_args, retry, redo, effort, pilot)
    if not plan.requests:
        console.print(
            "Nothing to request: every unit has version-2 notes or is waiting "
            "in a batch."
        )
        _print_unsaved(plan)
        return plan, None
    per_word, basis = _output_per_word(conn, output_per_word, "notes", effort)
    return plan, _print_estimate(plan, per_word, basis, "notes", effort)


@notes_cmd.command("estimate")
@click.argument("text_id", type=int)
@_notes_options
def notes_estimate_cmd(
    text_id, books, lines_args, retry, redo, effort, pilot, output_per_word
):
    """Estimate a notes batch's tokens and cost locally (no API call).

    \b
    Without --books, --lines or --retry, the whole text. Units whose notes
    are version 2, or waiting in a notes batch not yet collected, are left
    out, as are units without saved study data.
    """
    conn = get_connection()
    _notes_estimate(
        conn, text_id, books, lines_args, retry, redo, effort, pilot, output_per_word
    )


@notes_cmd.command("submit")
@click.argument("text_id", type=int)
@_notes_options
@click.option(
    "--yes", is_flag=True, help="Submit (spends money; approve the estimate first)"
)
def notes_submit_cmd(
    text_id, books, lines_args, retry, redo, effort, pilot, output_per_word, yes
):
    """Submit a notes batch: prints its estimate, and submits only with --yes.

    \b
    Without --books, --lines or --retry, the whole text. --retry FILE
    resubmits the requests a collect listed as failed.
    """
    conn = get_connection()
    plan, est = _notes_estimate(
        conn, text_id, books, lines_args, retry, redo, effort, pilot, output_per_word
    )
    if est is not None:
        _submit_units(conn, text_id, plan, est, yes, "notes", effort, pilot)


def _collect_notes(conn, client, batch: StudyBatch, failed_file: Optional[Path]):
    """
    Store an ended notes batch's notes (a pilot's only go to the review file),
    write the old and new notes to data/notes-<batch>.json, and report.
    """
    import anthropic

    text_id = batch.text_id
    requests = {r.custom_id: r for r in list_study_requests(conn, batch.id)}
    lines = {line.line_number: line for line in get_lines(conn, text_id)}
    all_parses = list_word_parses(conn, text_id)
    marks = first_in_book(study_words(list(lines.values()), all_parses))
    parses = _parses_by_line(all_parses)
    failures: Dict[str, str] = {}
    review = []
    stored = Counter()
    lengths = {"old": [], "new": []}
    seen = set()
    try:
        for entry in client.messages.batches.results(batch.id):
            request = requests.get(entry.custom_id)
            seen.add(entry.custom_id)
            if request is None:
                console.print(
                    f"[yellow]Skipped an unknown request {entry.custom_id}.[/yellow]"
                )
                continue
            numbers = range(request.first_line, request.last_line + 1)
            unit_lines = [lines[n] for n in numbers if n in lines]
            words = study_words(
                unit_lines, [p for n in numbers for p in parses[n]], marks
            )
            outcome = read_notes_result(entry, words, batch.model)
            if len(unit_lines) != len(numbers):
                outcome.failure = "lines of the request are missing"
            saved = {
                (w.line_number, w.token_index): w
                for w in list_word_study(
                    conn, text_id, numbers[0], numbers[-1], batch.model
                )
            }
            if not outcome.failure:
                try:
                    if batch.pilot:
                        succeed_study_request(
                            conn, batch.id, entry.custom_id, outcome.usage
                        )
                    else:
                        save_notes_result(
                            conn,
                            text_id,
                            batch.model,
                            [
                                (w.line_number, w.token_index, note)
                                for w, note in zip(words, outcome.notes)
                            ],
                            NOTE_VERSION,
                            request,
                            outcome.usage,
                        )
                except ValueError as e:
                    outcome.failure = str(e)
            if outcome.failure:
                failures[entry.custom_id] = outcome.failure
                fail_study_request(
                    conn, batch.id, entry.custom_id, outcome.failure, outcome.usage
                )
                continue
            old = [saved.get((w.line_number, w.token_index)) for w in words]
            review.append(
                {
                    "custom_id": entry.custom_id,
                    "lines": [line.reference for line in unit_lines],
                    "words": [
                        {
                            "ref": w.reference,
                            "word": w.form,
                            "lemma": " + ".join(p.lemma or "?" for p in w.parts),
                            "first_in_book": w.first_in_book,
                            "gloss": s.gloss if s else None,
                            "old": s.note if s else None,
                            "old_version": s.note_version if s else None,
                            "new": note,
                        }
                        for w, s, note in zip(words, old, outcome.notes)
                    ],
                }
            )
            lengths["old"] += [len(s.note) for s in old if s]
            lengths["new"] += [len(note) for note in outcome.notes]
            stored.update(
                {
                    "requests": 1,
                    "lines": len(numbers),
                    "words": len(words),
                    "empty notes": sum(1 for note in outcome.notes if not note),
                }
            )
    except anthropic.APIError as e:
        _fail(f"Error reading the results of {batch.id}: {e}. Collect again later.")
    for cid in requests.keys() - seen:
        failures[cid] = "no result returned"
        fail_study_request(conn, batch.id, cid, "no result returned")

    batch = finish_collect(conn, batch.id)
    review_path = _data_dir() / f"notes-{batch.id}.json"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review.sort(key=lambda unit: _line_order(unit["custom_id"]))
    review_path.write_text(
        json.dumps(
            {
                "batch": batch.id,
                "scope": batch.scope,
                "effort": batch.effort,
                "pilot": batch.pilot,
                "units": review,
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    _print_notes_collect(batch, stored, lengths, failures)
    console.print(
        f"Old and new notes: {review_path}"
        + (" (a pilot: no notes were stored)." if batch.pilot else "."),
        soft_wrap=True,
    )
    if failures:
        path = failed_file or _data_dir() / f"study-failed-{batch.id}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(f"{cid}\n" for cid in sorted(failures, key=_line_order))
        )
        console.print(
            f"[yellow]{len(failures):,} requests failed[/yellow]; their ids are in "
            f"{path}.\nResubmit them with 'greek study notes submit {text_id} "
            f"--retry {path} --effort {batch.effort}"
            + (" --pilot" if batch.pilot else "")
            + "' (prints the estimate; add --yes to submit).",
            soft_wrap=True,
        )


def _print_notes_collect(batch, stored: Counter, lengths, failures: Dict[str, str]):
    table = Table(title=f"Collected {batch.id}: {batch.scope}")
    table.add_column("", style="bold")
    table.add_column("Count", justify="right")
    done = "Requests checked (pilot)" if batch.pilot else "Requests stored"
    table.add_row(done, f"{stored['requests']:,}")
    table.add_row("Lines", f"{stored['lines']:,}")
    table.add_row("Words", f"{stored['words']:,}")
    table.add_row("Words with an empty note", f"{stored['empty notes']:,}")
    for label, values in (("new", lengths["new"]), ("before", lengths["old"])):
        if values:
            ordered = sorted(values)
            table.add_row(
                f"Note length, {label} (characters: mean / median / longest)",
                f"{sum(values) / len(values):,.0f} / {ordered[len(ordered) // 2]:,} "
                f"/ {ordered[-1]:,}",
            )
    table.add_row("Requests failed", f"{len(failures):,}")
    for reason, n in Counter(
        re.sub(r"\(.*\)$", "", f).strip() if f.startswith("refusal") else f
        for f in failures.values()
    ).most_common():
        table.add_row(f"  {reason}", f"{n:,}")
    console.print(table)
    console.print(
        f"Tokens: {batch.input_tokens:,} input, {batch.cache_write_tokens:,} cache "
        f"write, {batch.cache_read_tokens:,} cache read, {batch.output_tokens:,} "
        f"output. Cost at batch prices: [bold]${batch.cost_usd:,.2f}[/bold] "
        f"(effort {batch.effort})."
    )
    if stored["words"]:
        console.print(
            f"Per word: {batch.output_tokens / stored['words']:,.1f} output tokens, "
            f"${1000 * batch.cost_usd / stored['words']:,.2f} per 1,000 words "
            "(failed requests included)."
        )


# --- greek study parts --------------------------------------------------------


@study_cmd.group("parts")
def parts_cmd():
    """Principal parts of a text's verbs from Claude Opus 5.5, in Message Batches.

    \b
    The verbs are the treebank lemmas tagged as verbs, 20 to a request. The
    parts are saved in the work's dialect (Homeric for the Iliad and the
    Odyssey, Ionic for Herodotus, Attic for Thucydides), and the reader's
    word pane shows them. Only `submit --yes` spends money; run `estimate`
    (or `submit` without --yes) and approve the estimate first. Follow a
    batch with `greek study batch status | collect`.

    \b
    Example:
      greek study parts estimate 12 --books 1
      greek study parts submit 12 --books 1 --yes
      greek study batch collect msgbatch_...
    """


@dataclass
class _PartsPlan:
    """The verbs a parts submit would request, in requests, and what was left out."""

    text: TextMeta
    verbs: List[str]
    groups: List[List[str]]
    requests: List[dict]
    scope: str
    work: WorkProfile
    skipped_saved: int = 0
    skipped_pending: int = 0


def _parts_plan(conn, text_id, books, retry, redo) -> _PartsPlan:
    """Choose the verbs to request, skip those already done, and build requests."""
    if books is not None and retry is not None:
        _fail("Use only one of --books and --retry.")
    text = get_text(conn, text_id)
    if text is None:
        _fail(f"No text with ID {text_id}.")
    lines = get_lines(conn, text_id)
    if not list_tb_sentences(conn, text_id):
        _fail(
            f"'{text.title}' has no treebank data; run "
            "'greek study import-treebank' first."
        )

    if retry is not None:
        batch = get_study_batch(conn, retry)
        if batch is None or batch.kind != "parts":
            _fail(f"No principal-parts batch {retry}.")
        if batch.text_id != text_id:
            _fail(f"Batch {retry} is for text {batch.text_id}, not {text_id}.")
        verbs = [v for r in list_parts_requests(conn, retry) for v in r.verbs]
        scope = f"{text.title}, retry of {retry}"
    else:
        if books is not None:
            first, last = _book_range(books)
            lines = [
                line
                for line in lines
                if first <= (_book_number(line.reference) or 0) <= last
            ]
            if not lines:
                _fail(f"'{text.title}' has no book {books}.")
        verbs = verb_lemmas(conn, text_id, lines[0].line_number, lines[-1].line_number)
        scope = f"{text.title} {lines[0].reference}–{lines[-1].reference}"
    if not verbs:
        _fail("No verbs to request.")

    work = _work(conn, text_id)
    plan = _PartsPlan(text=text, verbs=[], groups=[], requests=[], scope="", work=work)
    if not redo:
        saved = verbs_with_parts(conn, verbs, MODEL, work.dialect)
        waiting = pending_parts_verbs(conn, work.dialect, MODEL)
        kept = []
        for verb in verbs:
            if verb in saved:
                plan.skipped_saved += 1
            elif verb in waiting:
                plan.skipped_pending += 1
            else:
                kept.append(verb)
        verbs = kept
    plan.verbs = verbs
    plan.groups = group_verbs(verbs)
    plan.requests = [
        build_parts_request(work, parts_custom_id(text_id, n), group)
        for n, group in enumerate(plan.groups, 1)
    ]
    plan.scope = (
        f"{scope}: principal parts of {len(verbs):,} verbs ({DIALECTS[work.dialect]})"
    )
    return plan


def _output_per_verb(conn, override: Optional[float]):
    """Output tokens per verb for estimates, and where the number comes from."""
    if override is not None:
        return override, "given with --output-per-verb"
    measured = measured_output_per_verb(conn, MODEL)
    if measured is not None:
        return measured, "measured over collected batches"
    return DEFAULT_OUTPUT_PER_VERB, "an estimate until a batch is collected"


def _print_parts_estimate(plan: _PartsPlan, per_verb: float, basis: str):
    est = estimate_parts(plan.requests, len(plan.verbs), per_verb)
    table = Table(title=f"Parts batch: {plan.scope} (ID {plan.text.id})")
    table.add_column("", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Batch cost", justify="right")
    table.add_row(f"Requests ({VERBS_PER_REQUEST} verbs each)", f"{est.requests:,}", "")
    table.add_row("Verbs", f"{est.verbs:,}", "")
    table.add_row(
        "Input tokens (estimate)", f"{est.input_tokens:,}", f"${est.input_cost:,.2f}"
    )
    table.add_row(
        f"Output tokens (estimate, {per_verb:.0f} per verb)",
        f"{est.output_tokens:,}",
        f"${est.output_cost:,.2f}",
    )
    table.add_row("Total", "", f"[bold]${est.cost:,.2f}[/bold]")
    console.print(table)
    console.print(
        f"[dim]Model {MODEL}, effort medium, max_tokens {MAX_TOKENS:,}. Input is "
        "estimated from characters, all at the uncached price; output per verb "
        f"(thinking included) is {basis}.[/dim]"
    )
    if plan.skipped_saved or plan.skipped_pending:
        dialect = DIALECTS[plan.work.dialect]
        console.print(
            f"Left out: {plan.skipped_saved:,} verbs with saved {dialect} parts "
            f"by {MODEL}, {plan.skipped_pending:,} waiting in a batch not yet "
            "collected (--redo includes them)."
        )
    return est


def _parts_options(f):
    f = click.option(
        "--retry",
        metavar="BATCH_ID",
        help="Request the verbs of a collected parts batch that have no parts yet",
    )(f)
    f = click.option("--books", help="A book or range of books, e.g. 1 or 2-24")(f)
    f = click.option(
        "--redo",
        is_flag=True,
        help="Include verbs with saved parts or waiting in an uncollected batch",
    )(f)
    f = click.option(
        "--output-per-verb",
        type=float,
        default=None,
        help="Output tokens per verb for the estimate (default: measured, else 350)",
    )(f)
    return f


@parts_cmd.command("estimate")
@click.argument("text_id", type=int)
@_parts_options
def parts_estimate_cmd(text_id, books, retry, redo, output_per_verb):
    """Estimate a parts batch's tokens and cost locally (no API call).

    \b
    Without --books or --retry, every verb of the text. Verbs with saved
    parts, or waiting in a batch not yet collected, are left out.
    """
    conn = get_connection()
    plan = _parts_plan(conn, text_id, books, retry, redo)
    if not plan.requests:
        console.print("Nothing to request: every verb has parts or is waiting.")
        return
    _print_parts_estimate(plan, *_output_per_verb(conn, output_per_verb))


@parts_cmd.command("submit")
@click.argument("text_id", type=int)
@_parts_options
@click.option(
    "--yes", is_flag=True, help="Submit (spends money; approve the estimate first)"
)
def parts_submit_cmd(text_id, books, retry, redo, output_per_verb, yes):
    """Submit a parts batch: prints its estimate, and submits only with --yes."""
    conn = get_connection()
    plan = _parts_plan(conn, text_id, books, retry, redo)
    if not plan.requests:
        console.print("Nothing to request: every verb has parts or is waiting.")
        return
    est = _print_parts_estimate(plan, *_output_per_verb(conn, output_per_verb))
    if not yes:
        console.print(
            f"[yellow]Nothing submitted.[/yellow] This batch would cost about "
            f"${est.cost:,.2f}. If you approve the estimate, rerun with --yes."
        )
        return

    import anthropic

    try:
        client = batch_client()
        batch = client.messages.batches.create(requests=plan.requests)
    except RuntimeError as e:
        _fail(f"Error: {e}")
    except anthropic.APIError as e:
        _fail(f"Error: the batch was not submitted: {e}")
    save_submitted_parts_batch(
        conn,
        StudyBatch(
            id=batch.id,
            text_id=text_id,
            scope=plan.scope,
            model=MODEL,
            status=batch.processing_status,
            request_count=len(plan.requests),
            kind="parts",
            dialect=plan.work.dialect,
        ),
        [
            PartsRequest(
                batch_id=batch.id,
                custom_id=r["custom_id"],
                verbs=group,
            )
            for r, group in zip(plan.requests, plan.groups)
        ],
    )
    console.print(
        f"[green]Submitted[/green] batch {batch.id}: {len(plan.requests):,} requests "
        f"({plan.scope}).\nMost batches end within an hour (at most 24 hours). "
        f"Check with 'greek study batch status {batch.id}', then run "
        f"'greek study batch collect {batch.id}'.",
        soft_wrap=True,
    )


def _collect_parts(conn, client, batch: StudyBatch):
    """Store an ended parts batch's results, verb by verb, and report."""
    import anthropic

    requests = {r.custom_id: r for r in list_parts_requests(conn, batch.id)}
    failures: Dict[str, str] = {}
    stored = Counter()
    seen = set()
    try:
        for entry in client.messages.batches.results(batch.id):
            request = requests.get(entry.custom_id)
            seen.add(entry.custom_id)
            if request is None:
                console.print(
                    f"[yellow]Skipped an unknown request {entry.custom_id}.[/yellow]"
                )
                continue
            outcome = read_parts_result(entry, request.verbs, batch.model)
            if outcome.failure:
                fail_parts_request(conn, request, outcome.failure, outcome.usage)
                failures[entry.custom_id] = outcome.failure
                stored["failed verbs"] += len(request.verbs)
                continue
            save_parts_result(
                conn,
                batch.model,
                outcome.parts,
                request,
                outcome.summary,
                outcome.usage,
                batch.dialect or "homeric",
            )
            stored["verbs"] += len(outcome.parts)
            stored["failed verbs"] += len(outcome.failed)
            if outcome.failed:
                failures[entry.custom_id] = outcome.summary
            else:
                stored["requests"] += 1
    except anthropic.APIError as e:
        _fail(f"Error reading the results of {batch.id}: {e}. Collect again later.")
    for cid in requests.keys() - seen:
        failures[cid] = "no result returned"
        fail_parts_request(conn, requests[cid], "no result returned")
        stored["failed verbs"] += len(requests[cid].verbs)

    batch = finish_collect(conn, batch.id)
    table = Table(title=f"Collected {batch.id}: {batch.scope}")
    table.add_column("", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Requests fully stored", f"{stored['requests']:,}")
    table.add_row("Verbs stored", f"{stored['verbs']:,}")
    table.add_row("Verbs failed", f"{stored['failed verbs']:,}")
    console.print(table)
    console.print(
        f"Tokens: {batch.input_tokens:,} input, {batch.cache_write_tokens:,} cache "
        f"write, {batch.cache_read_tokens:,} cache read, {batch.output_tokens:,} "
        f"output. Cost at batch prices: [bold]${batch.cost_usd:,.2f}[/bold]."
    )
    asked = sum(len(r.verbs) for r in requests.values())
    if asked:
        console.print(
            f"Per verb asked for: {batch.output_tokens / asked:,.0f} output tokens, "
            f"${batch.cost_usd / asked:,.4f}."
        )
    if failures:
        for cid, reason in failures.items():
            console.print(f"  {cid}: {reason}", soft_wrap=True)
        console.print(
            f"[yellow]{stored['failed verbs']:,} verbs failed.[/yellow] Resubmit "
            f"them with 'greek study parts submit {batch.text_id} --retry "
            f"{batch.id}' (prints the estimate; add --yes to submit).",
            soft_wrap=True,
        )


# --- greek study forms --------------------------------------------------------


@study_cmd.group("forms")
def forms_cmd():
    """Dictionary forms and verb constructions, in Message Batches.

    \b
    From Claude Opus 5.5: each noun and adjective lemma of the treebank gets
    its dictionary form as a commentary's vocabulary gives it ("μῆνις, -ιος,
    ἡ", "οὐλόμενος, -η, -ον"), a note on its forms in the work's dialect, and
    an adjective's irregular comparison; each verb lemma gets the cases and
    constructions it takes in the work's author ("+ gen. of the person heard,
    + acc. of the thing"). 25 lemmas to a request (nouns and adjectives
    first), each with the start of its LSJ entry (a verb's short definition)
    and its forms in the text. Forms are kept by dialect (Homeric for the
    Iliad and the Odyssey, Ionic for Herodotus, Attic for Thucydides), so a
    lemma saved in the text's dialect is left out. The reader's word pane
    shows them. Only `submit --yes`
    spends money; run `estimate` (or `submit` without --yes) and approve the
    estimate first. Follow a batch with `greek study batch status | collect`.

    \b
    Example:
      greek study forms estimate 12 --lines 1.1-1.8,1.17-1.34
      greek study forms submit 12 --books 1 --yes
      greek study batch collect msgbatch_...
    """


@dataclass
class _FormsPlan:
    """The lemmas a forms submit would request, in requests, and what was left out."""

    text: TextMeta
    lemmas: List[FormsLemma]
    groups: List[List[FormsLemma]]
    requests: List[dict]
    scope: str
    work: WorkProfile
    skipped_saved: int = 0
    skipped_pending: int = 0


def _line_ranges(conn, text: TextMeta, books, lines_args):
    """(start, end) line numbers of --books or --lines, and their label."""
    if books is not None:
        first, last = _book_range(books)
        lines = [
            line
            for line in get_lines(conn, text.id)
            if first <= (_book_number(line.reference) or 0) <= last
        ]
        if not lines:
            _fail(f"'{text.title}' has no book {books}.")
        return [(lines[0].line_number, lines[-1].line_number)], (
            f"{lines[0].reference}–{lines[-1].reference}"
        )
    ranges = []
    for arg in (a.strip() for value in lines_args for a in value.split(",")):
        if not arg:
            continue
        start, end, error = resolve_line_range(conn, text.id, arg)
        if error:
            _fail(error)
        ranges.append((start, end))
    lines = get_lines(conn, text.id)
    if not ranges:
        ranges = [(lines[0].line_number, lines[-1].line_number)]
    refs = {line.line_number: line.reference for line in lines}
    return ranges, ", ".join(f"{refs[a]}–{refs[b]}" for a, b in ranges)


def _forms_lemmas(conn, text_id: int, lemmas) -> List[FormsLemma]:
    """
    The request's view of (lemma, pos) pairs: LSJ's start (a verb's short
    definition) and the forms seen.
    """
    entries = get_lemma_entries(conn, [lemma for lemma, _ in lemmas], with_html=True)
    seen = lemma_forms_seen(conn, text_id, [lemma for lemma, _ in lemmas], FORMS_SHOWN)
    result = []
    for lemma, pos in lemmas:
        entry = entries.get(lemma)
        lexicon = entry.entry if entry else None
        verb = pos == "verb"
        result.append(
            FormsLemma(
                lemma=lemma,
                pos=pos,
                lsj=lsj_start(lexicon.entry_html) if lexicon and not verb else "",
                identification=(entry.model_def or "") if entry else "",
                forms=seen.get(lemma, []),
                lsj_headword=lexicon.headword
                if lexicon and not verb and plain(lexicon.headword) != plain(lemma)
                else "",
                meaning=lexicon.short_def if lexicon and verb else "",
            )
        )
    return result


def _text_lemmas(conn, text_id: int) -> Dict[str, str]:
    """Every noun, adjective and verb lemma of a text, with its usual part of speech."""
    last = get_lines(conn, text_id)[-1].line_number
    return dict(lemmas_with_pos(conn, text_id, [(1, last)]))


def _forms_plan(conn, text_id, books, lines_args, retry, redo) -> _FormsPlan:
    """Choose the lemmas to request, skip those already done, and build requests."""
    if sum(bool(x) for x in (books, lines_args, retry)) > 1:
        _fail("Use only one of --books, --lines and --retry.")
    text = get_text(conn, text_id)
    if text is None:
        _fail(f"No text with ID {text_id}.")
    if not list_tb_sentences(conn, text_id):
        _fail(
            f"'{text.title}' has no treebank data; run "
            "'greek study import-treebank' first."
        )
    if retry is not None:
        batch = get_study_batch(conn, retry)
        if batch is None or batch.kind != "forms":
            _fail(f"No dictionary-forms batch {retry}.")
        if batch.text_id != text_id:
            _fail(f"Batch {retry} is for text {batch.text_id}, not {text_id}.")
        asked = [lemma for r in list_lemma_requests(conn, retry) for lemma in r.lemmas]
        pos = _text_lemmas(conn, text_id)
        lemmas = [(lemma, pos[lemma]) for lemma in asked if lemma in pos]
        scope = f"{text.title}, retry of {retry}"
    else:
        ranges, label = _line_ranges(conn, text, books, lines_args)
        lemmas = lemmas_with_pos(conn, text_id, ranges)
        scope = f"{text.title} {label}"
    # nouns and adjectives first, so most requests ask for one kind
    lemmas.sort(key=lambda pair: pair[1] == "verb")
    if not lemmas:
        _fail("No nouns, adjectives or verbs to request.")

    work = _work(conn, text_id)
    plan = _FormsPlan(text=text, lemmas=[], groups=[], requests=[], scope="", work=work)
    if not redo:
        saved = lemmas_with_forms(
            conn, [lemma for lemma, _ in lemmas], MODEL, work.dialect
        )
        waiting = pending_forms_lemmas(conn, work.dialect, MODEL)
        kept = []
        for lemma, pos in lemmas:
            if lemma in saved:
                plan.skipped_saved += 1
            elif lemma in waiting:
                plan.skipped_pending += 1
            else:
                kept.append((lemma, pos))
        lemmas = kept
    plan.lemmas = _forms_lemmas(conn, text_id, lemmas)
    plan.groups = group_lemmas(plan.lemmas)
    plan.requests = [
        build_forms_request(work, forms_custom_id(text_id, n), group)
        for n, group in enumerate(plan.groups, 1)
    ]
    kinds = Counter(item.pos for item in plan.lemmas)
    plan.scope = (
        f"{scope}: dictionary forms of {kinds['noun'] + kinds['adjective']:,} "
        f"lemmas ({kinds['noun']:,} nouns, {kinds['adjective']:,} adjectives) "
        f"and constructions of {kinds['verb']:,} verbs ({DIALECTS[work.dialect]})"
    )
    return plan


def _output_per_lemma(conn, override: Optional[float]):
    """Output tokens per lemma for estimates, and where the number comes from."""
    if override is not None:
        return override, "given with --output-per-lemma"
    measured = measured_output_per_lemma(conn, MODEL)
    if measured is not None:
        return measured, "measured over collected batches"
    return DEFAULT_OUTPUT_PER_LEMMA, "the plan's estimate until a batch is collected"


def _print_forms_estimate(plan: _FormsPlan, per_lemma: float, basis: str):
    est = estimate_forms(plan.requests, len(plan.lemmas), per_lemma)
    table = Table(title=f"Forms batch: {plan.scope} (ID {plan.text.id})")
    table.add_column("", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Batch cost", justify="right")
    table.add_row(
        f"Requests ({LEMMAS_PER_REQUEST} lemmas each)", f"{est.requests:,}", ""
    )
    table.add_row("Lemmas", f"{est.lemmas:,}", "")
    table.add_row("  verbs", f"{sum(x.pos == 'verb' for x in plan.lemmas):,}", "")
    table.add_row(
        "  without an LSJ entry",
        f"{sum(not (x.lsj or x.meaning) for x in plan.lemmas):,}",
        "",
    )
    table.add_row(
        "Input tokens (estimate)", f"{est.input_tokens:,}", f"${est.input_cost:,.2f}"
    )
    table.add_row(
        f"Output tokens (estimate, {per_lemma:.0f} per lemma)",
        f"{est.output_tokens:,}",
        f"${est.output_cost:,.2f}",
    )
    table.add_row("Total", "", f"[bold]${est.cost:,.2f}[/bold]")
    console.print(table)
    console.print(
        f"[dim]Model {MODEL}, effort {EFFORT}, max_tokens {MAX_TOKENS:,}. Input is "
        "estimated from characters, all at the uncached price; output per lemma "
        f"(thinking included) is {basis}.[/dim]"
    )
    if plan.skipped_saved or plan.skipped_pending:
        console.print(
            f"Left out: {plan.skipped_saved:,} lemmas with a saved "
            f"{DIALECTS[plan.work.dialect]} form or construction by {MODEL}, "
            f"{plan.skipped_pending:,} waiting in a batch not yet collected "
            "(--redo includes them)."
        )
    return est


def _forms_options(f):
    f = click.option(
        "--retry",
        metavar="BATCH_ID",
        help="Request the lemmas of a collected forms batch that have no form yet",
    )(f)
    f = click.option(
        "--lines",
        "lines_args",
        multiple=True,
        help="Lemmas of these lines, e.g. 1.1-1.8,1.17-1.34 (or repeat --lines)",
    )(f)
    f = click.option("--books", help="A book or range of books, e.g. 1 or 2-24")(f)
    f = click.option(
        "--redo",
        is_flag=True,
        help="Include lemmas with a saved form or waiting in an uncollected batch",
    )(f)
    f = click.option(
        "--output-per-lemma",
        type=float,
        default=None,
        help="Output tokens per lemma for the estimate "
        f"(default: measured, else {DEFAULT_OUTPUT_PER_LEMMA:.0f})",
    )(f)
    return f


@forms_cmd.command("estimate")
@click.argument("text_id", type=int)
@_forms_options
@click.option("--show", is_flag=True, help="Print the requests' user messages")
def forms_estimate_cmd(text_id, books, lines_args, retry, redo, output_per_lemma, show):
    """Estimate a forms batch's tokens and cost locally (no API call).

    \b
    Without --books, --lines or --retry, every noun, adjective and verb of
    the text. Lemmas with a saved form or construction, or waiting in a
    batch not yet collected, are left out.
    """
    conn = get_connection()
    plan = _forms_plan(conn, text_id, books, lines_args, retry, redo)
    if not plan.requests:
        console.print("Nothing to request: every lemma has a form or is waiting.")
        return
    if show:
        _show(plan.requests)
    _print_forms_estimate(plan, *_output_per_lemma(conn, output_per_lemma))


@forms_cmd.command("submit")
@click.argument("text_id", type=int)
@_forms_options
@click.option(
    "--yes", is_flag=True, help="Submit (spends money; approve the estimate first)"
)
def forms_submit_cmd(text_id, books, lines_args, retry, redo, output_per_lemma, yes):
    """Submit a forms batch: prints its estimate, and submits only with --yes."""
    conn = get_connection()
    plan = _forms_plan(conn, text_id, books, lines_args, retry, redo)
    if not plan.requests:
        console.print("Nothing to request: every lemma has a form or is waiting.")
        return
    est = _print_forms_estimate(plan, *_output_per_lemma(conn, output_per_lemma))
    if not yes:
        console.print(
            f"[yellow]Nothing submitted.[/yellow] This batch would cost about "
            f"${est.cost:,.2f}. If you approve the estimate, rerun with --yes."
        )
        return

    import anthropic

    try:
        client = batch_client()
        batch = client.messages.batches.create(requests=plan.requests)
    except RuntimeError as e:
        _fail(f"Error: {e}")
    except anthropic.APIError as e:
        _fail(f"Error: the batch was not submitted: {e}")
    save_submitted_forms_batch(
        conn,
        StudyBatch(
            id=batch.id,
            text_id=text_id,
            scope=plan.scope,
            model=MODEL,
            status=batch.processing_status,
            request_count=len(plan.requests),
            kind="forms",
            dialect=plan.work.dialect,
        ),
        [
            LemmaRequest(
                batch_id=batch.id,
                custom_id=r["custom_id"],
                lemmas=[item.lemma for item in group],
            )
            for r, group in zip(plan.requests, plan.groups)
        ],
    )
    console.print(
        f"[green]Submitted[/green] batch {batch.id}: {len(plan.requests):,} requests "
        f"({plan.scope}).\nMost batches end within an hour (at most 24 hours). "
        f"Check with 'greek study batch status {batch.id}', then run "
        f"'greek study batch collect {batch.id}'.",
        soft_wrap=True,
    )


def _collect_forms(conn, client, batch: StudyBatch):
    """
    Store an ended forms batch's results, lemma by lemma; check each noun's
    article against LSJ's; write the results to data/forms-<batch>.json and
    report. A verb's construction is stored with its lemma as the headword.
    """
    import anthropic

    requests = {r.custom_id: r for r in list_lemma_requests(conn, batch.id)}
    asked = [lemma for r in requests.values() for lemma in r.lemmas]
    pos = _text_lemmas(conn, batch.text_id)
    verbs = {lemma for lemma in asked if pos.get(lemma) == "verb"}
    pairs = [(lemma, pos[lemma]) for lemma in asked if lemma in pos]
    inputs = {x.lemma: x for x in _forms_lemmas(conn, batch.text_id, pairs)}
    entries = get_lemma_entries(conn, asked, with_html=True)
    failures: Dict[str, str] = {}
    stored = Counter()
    review = []
    mismatches = []
    seen = set()
    try:
        for entry in client.messages.batches.results(batch.id):
            request = requests.get(entry.custom_id)
            seen.add(entry.custom_id)
            if request is None:
                console.print(
                    f"[yellow]Skipped an unknown request {entry.custom_id}.[/yellow]"
                )
                continue
            outcome = read_forms_result(entry, request.lemmas, batch.model, verbs)
            if outcome.failure:
                fail_lemma_request(conn, request, outcome.failure, outcome.usage)
                failures[entry.custom_id] = outcome.failure
                stored["failed lemmas"] += len(request.lemmas)
                continue
            forms = [
                LemmaForms(
                    lemma=lemma,
                    model_used=batch.model,
                    pos=pos.get(lemma, ""),
                    headword=out.headword if lemma not in verbs else lemma,
                    forms=out.forms if lemma not in verbs else "",
                    comparison=out.comparison if lemma not in verbs else "",
                    construction=out.construction
                    if lemma in verbs
                    else out.construction or None,
                    dialect=batch.dialect or "homeric",
                )
                for lemma, out in outcome.lemmas.items()
            ]
            save_forms_result(conn, forms, request, outcome.summary, outcome.usage)
            stored["lemmas"] += len(forms)
            stored["constructions"] += sum(f.construction is not None for f in forms)
            stored["failed lemmas"] += len(outcome.failed)
            if outcome.failed:
                failures[entry.custom_id] = outcome.summary
            else:
                stored["requests"] += 1
            for f in forms:
                lsj = entries.get(f.lemma)
                lsj_gen = (
                    lsj_article(lsj.entry.entry_html) if lsj and lsj.entry else None
                )
                given = headword_articles(f.headword)
                check = "no article in LSJ"
                if f.pos == "noun" and lsj_gen and given:
                    # a noun of both genders (κύων, ὁ, ἡ) agrees with either
                    check = "agrees" if lsj_gen in given else "differs"
                elif f.pos == "noun" and lsj_gen:
                    check = "no article in the headword"
                stored[f"gender {check}"] += f.pos == "noun"
                if check == "differs":
                    mismatches.append((f.lemma, f.headword, lsj_gen))
                item = inputs.get(f.lemma)
                review.append(
                    {
                        "lemma": f.lemma,
                        "pos": f.pos,
                        "lsj": item.lsj if item else "",
                        "identification": item.identification if item else "",
                        "seen": item.forms if item else [],
                        "headword": f.headword,
                        "forms": f.forms,
                        "comparison": f.comparison,
                        "meaning": item.meaning if item else "",
                        "construction": f.construction,
                        "lsj_article": lsj_gen,
                        "gender_check": check if f.pos == "noun" else None,
                    }
                )
    except anthropic.APIError as e:
        _fail(f"Error reading the results of {batch.id}: {e}. Collect again later.")
    for cid in requests.keys() - seen:
        failures[cid] = "no result returned"
        fail_lemma_request(conn, requests[cid], "no result returned")
        stored["failed lemmas"] += len(requests[cid].lemmas)

    batch = finish_collect(conn, batch.id)
    order = {lemma: i for i, lemma in enumerate(asked)}
    review.sort(key=lambda item: order.get(item["lemma"], 0))
    review_path = _data_dir() / f"forms-{batch.id}.json"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(
        json.dumps(
            {"batch": batch.id, "scope": batch.scope, "lemmas": review},
            ensure_ascii=False,
            indent=1,
        )
    )
    table = Table(title=f"Collected {batch.id}: {batch.scope}")
    table.add_column("", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Requests fully stored", f"{stored['requests']:,}")
    table.add_row("Lemmas stored", f"{stored['lemmas']:,}")
    table.add_row("  with a construction", f"{stored['constructions']:,}")
    table.add_row("Lemmas failed", f"{stored['failed lemmas']:,}")
    table.add_row("Nouns whose article agrees with LSJ", f"{stored['gender agrees']:,}")
    table.add_row(
        "Nouns whose article differs from LSJ", f"{stored['gender differs']:,}"
    )
    table.add_row(
        "Nouns without an article to compare",
        f"{stored['gender no article in LSJ'] + stored['gender no article in the headword']:,}",
    )
    console.print(table)
    for lemma, headword, lsj_gen in mismatches:
        console.print(f"  {lemma}: {headword} (LSJ {lsj_gen})", soft_wrap=True)
    console.print(
        f"Tokens: {batch.input_tokens:,} input, {batch.cache_write_tokens:,} cache "
        f"write, {batch.cache_read_tokens:,} cache read, {batch.output_tokens:,} "
        f"output. Cost at batch prices: [bold]${batch.cost_usd:,.2f}[/bold]."
    )
    if asked:
        console.print(
            f"Per lemma asked for: {batch.output_tokens / len(asked):,.0f} output "
            f"tokens, ${batch.cost_usd / len(asked):,.4f}."
        )
    console.print(f"Results for review: {review_path}", soft_wrap=True)
    if failures:
        for cid, reason in failures.items():
            console.print(f"  {cid}: {reason}", soft_wrap=True)
        console.print(
            f"[yellow]{stored['failed lemmas']:,} lemmas failed.[/yellow] Resubmit "
            f"them with 'greek study forms submit {batch.text_id} --retry "
            f"{batch.id}' (prints the estimate; add --yes to submit).",
            soft_wrap=True,
        )


# --- greek study uses ---------------------------------------------------------


@study_cmd.group("uses")
def uses_cmd():
    """How words work in their sentence, from Claude Opus 5.5 in batches.

    \b
    A use label for the words whose use a reader has to work out ("dative
    of disadvantage", "optative of wish", "circumstantial participle:
    causal"), chosen from the treebank: genitives and datives not after a
    preposition, adverbial accusatives, participles, infinitives,
    subjunctives, optatives and imperatives. The model may add a label for
    another word whose use a reader could miss. One request per translation
    unit with saved study data: its lines, saved fluent translation, and
    every word with its parse, saved gloss and treebank role, the words to
    label marked. Only `submit --yes` spends money; run `estimate` (or
    `submit` without --yes) and approve the estimate first. Follow a batch
    with `greek study batch status | collect`.

    \b
    A --pilot batch stores nothing: collect writes its labels to
    data/uses-<batch>.json for review, and 'greek study batch collect
    <batch> --store' stores them once its effort is chosen (results stay
    downloadable for 29 days).

    \b
    Example:
      greek study uses estimate 12 --lines 1.1-1.8 --lines 1.43-1.52 --pilot
      greek study uses submit 12 --books 1-7 --effort high --yes
      greek study batch collect msgbatch_...
    """


@dataclass
class _UsesPlan(_Plan):
    """A uses plan: word_counts are the words to label, as requests record them."""

    words: int = 0  # every word of the requested units
    skipped_empty: int = 0  # units without a word to label
    kinds: Counter = field(default_factory=Counter)


@dataclass
class _UsesText:
    """A text's lines, word parses and treebank trees, for its units' words."""

    lines: Dict[int, Line]
    parses: Dict[int, List[WordParse]]
    trees: Dict[int, SentenceSyntax]

    automatic: set = field(default_factory=set)

    def words(self, first: int, last: int):
        unit_lines = [self.lines[n] for n in range(first, last + 1) if n in self.lines]
        parses = [p for line in unit_lines for p in self.parses[line.line_number]]
        return unit_lines, use_words(unit_lines, parses, self.trees, self.automatic)


def _uses_text(
    conn, text_id: int, lines: Dict[int, Line], spans, automatic=frozenset()
) -> _UsesText:
    """
    The parses of a text and the trees of the sentences in the spans' lines;
    automatic is the sentences GLAUx's parser annotated, marked in requests.
    """
    parses = _parses_by_line(list_word_parses(conn, text_id))
    sentences = {
        p.sentence_id
        for first, last in spans
        for n in range(first, last + 1)
        for p in parses[n]
        if p.sentence_id is not None
    }
    trees = text_syntax(
        list_tb_nodes(conn, text_id, sentences),
        [p for ps in parses.values() for p in ps if p.sentence_id in sentences],
        lines,
    )
    return _UsesText(lines, parses, trees, set(automatic))


def _uses_plan(
    conn, text_id, books, lines_args, retry, redo, effort, pilot
) -> _UsesPlan:
    """Choose the units whose words to label, skip those done, and build requests."""
    chosen = _select_units(conn, text_id, books, lines_args, retry)
    text, by_number, units = chosen.text, chosen.by_number, chosen.units
    scope = f"{chosen.scope}: uses, effort {effort}" + (", pilot" if pilot else "")
    plan = _UsesPlan(
        text=text, units=[], requests=[], word_counts=[], scope=scope, work=chosen.work
    )
    labelled, waiting = set(), []
    if not redo:
        labelled = labelled_words(conn, text_id, MODEL)
        waiting = pending_ranges(
            conn, text_id, MODEL, "uses", pilot, effort if pilot else None
        )
    source = _uses_text(
        conn,
        text_id,
        by_number,
        [(u.first_line, u.last_line) for u in units],
        chosen.automatic,
    )
    for u in units:
        unit_lines, words = source.words(u.first_line, u.last_line)
        marked = [w for w in words if w.target]
        if not marked:
            plan.skipped_empty += 1
            continue
        if not redo:
            if all((w.line_number, w.token_index) in labelled for w in marked):
                plan.skipped_saved += 1
                continue
            if any(u.first_line <= b and u.last_line >= a for a, b in waiting):
                plan.skipped_pending += 1
                continue
        saved = {
            (w.line_number, w.token_index): w
            for w in list_word_study(conn, text_id, u.first_line, u.last_line, MODEL)
        }
        fluent = [
            t.fluent
            for t in list_translation_units(
                conn, text_id, u.first_line, u.last_line, MODEL
            )
            if (t.first_line, t.last_line) == (u.first_line, u.last_line)
        ]
        if not fluent or any(
            (w.line_number, w.token_index) not in saved for w in words
        ):
            plan.skipped_unsaved += 1
            continue
        for w in words:
            w.gloss = saved[(w.line_number, w.token_index)].gloss
        plan.units.append(u)
        plan.requests.append(
            build_uses_request(chosen.work, u, unit_lines, fluent[0], words, effort)
        )
        plan.word_counts.append(len(marked))
        plan.words += len(words)
        plan.line_count += len(unit_lines)
        plan.kinds.update(w.target for w in marked)
        if len(words) > plan.largest_words:
            plan.largest, plan.largest_words = u, len(words)
    return plan


def _output_per_label(conn, override: Optional[float], effort: str):
    """Output tokens per label for estimates, and where the number comes from."""
    if override is not None:
        return override, "given with --output-per-label"
    measured = measured_output_per_word(conn, MODEL, "uses", effort)
    if measured is not None:
        return measured, f"measured over collected batches at effort {effort}"
    return default_output_per_label(effort), f"the plan's estimate at effort {effort}"


def _print_uses_estimate(plan: _UsesPlan, per_label: float, basis: str, effort):
    labels = sum(plan.word_counts)
    est = estimate(plan.requests, plan.line_count, labels, per_label)
    table = Table(title=f"Uses batch: {plan.scope} (ID {plan.text.id})")
    table.add_column("", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Batch cost", justify="right")
    table.add_row("Requests (translation units)", f"{est.requests:,}", "")
    table.add_row(f"{plan.work.unit_name.title()}s", f"{est.lines:,}", "")
    table.add_row("Words", f"{plan.words:,}", "")
    table.add_row("Words to label", f"{labels:,}", "")
    for kind in KINDS:
        if plan.kinds[kind]:
            table.add_row(f"  {kind}", f"{plan.kinds[kind]:,}", "")
    table.add_row(
        "Input tokens (estimate)", f"{est.input_tokens:,}", f"${est.input_cost:,.2f}"
    )
    table.add_row(
        f"Output tokens (estimate, {per_label:.0f} per label)",
        f"{est.output_tokens:,}",
        f"${est.output_cost:,.2f}",
    )
    table.add_row("Total", "", f"[bold]${est.cost:,.2f}[/bold]")
    console.print(table)
    console.print(
        f"[dim]Model {MODEL}, effort {effort}, max_tokens {MAX_TOKENS:,}. Input is "
        "estimated from characters, all at the uncached price; output per label "
        f"(thinking included) is {basis}. Labels the model adds for unmarked "
        "words are extra.[/dim]"
    )
    if plan.skipped_saved or plan.skipped_pending:
        console.print(
            f"Left out: {plan.skipped_saved:,} units whose words to label all have "
            f"a label by {MODEL}, {plan.skipped_pending:,} waiting in a batch not "
            "yet collected (--redo includes them)."
        )
    if plan.skipped_empty:
        console.print(f"Left out: {plan.skipped_empty:,} units with no word to label.")
    _print_unsaved(plan)
    return est


def _uses_options(f):
    f = click.option(
        "--retry",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Request the ids listed in a file of failed requests",
    )(f)
    f = click.option(
        "--lines",
        "lines_args",
        multiple=True,
        help="Units overlapping these lines, e.g. 1.1-1.8 (repeat for more ranges)",
    )(f)
    f = click.option("--books", help="A book or range of books, e.g. 1 or 1-7")(f)
    f = click.option(
        "--redo",
        is_flag=True,
        help="Include units already labelled or waiting in an uncollected batch",
    )(f)
    f = click.option(
        "--effort",
        type=click.Choice(EFFORTS),
        default=EFFORT,
        show_default=True,
        help="Opus 5.5's effort (thinking depth)",
    )(f)
    f = click.option(
        "--pilot",
        is_flag=True,
        help="Store nothing: collect writes the labels to a review file",
    )(f)
    f = click.option(
        "--output-per-label",
        type=float,
        default=None,
        help="Output tokens per label for the estimate "
        "(default: measured at this effort, else the plan's estimate)",
    )(f)
    return f


def _uses_estimate(
    conn, text_id, books, lines_args, retry, redo, effort, pilot, output_per_label
):
    """The plan for a uses batch, with its estimate printed (None if empty)."""
    plan = _uses_plan(conn, text_id, books, lines_args, retry, redo, effort, pilot)
    if not plan.requests:
        console.print(
            "Nothing to request: every unit is labelled, waiting in a batch, or "
            "has no word to label."
        )
        _print_unsaved(plan)
        return plan, None
    per_label, basis = _output_per_label(conn, output_per_label, effort)
    return plan, _print_uses_estimate(plan, per_label, basis, effort)


@uses_cmd.command("estimate")
@click.argument("text_id", type=int)
@_uses_options
@click.option("--show", is_flag=True, help="Print the requests' user messages")
def uses_estimate_cmd(
    text_id, books, lines_args, retry, redo, effort, pilot, output_per_label, show
):
    """Estimate a uses batch's tokens and cost locally (no API call).

    \b
    Without --books, --lines or --retry, the whole text. Units whose words
    to label all have a label, or waiting in a uses batch not yet collected,
    are left out, as are units without saved study data.
    """
    conn = get_connection()
    plan, _ = _uses_estimate(
        conn, text_id, books, lines_args, retry, redo, effort, pilot, output_per_label
    )
    if show:
        for r in plan.requests:
            console.print(f"[bold]{r['custom_id']}[/bold]", soft_wrap=True)
            console.print(r["params"]["messages"][0]["content"], soft_wrap=True)
            console.print()


@uses_cmd.command("submit")
@click.argument("text_id", type=int)
@_uses_options
@click.option(
    "--yes", is_flag=True, help="Submit (spends money; approve the estimate first)"
)
def uses_submit_cmd(
    text_id, books, lines_args, retry, redo, effort, pilot, output_per_label, yes
):
    """Submit a uses batch: prints its estimate, and submits only with --yes.

    \b
    Without --books, --lines or --retry, the whole text. --retry FILE
    resubmits the requests a collect listed as failed.
    """
    conn = get_connection()
    plan, est = _uses_estimate(
        conn, text_id, books, lines_args, retry, redo, effort, pilot, output_per_label
    )
    if est is not None:
        _submit_units(conn, text_id, plan, est, yes, "uses", effort, pilot)


def _collect_uses(
    conn, client, batch: StudyBatch, failed_file: Optional[Path], store: bool
):
    """
    Check an ended uses batch's labels against its units, store them (a
    pilot's only with store), write them to data/uses-<batch>.json, and
    report.
    """
    import anthropic

    text_id = batch.text_id
    store = store or not batch.pilot
    requests = {r.custom_id: r for r in list_study_requests(conn, batch.id)}
    lines = {line.line_number: line for line in get_lines(conn, text_id)}
    source = _uses_text(
        conn, text_id, lines, [(r.first_line, r.last_line) for r in requests.values()]
    )
    failures: Dict[str, str] = {}
    review = []
    stored = Counter()
    lengths: List[int] = []
    seen = set()
    try:
        for entry in client.messages.batches.results(batch.id):
            request = requests.get(entry.custom_id)
            seen.add(entry.custom_id)
            if request is None:
                console.print(
                    f"[yellow]Skipped an unknown request {entry.custom_id}.[/yellow]"
                )
                continue
            first, last = request.first_line, request.last_line
            unit_lines, words = source.words(first, last)
            outcome = read_uses_result(entry, words, batch.model)
            if len(unit_lines) != last - first + 1:
                outcome.failure = "lines of the request are missing"
            elif sum(1 for w in words if w.target) != request.word_count:
                outcome.failure = "the words to label changed since it was submitted"
            if outcome.failure:
                failures[entry.custom_id] = outcome.failure
                fail_study_request(
                    conn, batch.id, entry.custom_id, outcome.failure, outcome.usage
                )
                continue
            uses = [
                WordUse(
                    text_id,
                    words[u.number - 1].line_number,
                    words[u.number - 1].token_index,
                    batch.model,
                    u.label,
                    batch.effort,
                    u.extra,
                )
                for u in outcome.uses
            ]
            if store:
                save_uses_result(
                    conn,
                    text_id,
                    batch.model,
                    first,
                    last,
                    uses,
                    request,
                    outcome.usage,
                )
            else:
                succeed_study_request(conn, batch.id, entry.custom_id, outcome.usage)
            gloss = {
                (s.line_number, s.token_index): s.gloss
                for s in list_word_study(conn, text_id, first, last, batch.model)
            }
            review.append(
                {
                    "custom_id": entry.custom_id,
                    "lines": [line.reference for line in unit_lines],
                    "uses": [
                        {
                            "n": u.number,
                            "ref": w.reference,
                            "word": w.form,
                            "lemma": " + ".join(p.lemma or "?" for p in w.parts),
                            "parse": " + ".join(parse_label(p.postag) for p in w.parts),
                            "gloss": gloss.get((w.line_number, w.token_index)),
                            "role": w.role,
                            "kind": w.target,
                            "extra": u.extra,
                            "use": u.label,
                        }
                        for u in outcome.uses
                        for w in [words[u.number - 1]]
                    ],
                }
            )
            lengths += [len(u.label) for u in outcome.uses]
            stored.update(
                {
                    "requests": 1,
                    "words": len(words),
                    "labels": sum(1 for u in outcome.uses if not u.extra),
                    "extras": sum(1 for u in outcome.uses if u.extra),
                }
            )
    except anthropic.APIError as e:
        _fail(f"Error reading the results of {batch.id}: {e}. Collect again later.")
    for cid in requests.keys() - seen:
        failures[cid] = "no result returned"
        fail_study_request(conn, batch.id, cid, "no result returned")

    batch = finish_collect(conn, batch.id)
    review_path = _data_dir() / f"uses-{batch.id}.json"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review.sort(key=lambda unit: _line_order(unit["custom_id"]))
    review_path.write_text(
        json.dumps(
            {
                "batch": batch.id,
                "scope": batch.scope,
                "effort": batch.effort,
                "pilot": batch.pilot,
                "stored": store,
                "units": review,
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    _print_uses_collect(batch, stored, lengths, failures, store)
    console.print(
        f"Labels: {review_path}" + ("." if store else " (a pilot: none were stored)."),
        soft_wrap=True,
    )
    if failures:
        path = failed_file or _data_dir() / f"study-failed-{batch.id}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(f"{cid}\n" for cid in sorted(failures, key=_line_order))
        )
        console.print(
            f"[yellow]{len(failures):,} requests failed[/yellow]; their ids are in "
            f"{path}.\nResubmit them with 'greek study uses submit {text_id} "
            f"--retry {path} --effort {batch.effort}"
            + (" --pilot" if batch.pilot else "")
            + "' (prints the estimate; add --yes to submit).",
            soft_wrap=True,
        )


def _print_uses_collect(batch, stored: Counter, lengths, failures, store: bool):
    table = Table(title=f"Collected {batch.id}: {batch.scope}")
    table.add_column("", style="bold")
    table.add_column("Count", justify="right")
    done = "Requests stored" if store else "Requests checked (pilot)"
    table.add_row(done, f"{stored['requests']:,}")
    table.add_row("Words", f"{stored['words']:,}")
    table.add_row("Marked words labelled", f"{stored['labels']:,}")
    table.add_row("Extra labels (unmarked words)", f"{stored['extras']:,}")
    if lengths:
        ordered = sorted(lengths)
        table.add_row(
            "Label length (characters: mean / median / longest)",
            f"{sum(lengths) / len(lengths):,.0f} / {ordered[len(ordered) // 2]:,} "
            f"/ {ordered[-1]:,}",
        )
    table.add_row("Requests failed", f"{len(failures):,}")
    for reason, n in Counter(
        re.sub(r"\(.*\)$", "", f).strip() if f.startswith("refusal") else f
        for f in failures.values()
    ).most_common():
        table.add_row(f"  {reason}", f"{n:,}")
    console.print(table)
    console.print(
        f"Tokens: {batch.input_tokens:,} input, {batch.cache_write_tokens:,} cache "
        f"write, {batch.cache_read_tokens:,} cache read, {batch.output_tokens:,} "
        f"output. Cost at batch prices: [bold]${batch.cost_usd:,.2f}[/bold] "
        f"(effort {batch.effort})."
    )
    labels = stored["labels"] + stored["extras"]
    if labels:
        console.print(
            f"Per label: {batch.output_tokens / labels:,.1f} output tokens, "
            f"${1000 * batch.cost_usd / labels:,.2f} per 1,000 labels; "
            f"${1000 * batch.cost_usd / stored['words']:,.2f} per 1,000 words "
            "(failed requests included)."
        )


# --- greek study coverage and remarks ------------------------------------------

_MISSING_SHOWN = 10  # missing items listed per kind


@dataclass
class _BookCoverage:
    """One book's share of a text's study data (counts of saved over expected)."""

    words: int = 0
    glossed: int = 0
    lines: int = 0
    literal: int = 0
    units: int = 0
    units_saved: int = 0
    marked: int = 0
    labelled: int = 0
    remarks: int = 0

    def add(self, other: "_BookCoverage") -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(self, name) + getattr(other, name))


def _share(saved: int, total: int) -> str:
    """'4,027 / 4,027' in green when complete, yellow when not."""
    color = "green" if saved == total else "yellow"
    return f"[{color}]{saved:,} / {total:,}[/{color}]"


def _first(items: List[str], total: int) -> str:
    shown = ", ".join(items[:_MISSING_SHOWN])
    more = total - min(total, _MISSING_SHOWN)
    return shown + (f" and {more:,} more" if more else "")


@study_cmd.command("coverage")
@click.argument("text_id", type=int)
def coverage_cmd(text_id):
    """What a text's study data covers, book by book, and what is missing.

    \b
    Reads saved data only. Per book: the words with a gloss and a note, the
    lines with a literal translation, the translation units with all their
    results (as a study batch counts them), the words to label that have a
    use label, and the model's parse_check remarks. For the whole text: the
    lemmas with a dictionary form or construction and the verbs with
    principal parts in the text's dialect, and the lemmas with an LSJ entry
    (or a model's identification). Then the first missing items of each
    kind; an item waiting in a batch not yet collected counts as missing.

    \b
    Example:
      greek study coverage 12
    """
    conn = get_connection()
    chosen = _select_units(conn, text_id, None, (), None)
    text, by_number, units, work = (
        chosen.text,
        chosen.by_number,
        chosen.units,
        chosen.work,
    )
    lines = list(by_number.values())
    first, last = lines[0].line_number, lines[-1].line_number
    book_of = {n: _book_number(line.reference) or 0 for n, line in by_number.items()}
    books: Dict[int, _BookCoverage] = defaultdict(_BookCoverage)

    parses = list_word_parses(conn, text_id)
    words = study_words(lines, parses)
    studied = {
        (w.line_number, w.token_index): w
        for w in list_word_study(conn, text_id, first, last, MODEL)
    }
    for w in words:
        book = books[book_of[w.line_number]]
        book.words += 1
        found = studied.get((w.line_number, w.token_index))
        book.glossed += found is not None
        book.remarks += bool(found and found.parse_check)

    literal = {
        t.line_number for t in list_line_translations(conn, text_id, first, last, MODEL)
    }
    saved = covered_units(conn, units, _words_per_line(lines, parses), MODEL)
    source = _uses_text(
        conn,
        text_id,
        by_number,
        [(u.first_line, u.last_line) for u in units],
        chosen.automatic,
    )
    labelled = labelled_words(conn, text_id, MODEL)
    missing_units, unlabelled = [], []
    for u in units:
        book = books[book_of[u.first_line]]
        book.units += 1
        if (u.first_line, u.last_line) in saved:
            book.units_saved += 1
        else:
            missing_units.append(
                f"{by_number[u.first_line].reference}–{by_number[u.last_line].reference}"
            )
        for n in range(u.first_line, u.last_line + 1):
            books[book_of[n]].lines += 1
            books[book_of[n]].literal += n in literal
        for w in source.words(u.first_line, u.last_line)[1]:
            if w.target:
                book = books[book_of[w.line_number]]
                book.marked += 1
                if (w.line_number, w.token_index) in labelled:
                    book.labelled += 1
                else:
                    unlabelled.append(f"{w.reference} {w.form}")

    table = Table(title=f"Study data: {text.title} (ID {text_id}), {MODEL}")
    for name in (
        "Book",
        "Words glossed",
        "Literal lines",
        "Units",
        "Words labelled",
        "Remarks",
    ):
        table.add_column(name, justify="right")
    total = _BookCoverage()
    for number in sorted(books):
        b = books[number]
        total.add(b)
        table.add_row(
            str(number) if number else "–",
            _share(b.glossed, b.words),
            _share(b.literal, b.lines),
            _share(b.units_saved, b.units),
            _share(b.labelled, b.marked),
            f"{b.remarks:,}",
        )
    table.add_row(
        "[bold]All[/bold]",
        _share(total.glossed, total.words),
        _share(total.literal, total.lines),
        _share(total.units_saved, total.units),
        _share(total.labelled, total.marked),
        f"{total.remarks:,}",
    )
    console.print(table)

    pairs = lemmas_with_pos(conn, text_id, [(first, last)])
    with_forms = lemmas_with_forms(
        conn, [lemma for lemma, _ in pairs], MODEL, work.dialect
    )
    no_forms = [lemma for lemma, _ in pairs if lemma not in with_forms]
    verbs = verb_lemmas(conn, text_id)
    with_parts = verbs_with_parts(conn, verbs, MODEL, work.dialect)
    no_parts = [verb for verb in verbs if verb not in with_parts]
    counts = Counter(p.lemma for p in parses if p.lemma and p.postag)
    entries = get_lemma_entries(conn, list(counts), with_html=False)
    defined = [
        lemma
        for lemma in counts
        if lemma in entries and (entries[lemma].entry or entries[lemma].model_def)
    ]
    dictionary = Table(title=f"Dictionary data ({DIALECTS[work.dialect]})")
    dictionary.add_column("", style="bold")
    dictionary.add_column("Saved", justify="right")
    dictionary.add_row(
        "Lemmas with a dictionary form or construction",
        _share(len(pairs) - len(no_forms), len(pairs)),
    )
    dictionary.add_row(
        "Verbs with principal parts", _share(len(verbs) - len(no_parts), len(verbs))
    )
    words_defined = sum(counts[lemma] for lemma in defined)
    dictionary.add_row(
        "Lemmas with an LSJ entry or identification",
        f"{len(defined):,} / {len(counts):,} "
        f"({100 * words_defined / max(1, sum(counts.values())):.1f}% of words)",
    )
    console.print(dictionary)

    old_notes = sum(1 for w in studied.values() if w.note_version < NOTE_VERSION)
    waiting = [
        b
        for b in list_study_batches(conn)
        if b.text_id == text_id and b.collected_at is None
    ]
    missing = [
        ("Units without all their results", missing_units),
        ("Words to label without a label", unlabelled),
        ("Lemmas without a dictionary form or construction", no_forms),
        ("Verbs without principal parts", no_parts),
    ]
    if not any(items for _, items in missing) and not old_notes:
        console.print("[green]Nothing missing.[/green]")
    for label, items in missing:
        if items:
            console.print(
                f"[bold]{label} ({len(items):,}):[/bold] {_first(items, len(items))}"
            )
    if old_notes:
        console.print(
            f"[bold]Words with notes older than version {NOTE_VERSION}:[/bold] "
            f"{old_notes:,} ('greek study notes' rewrites them)."
        )
    if waiting:
        console.print(
            f"{len(waiting):,} batches for this text are not yet collected "
            "('greek study batch status')."
        )


@study_cmd.command("remarks")
@click.argument("text_id", type=int)
@click.option("--books", help="A book or range of books, e.g. 1 or 8-24")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="The report file (default: remarks-<TEXT_ID>.md in the data directory)",
)
def remarks_cmd(text_id, books, out_path):
    """The model's parse_check remarks on the treebank, as a Markdown report.

    \b
    A study request asks the model to check each word's treebank parse and
    to say briefly where it disagrees; the reader's word pane shows the
    remark. The report lists every remark by book and line (the word, the
    treebank's lemma and parse, the remark), after the lemmas remarked on
    most often: a treebank convention the model disputes shows up there
    rather than as an error in one place. Reads saved data only.

    \b
    Example:
      greek study remarks 12 --books 8-24 --out remarks-iliad.md
    """
    conn = get_connection()
    text = get_text(conn, text_id)
    if text is None:
        _fail(f"No text with ID {text_id}.")
    lines = get_lines(conn, text_id)
    if books is not None:
        low, high = _book_range(books)
        lines = [
            line for line in lines if low <= (_book_number(line.reference) or 0) <= high
        ]
        if not lines:
            _fail(f"'{text.title}' has no book {books}.")
    if not lines:
        _fail(f"'{text.title}' has no lines.")
    first, last = lines[0].line_number, lines[-1].line_number
    by_number = {line.line_number: line for line in lines}
    parses = _parses_by_line(list_word_parses(conn, text_id, first, last))
    rows = []
    for w in list_word_study(conn, text_id, first, last, MODEL):
        if not w.parse_check:
            continue
        line = by_number[w.line_number]
        tokens = line.content.split()
        form = tokens[w.token_index] if w.token_index < len(tokens) else ""
        parts = [p for p in parses[w.line_number] if p.token_index == w.token_index]
        rows.append(
            (
                line,
                form.strip(_PUNCT_STRIP),
                " + ".join(p.lemma or "" for p in parts),
                " + ".join(parse_label(p.postag) for p in parts),
                " ".join(w.parse_check.split()),
            )
        )

    scope = f"{lines[0].reference}–{lines[-1].reference}"
    out = out_path or _data_dir() / f"remarks-{text_id}.md"
    by_lemma = Counter(lemma for _, _, lemma, _, _ in rows)
    by_book = Counter(_book_number(line.reference) or 0 for line, *_ in rows)
    report = [
        f"# parse_check remarks: {text.title} {scope}",
        "",
        f"{len(rows):,} remarks by {MODEL} on {text.title}'s treebank parses "
        f"(saved study data, {len(by_number):,} lines). Each says where the model "
        "disagrees with the treebank's lemma or parse; the reader's word pane "
        "shows the same remark.",
        "",
        "## Lemmas remarked on most often",
        "",
        "| Lemma | Remarks |",
        "|---|---|",
        *(f"| {lemma or '–'} | {n:,} |" for lemma, n in by_lemma.most_common(30)),
        "",
    ]
    for number in sorted(by_book):
        report += [
            f"## Book {number}" if number else "## Without a book",
            "",
            "| Line | Word | Treebank | Remark |",
            "|---|---|---|---|",
        ]
        for line, form, lemma, parse, remark in rows:
            if (_book_number(line.reference) or 0) == number:
                cells = [line.reference, form, f"{lemma}, {parse}", remark]
                report.append("| " + " | ".join(_md_cell(c) for c in cells) + " |")
        report.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(report))

    console.print(
        f"{len(rows):,} parse_check remarks in {text.title} {scope}; wrote {out}."
    )
    if by_lemma:
        console.print(
            "Most remarked: "
            + ", ".join(f"{lemma} ({n:,})" for lemma, n in by_lemma.most_common(10))
        )


def _md_cell(value: str) -> str:
    """A value for a Markdown table cell (pipes escaped, on one line)."""
    return " ".join(value.split()).replace("|", "\\|")
