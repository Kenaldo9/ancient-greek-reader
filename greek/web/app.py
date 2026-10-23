import sys

from dotenv import load_dotenv
from fastapi import FastAPI, Request

load_dotenv()
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.datastructures import MutableHeaders  # noqa: E402
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402

from greek.db.connection import (  # noqa: E402
    DatabaseUnusable,
    get_connection,
    run_migrations,
)
from greek.version import read_versions  # noqa: E402
from greek.web.templates_config import (  # noqa: E402
    STATIC_DIR,
    static_cache_control,
    templates,  # noqa: F401 — re-exported for convenience
)
from greek.web.routes import about, library, passage, reader, site  # noqa: E402


def _run_startup_migrations():
    """Migrate the database; the reason instead when it can't (DatabaseUnusable)."""
    conn = get_connection()
    try:
        upgrade = run_migrations(conn)
    except DatabaseUnusable as e:
        print(e, file=sys.stderr, flush=True)
        return e
    finally:
        conn.close()
    if upgrade.backup:
        print(
            f"Backed up the database to {upgrade.backup}, then applied "
            f"{', '.join(upgrade.applied)}.",
            file=sys.stderr,
            flush=True,
        )
    return None


# Set when the database is newer than this code, or read-only and behind it:
# every page says so instead
startup_error = _run_startup_migrations()

# No /docs, /redoc or /openapi.json: the routes are pages, not an API
app = FastAPI(
    title="Ancient Greek Reader", docs_url=None, redoc_url=None, openapi_url=None
)


class CachedStaticFiles(StaticFiles):
    """Static files with how long they may be kept (static_cache_control)."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = static_cache_control(
            path, scope["query_string"]
        )
        return response


app.mount("/static", CachedStaticFiles(directory=STATIC_DIR), name="static")

app.include_router(library.router)
app.include_router(reader.router)
app.include_router(passage.router)
app.include_router(about.router)
app.include_router(site.router)


@app.middleware("http")
async def refuse_an_unusable_database(request: Request, call_next):
    if startup_error and not request.url.path.startswith(("/static/", "/version")):
        return templates.TemplateResponse(
            request,
            "unavailable.html",
            {"heading": startup_error.heading, "message": str(startup_error)},
            status_code=503,
        )
    return await call_next(request)


class TextGZipMiddleware(GZipMiddleware):
    """gzip for pages, fragments, CSS and JS; fonts and images already are."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].endswith((".woff2", ".png")):
            await self.app(scope, receive, send)
        else:
            await super().__call__(scope, receive, send)


# A book page is about 690 KB of HTML and 50–70 KB compressed. Level 6 takes
# 25 ms for one on the Raspberry Pi 4, level 9 87 ms for 3 KB less.
app.add_middleware(TextGZipMiddleware, minimum_size=1000, compresslevel=6)

# Every page uses only the app's own scripts, styles, fonts and images: no
# inline code or style attributes (base.html turns off htmx's injected style).
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; base-uri 'none'; "
    "object-src 'none'; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}


class SecurityHeaders:
    """SECURITY_HEADERS on every response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).update(SECURITY_HEADERS)
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(SecurityHeaders)


@app.get("/")
def root():
    return RedirectResponse(url="/library")


@app.get("/version")
def version():
    """Which code is running and which database it uses (greek --version)."""
    v = read_versions()
    return {
        "code": {"commit": v.commit, "built": v.built},
        "database": {
            "version": v.database,
            "code_knows": v.known,
            "pending": v.pending,
            "newer_than_code": v.unknown,
        },
        "serving": startup_error is None,
    }
