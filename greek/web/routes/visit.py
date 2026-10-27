"""/visit: what visit.js asks for once a page has been read a moment."""

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()


@router.get("/visit")
def visit():
    """
    Nothing to send back. On the public site the visitor counts
    (greek/web/visits.py) count the request before it gets here; elsewhere
    it counts for nothing.
    """
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
