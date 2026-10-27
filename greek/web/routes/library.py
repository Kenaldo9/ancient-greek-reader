from dataclasses import dataclass
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from greek.db.repos.lines import get_lines, list_books
from greek.models import Book, Line, TextMeta
from greek.web.deps import get_db
from greek.web.formatting import line_unit
from greek.web.reader_data import TextStudy, text_study, texts_in_order
from greek.web.templates_config import templates

router = APIRouter()


@dataclass
class TextCard:
    text: TextMeta
    books: List[Book]
    unit: str  # "line" (verse) or "section" (prose)
    first_line: Optional[Line]
    study: TextStudy  # what the text has, book by book


@router.get("/library", response_class=HTMLResponse)
def library(request: Request, db=Depends(get_db)):
    """
    The texts, each labelled with what it has and its books marked by their
    study data. Continue buttons take this browser's reading positions
    (positions.js).
    """
    cards = []
    for text in texts_in_order(db):
        first = get_lines(db, text.id, 1, 1)
        first_line = first[0] if first else None
        cards.append(
            TextCard(
                text=text,
                books=list_books(db, text.id),
                unit=line_unit(first_line.reference if first_line else None),
                first_line=first_line,
                study=text_study(db, text),
            )
        )

    return templates.TemplateResponse(request, "library.html", {"cards": cards})
