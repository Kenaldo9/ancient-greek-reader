"""
Finished pages kept in memory, for the public site (deploy/README.md). Its
database is the published copy, read-only while the app runs, so a page is
the same bytes every time the same request asks for it. The private reader,
on its writable live database, caches nothing.
"""

from collections import OrderedDict
from typing import List, NamedTuple, Tuple

from starlette.datastructures import Headers
from starlette.requests import Request

from greek.web.reader_data import interlinear_on
from greek.web.templates_config import site_origin

# The sitemap's 114 pages (every page of the four texts, their contents, the
# Library and About) are 5.5 MB gzipped (2026-10-23); an Iliad book page sent
# without gzip is about 690 KB.
PAGE_CACHE_BYTES = 64 * 1024 * 1024

# Static files answer conditional requests themselves (ETag, 304), and
# /version is the deploy's check of the running app
UNCACHED = ("/static/", "/version")


class Page(NamedTuple):
    status: int
    headers: List[Tuple[bytes, bytes]]
    body: bytes

    @property
    def size(self) -> int:
        return len(self.body) + sum(len(k) + len(v) for k, v in self.headers)


class PageCache:
    """
    ASGI middleware that keeps the GET responses answered 200 (pages,
    fragments, robots.txt and the sitemap) and answers the same request with
    them again. A request's key is what a page depends on: the path and
    query, the Interlinear cookie (interlinear_on), the site's scheme and
    host (site_origin, for the canonical link and the sitemap), and whether
    the browser takes gzip. It sits outside the gzip middleware, so a page
    is kept as it was sent: gzipped, for a browser or Cloudflare that takes
    it.
    Up to max_bytes; the least recently used page goes first. A restart
    empties it, and every publish and deploy restarts the public site; the
    outside check (deploy/public/check_site.py), after every deploy and
    hourly, opens every page in the sitemap, with Interlinear off and on,
    which fills it again.
    """

    def __init__(self, app, max_bytes: int = PAGE_CACHE_BYTES):
        self.app = app
        self.max_bytes = max_bytes
        self.size = 0
        self.pages: "OrderedDict[tuple, Page]" = OrderedDict()

    @staticmethod
    def key(scope) -> tuple:
        request = Request(scope)
        return (
            scope["path"],
            scope["query_string"],
            interlinear_on(request),
            site_origin(request),
            # The gzip middleware's own test
            "gzip" in Headers(scope=scope).get("Accept-Encoding", ""),
        )

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "GET"
            or scope["path"].startswith(UNCACHED)
        ):
            return await self.app(scope, receive, send)

        key = self.key(scope)
        page = self.pages.get(key)
        if page is not None:
            self.pages.move_to_end(key)
            await send(
                {
                    "type": "http.response.start",
                    "status": page.status,
                    "headers": list(page.headers),
                }
            )
            await send({"type": "http.response.body", "body": page.body})
            return

        status = 0
        headers: List[Tuple[bytes, bytes]] = []
        chunks: List[bytes] = []

        async def keep(message):
            nonlocal status, headers
            if message["type"] == "http.response.start":
                # Copied before the middleware outside adds its headers
                status, headers = message["status"], list(message.get("headers", []))
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    self.store(key, Page(status, headers, b"".join(chunks)))
            await send(message)

        await self.app(scope, receive, keep)

    def store(self, key: tuple, page: Page) -> None:
        if page.status != 200 or any(k == b"set-cookie" for k, _ in page.headers):
            return
        if page.size > self.max_bytes:
            return
        old = self.pages.pop(key, None)  # two requests for it at once
        if old is not None:
            self.size -= old.size
        while self.pages and self.size + page.size > self.max_bytes:
            self.size -= self.pages.popitem(last=False)[1].size
        self.pages[key] = page
        self.size += page.size
