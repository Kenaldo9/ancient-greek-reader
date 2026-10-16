from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from greek.api.models import model_label
from greek.db.repos.lines import (
    get_lines,
    line_lengths,
    list_books,
    resolve_line,
    resolve_line_range,
    search_lines,
)
from greek.db.repos.texts import get_text, list_texts
from greek.models import Line
from greek.web.deps import get_db
from greek.display.analysis import _PUNCT_STRIP
from greek.web.formatting import line_unit
from greek.web.reader_data import (
    interlinear_on,
    passage_view,
    ref,
    references,
    saved_principal_parts,
    search_view,
    study_glosses,
    study_word,
    text_study,
)
from greek.web.reading import (
    LEGACY_PAGE_SIZE,
    find_page,
    parse_goto,
    split_parts,
    tokens,
)
from greek.web.templates_config import templates

router = APIRouter()

VERB_POS = {"verb", "participle", "infinitive"}


@dataclass
class LineView:
    line: Line
    ref: str
    words: List[str]
    glosses: Optional[List[str]]  # the study data's, one per word; None without


def _line_views(db, text_id: int, start: int, end: int) -> List[LineView]:
    lines = get_lines(db, text_id, start, end)
    contents = {ln.line_number: ln.content for ln in lines}
    glosses = study_glosses(db, text_id, contents, start, end)
    return [
        LineView(ln, ref(ln), tokens(ln.content), glosses.get(ln.line_number))
        for ln in lines
    ]


def _parts_of(db, text_id: int):
    """split_parts for a span of the text, each span read once."""
    cache = {}

    def parts_of(lo: int, hi: int) -> List[int]:
        if (lo, hi) not in cache:
            cache[(lo, hi)] = split_parts(line_lengths(db, text_id, lo, hi))
        return cache[(lo, hi)]

    return parts_of


def _first_references(db) -> dict:
    return {
        r["text_id"]: r["reference"]
        for r in db.execute(
            "SELECT text_id, reference FROM lines WHERE line_number = 1"
        )
    }


@router.get("/texts/{text_id}/read", response_class=HTMLResponse)
def read(
    request: Request,
    text_id: int,
    lines: str = None,
    at: str = None,
    page: int = None,
    interlinear: bool = None,
    q: str = None,
    hit: str = None,
    goto_error: str = None,
    db=Depends(get_db),
):
    """
    The reader. `q` (even empty) shows search results in the text pane instead
    of the lines; `hit` marks the line a search result was opened at. Without
    `at`, `lines` or `page` it opens at the start: reader.js continues where
    this browser last read (its reading position is in localStorage).
    """
    text = get_text(db, text_id)
    if text is None:
        raise HTTPException(status_code=404, detail="Text not found")

    selection = None
    if lines:
        start, end, err = resolve_line_range(db, text_id, lines)
        if err:
            raise HTTPException(status_code=400, detail=err)
        selection = (min(start, end), max(start, end))

    if at:
        at_line = resolve_line(db, text_id, at)
        if at_line is None:
            raise HTTPException(
                status_code=400, detail=f"'{at}' not found in this text"
            )
    elif page:  # links from before book pages
        at_line = (page - 1) * LEGACY_PAGE_SIZE + 1
    elif selection:
        at_line = selection[0]
    else:
        at_line = 1

    first_refs = _first_references(db)
    unit = line_unit(first_refs.get(text_id))
    books = list_books(db, text_id)
    pg = find_page(books, at_line, text.line_count, _parts_of(db, text_id))
    search = search_view(db, text_id, q) if q is not None else None
    line_views = [] if search else _line_views(db, text_id, pg.start, pg.end)
    refs = references(db, text_id, [at_line, pg.prev_start, pg.next_start])

    passage = None
    if selection:
        passage = passage_view(db, text, selection[0], selection[1])
    # a book without study data has no glosses for Interlinear to show
    page_book = text_study(db, text).book_at(pg.start)
    glossless = page_book is None or page_book.state == "none"

    texts = list_texts(db)
    return templates.TemplateResponse(
        request,
        "reader.html",
        {
            "text": text,
            "texts": texts,
            "units": {t.id: line_unit(first_refs.get(t.id)) for t in texts},
            "unit": unit,
            "books": books,
            "page": pg,
            "lines": line_views,
            "at_line": at_line,
            "refs": refs,
            "passage": passage,
            "search": search,
            "hit_ref": hit,
            "interlinear": interlinear_on(request, interlinear),
            "glossless": glossless,
            "goto_error": goto_error,
        },
    )


@router.get("/texts/{text_id}/word", response_class=HTMLResponse)
def word(request: Request, text_id: int, line: int, i: int, db=Depends(get_db)):
    """
    The word strip for token `i` of line `line`: treebank parsing and LSJ
    entries when the text has them, the study data's gloss and note, and a
    verb's saved principal parts. Reads saved data only.
    """
    text = get_text(db, text_id)
    if text is None:
        raise HTTPException(status_code=404, detail="Text not found")
    found = get_lines(db, text_id, line, line)
    toks = tokens(found[0].content) if found else []
    if not 0 <= i < len(toks):
        raise HTTPException(status_code=404, detail="No such word")

    lemma = pos = verb_link = ""
    verb_form = None  # the treebank's parse of the verb, for its principal part
    study = study_word(db, text, line, i, toks[i])
    if study and study.parsed:
        lemma, pos = study.lemma, study.pos
        verb = next((p for p in study.parts if p.pos in VERB_POS), None)
        verb_link = verb.lemma if verb else ""
        verb_form = verb.morphology if verb else None

    display_word = toks[i].strip(_PUNCT_STRIP)
    line_ref = references(db, text_id, [line])[line]
    gloss = note = source = ""
    missing = None  # in a book without study data, what has none yet
    if study and study.studied:
        gloss, note = study.gloss, study.note
        source = f"Gloss and note · {model_label(study.studied)}"
    else:
        missing = text_study(db, text).missing(line)

    return templates.TemplateResponse(
        request,
        "_word_strip.html",
        {
            "text": text,
            "token": toks[i],
            "display_word": display_word,
            "find_count": search_lines(db, text_id, display_word, limit=0)[1],
            "unit": line_unit(line_ref),
            "line_ref": line_ref,
            "line_number": line,
            "index": i,
            "source": source,
            "gloss": gloss,
            "lemma": lemma,
            "pos": pos,
            "note": note,
            "missing": missing,
            "verb_link": verb_link,
            "principal_parts": saved_principal_parts(
                db, verb_link, verb_form, study.dialect if study else None
            ),
            "study": study,
        },
    )


@router.get("/goto")
def goto(q: str = "", text_id: int = None, at: str = None, db=Depends(get_db)):
    """Jump to 'Iliad 1.42', '1.42' (current text), 'Odyssey 5' (a book) or 'Odyssey'."""

    def fail(message: str):
        if text_id is None:
            return RedirectResponse("/library", status_code=303)
        params = {"goto_error": message, **({"at": at} if at else {})}
        return RedirectResponse(
            f"/texts/{text_id}/read?{urlencode(params)}", status_code=303
        )

    title, wanted = parse_goto(q)
    if title:
        needle = title.lower()
        texts = list_texts(db)
        matches = [t for t in texts if t.title.lower().startswith(needle)] or [
            t
            for t in texts
            if needle in t.title.lower() or needle in (t.author or "").lower()
        ]
        if not matches:
            return fail(f"No text called “{title}”")
        target = matches[0]
    else:
        target = get_text(db, text_id) if text_id is not None else None
        if target is None:
            return fail("Type a reference, e.g. 1.42")

    if wanted is None:
        return RedirectResponse(f"/texts/{target.id}/read", status_code=303)

    line_number = None
    if "." not in wanted:  # a bare number is a book when the text has one
        book = next((b for b in list_books(db, target.id) if b.number == wanted), None)
        line_number = book.line_start if book else None
    if line_number is None:
        line_number = resolve_line(db, target.id, wanted)
    if line_number is None:
        return fail(f"{target.title} has no {wanted}")
    at_ref = references(db, target.id, [line_number])[line_number]
    return RedirectResponse(
        f"/texts/{target.id}/read?{urlencode({'at': at_ref})}", status_code=303
    )


@router.get("/texts/{text_id}/toc", response_class=HTMLResponse)
def toc(request: Request, text_id: int, db=Depends(get_db)):
    text = get_text(db, text_id)
    if text is None:
        raise HTTPException(status_code=404, detail="Text not found")

    db_lines = get_lines(db, text_id)

    sections = []
    current_prefix = None
    current_start = None
    current_start_line = None
    count = 0
    last_ref = None
    last_line = None

    for ln in db_lines:
        if ln.reference:
            prefix = ln.reference.split(".")[0]
        else:
            prefix = ""

        if prefix != current_prefix:
            if current_prefix is not None:
                sections.append(
                    {
                        "book": current_prefix,
                        "ref_start": current_start,
                        "ref_end": last_ref,
                        "line_start": current_start_line,
                        "line_end": last_line,
                        "count": count,
                    }
                )
            current_prefix = prefix
            current_start = ln.reference
            current_start_line = ln.line_number
            count = 0

        last_ref = ln.reference
        last_line = ln.line_number
        count += 1

    if current_prefix is not None:
        sections.append(
            {
                "book": current_prefix,
                "ref_start": current_start,
                "ref_end": last_ref,
                "line_start": current_start_line,
                "line_end": last_line,
                "count": count,
            }
        )

    return templates.TemplateResponse(
        request, "toc.html", {"text": text, "sections": sections}
    )


@router.get("/texts/{text_id}/search")
def search(text_id: int, q: str = "", db=Depends(get_db)):
    """Old search URL: search now happens in the reader's text pane."""
    if get_text(db, text_id) is None:
        raise HTTPException(status_code=404, detail="Text not found")
    return RedirectResponse(
        f"/texts/{text_id}/read?{urlencode({'q': q})}", status_code=303
    )
