from dataclasses import dataclass
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from greek.api.study import MODEL as STUDY_MODEL
from greek.db.repos.parses import annotation_counts, get_tb_source
from greek.db.repos.texts import list_texts
from greek.fetch.treebank import WORKS, TreebankWork
from greek.models import TextMeta
from greek.web.deps import get_db
from greek.web.reader_data import TextStudy, text_study
from greek.web.templates_config import templates

router = APIRouter()

# The public repository: the code, and its releases the database
REPOSITORY = "https://github.com/Kenaldo9/ancient-greek-reader"


@dataclass
class AboutText:
    """A text's row in About: its treebank, how much of it is by hand, its study data."""

    text: TextMeta
    work: Optional[TreebankWork]
    hand_share: Optional[int]  # percent of sentences annotated by hand
    study: TextStudy


@router.get("/about", response_class=HTMLResponse)
def about(request: Request, db=Depends(get_db)):
    """What the reader is, where each text's data comes from, and the licences."""
    texts: List[AboutText] = []
    for text in list_texts(db):
        source = get_tb_source(db, text.id)
        counts = annotation_counts(db, text.id)
        total = sum(counts.values())
        texts.append(
            AboutText(
                text,
                WORKS.get(source.work) if source else None,
                round(100 * counts.get("manual", 0) / total) if total else None,
                text_study(db, text),
            )
        )
    return templates.TemplateResponse(
        request,
        "about.html",
        {"texts": texts, "model": STUDY_MODEL, "repository": REPOSITORY},
    )
