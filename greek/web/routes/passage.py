from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from greek.db.repos.lines import resolve_line_range
from greek.db.repos.texts import get_text
from greek.web.deps import get_db
from greek.web.reader_data import passage_view
from greek.web.templates_config import templates

router = APIRouter()


def _passage_box(request: Request, view, message: str = None):
    """Render the reader's passage box (or a hint when nothing is selected)."""
    return templates.TemplateResponse(
        request, "_passage_box.html", {"passage": view, "message": message}
    )


@router.get("/texts/{text_id}/passage", response_class=HTMLResponse)
def passage_box(request: Request, text_id: int, lines: str = None, db=Depends(get_db)):
    """The passage box for a selection, from saved data."""
    text = get_text(db, text_id)
    if text is None:
        raise HTTPException(status_code=404, detail="Text not found")
    if not lines:
        return _passage_box(request, None)
    start, end, err = resolve_line_range(db, text_id, lines)
    if err:
        return _passage_box(request, None, message=err)
    view = passage_view(db, text, min(start, end), max(start, end))
    if view is None:
        return _passage_box(request, None, message="No lines found for that range.")
    return _passage_box(request, view)
