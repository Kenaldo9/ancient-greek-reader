import sys

from dotenv import load_dotenv
from fastapi import FastAPI, Request

load_dotenv()
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402

from greek.db.connection import (  # noqa: E402
    DatabaseNewerThanCode,
    get_connection,
    run_migrations,
)
from greek.version import read_versions  # noqa: E402
from greek.web.templates_config import STATIC_DIR, templates  # noqa: E402, F401 — templates re-exported for convenience
from greek.web.routes import about, library, passage, reader  # noqa: E402


def _run_startup_migrations():
    """Migrate the database; the reason instead when it is newer than this code."""
    conn = get_connection()
    try:
        upgrade = run_migrations(conn)
    except DatabaseNewerThanCode as e:
        print(e, file=sys.stderr, flush=True)
        return str(e)
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


# Set when the database is newer than this code: every page says so instead
startup_error = _run_startup_migrations()

app = FastAPI(title="Ancient Greek Reader")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(library.router)
app.include_router(reader.router)
app.include_router(passage.router)
app.include_router(about.router)


@app.middleware("http")
async def refuse_a_newer_database(request: Request, call_next):
    if startup_error and not request.url.path.startswith(("/static/", "/version")):
        return templates.TemplateResponse(
            request, "unavailable.html", {"message": startup_error}, status_code=503
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
