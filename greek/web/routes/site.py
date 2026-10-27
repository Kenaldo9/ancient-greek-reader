"""robots.txt and the sitemap: what search engines may index."""

from typing import List
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response

from greek.db.repos.texts import list_texts
from greek.web.deps import get_db
from greek.web.reader_data import page_starts, reader_path
from greek.web.templates_config import absolute_url

router = APIRouter()


def _indexed(db) -> List[str]:
    """
    The paths search engines may index: the Library, About, each text's
    contents, and each page of its books where the Library and the contents
    open it (the reader's canonical link names the same).
    """
    paths = ["/library", "/about"]
    for text in list_texts(db):
        paths.append(f"/texts/{text.id}/toc")
        paths += [reader_path(text.id, at) for at in page_starts(db, text.id)]
    return paths


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots(request: Request, db=Depends(get_db)):
    # The reader's pages are allowed one by one ($: the whole URL), and every
    # other reader URL is not: the most specific rule wins (RFC 9309).
    pages = [path for path in _indexed(db) if "/read?" in path]
    return "\n".join(
        [
            "# Search engines may index the Library, About, each text's contents",
            "# and each page of its books, as the sitemap lists them; not the",
            "# word, passage and search panels, nor a page opened partway.",
            "User-agent: *",
            *(f"Allow: {path}$" for path in pages),
            "Disallow: /texts/*/read",
            "Disallow: /texts/*/word",
            "Disallow: /texts/*/passage",
            "Disallow: /texts/*/search",
            "Disallow: /goto",
            "Disallow: /version",
            "Disallow: /visit",
            "",
            f"Sitemap: {absolute_url(request, '/sitemap.xml')}",
            "",
        ]
    )


@router.get("/sitemap.xml")
def sitemap(request: Request, db=Depends(get_db)):
    urls = "".join(
        f"  <url><loc>{escape(absolute_url(request, path))}</loc></url>\n"
        for path in _indexed(db)
    )
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n",
        media_type="application/xml",
    )
