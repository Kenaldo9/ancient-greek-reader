"""
The public site's visitor counts: daily totals of who reads it, what and
where they came from, with people told apart from bots and the outside
check (deploy/public/check_site.py). Nothing kept tells one visitor from
another: no address, User-Agent string, cookie or identifier, no row per
visitor and no count of "unique visitors". A day's file holds totals only.

People are counted from what a browser does. visit.js asks for /visit once
a page has been in view for 3 seconds or the reader clicks or taps on it,
and htmx's requests (a book opened in the text pane, a search, a word, a
passage) come only from a page's own script. The first word a text's first
visit opens by itself (X-First-Word) is no one's choice and isn't counted.
A request whose User-Agent names a bot counts as that bot's, and the site
check names itself. A page a browser loads but never confirms is
"unconfirmed": a bot passing as a browser, a reader with JavaScript off, or
one who left within 3 seconds.

Only requests that came through Cloudflare count (they carry CF-Ray), so
the deploy's own checks from inside the container don't. The country is
Cloudflare's CF-IPCountry; the referring site is the host visit.js names.

On only when GREEK_VISITS_DIR names a folder, which only the public site's
compose file sets. There each day is a file, YYYY-MM-DD.json, rewritten
every WRITE_EVERY seconds while its counts change and when the app stops,
read back when it starts, and kept KEEP_DAYS days.
"""

import json
import os
import re
import sqlite3
import sys
import threading
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, FrozenSet, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from starlette.requests import Request

from greek.db.connection import get_connection
from greek.db.repos.lines import list_books
from greek.db.repos.parses import get_tb_source
from greek.db.repos.texts import list_texts
from greek.web.reader_data import work_name
from greek.web.templates_config import site_origin

FORMAT = 1
KEEP_DAYS = 400
WRITE_EVERY = 300  # seconds
MOST_SITES = 100  # referring sites a day; the rest are "other"
VISIT_PATH = "/visit"

# deploy/public/check_site.py's User-Agent
SITE_CHECK = "ancient-greek-reader site check"
CHECK = "site check"
BROWSER = ""

# Bots that say what they are, by name: the first whose word is in the
# User-Agent (lower case). Search engines, link previews, and assistants
# fetching a page for someone (Cloudflare blocks the AI crawlers).
BOTS = [
    ("Googlebot", ("googlebot", "google-inspectiontool", "googleother")),
    ("Bingbot", ("bingbot", "bingpreview", "msnbot")),
    ("DuckDuckBot", ("duckduckbot", "duckassistbot")),
    ("Applebot", ("applebot",)),
    ("YandexBot", ("yandex",)),
    ("Baiduspider", ("baiduspider",)),
    ("Ahrefs", ("ahrefs",)),
    ("Semrush", ("semrush",)),
    ("Facebook", ("facebookexternalhit", "meta-externalagent")),
    ("Twitter", ("twitterbot",)),
    ("LinkedIn", ("linkedinbot",)),
    ("Slack", ("slackbot", "slack-imgproxy")),
    ("Discord", ("discordbot",)),
    ("Telegram", ("telegrambot",)),
    ("WhatsApp", ("whatsapp",)),
    ("ChatGPT", ("chatgpt-user", "oai-searchbot", "gptbot")),
    ("Claude", ("claude-user", "claude-searchbot", "claudebot")),
    ("Perplexity", ("perplexity",)),
]
OTHER_BOT = "other bot"
# Any other client that says it is a bot or a script; browsers all start
# their User-Agent with Mozilla/
BOT_WORDS = re.compile(
    r"bot\b|bot/|crawl|spider|slurp|headless|python|curl|wget|httpx|"
    r"go-http-client|okhttp|java/|libwww|scrapy|monitor|uptime|lighthouse"
)

_HOST = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)*")
_COUNTRY = re.compile(r"[A-Z][A-Z0-9]")
_READER = re.compile(r"/texts/(\d+)/(read|toc|word|passage)")

# Each text's name in the counts and its books' numbers
Names = Dict[int, Tuple[str, FrozenSet[str]]]


def who(user_agent: str) -> str:
    """CHECK, a bot's name, or BROWSER."""
    if user_agent == SITE_CHECK:
        return CHECK
    agent = user_agent.lower()
    for name, words in BOTS:
        if any(word in agent for word in words):
            return name
    if not agent.startswith("mozilla/") or BOT_WORDS.search(agent):
        return OTHER_BOT
    return BROWSER


def referring_site(host: str, own_host: str) -> Optional[str]:
    """
    The site a visit came from, as visit.js names it (the referring page's
    host): without www., "direct" when there was none, "other" for what
    isn't a host name; None for this site itself (a page view, not a visit).
    """

    def bare(name: str) -> str:
        name = name.strip().lower().rstrip(".").split(":")[0]
        return name[4:] if name.startswith("www.") else name

    host = bare(host)
    if not host:
        return "direct"
    if host == bare(own_host):
        return None
    if len(host) > 100 or not _HOST.fullmatch(host):
        return "other"
    return host


def country(headers) -> str:
    """Cloudflare's two-letter country (XX unknown, T1 Tor), else "unknown"."""
    code = headers.get("cf-ipcountry", "").strip().upper()
    return code if _COUNTRY.fullmatch(code) else "unknown"


def page_names(conn: sqlite3.Connection) -> Names:
    """How the counts name each text ("Iliad", "Herodotus"), and its books."""
    names = {}
    for text in list_texts(conn):
        name = work_name(text, get_tb_source(conn, text.id))
        name = name[4:] if name.startswith("the ") else name
        books = frozenset(b.number for b in list_books(conn, text.id))
        names[text.id] = (name[:1].upper() + name[1:], books)
    return names


def page_label(path: str, query: str, names: Names) -> Optional[str]:
    """
    A page as the counts name it: Library, About, "Iliad contents", "Iliad 1"
    (the book of at= or lines=), or just "Iliad"; None for any other page.
    """
    if path == "/library":
        return "Library"
    if path == "/about":
        return "About"
    m = _READER.fullmatch(path)
    if m is None or m[2] not in ("read", "toc") or int(m[1]) not in names:
        return None
    name, books = names[int(m[1])]
    if m[2] == "toc":
        return f"{name} contents"
    params = parse_qs(query)
    place = (params.get("at") or params.get("lines") or [""])[0]
    book = place.split(".")[0].split("-")[0]
    return f"{name} {book}" if book in books else name


def from_this_site(request: Request) -> bool:
    """
    Whether a request came from one of the site's own pages: the browser's
    Sec-Fetch-Site, or for a browser without it, a Referer on this site (the
    site's Referrer-Policy, same-origin, sends one).
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site == "same-origin"
    return request.headers.get("referer", "").startswith(site_origin(request) + "/")


def _empty(day: str) -> dict:
    return {
        "format": FORMAT,
        "day": day,
        "people": {"visits": 0, "views": 0},
        "pages": Counter(),
        "from": Counter(),
        "countries": Counter(),
        "tools": Counter({"word": 0, "passage": 0, "search": 0}),
        "bots": Counter(),
        "checks": {"runs": 0, "requests": 0},
        # Pages browsers loaded, and those visit.js confirmed
        "loads": {"browsers": 0, "confirmed": 0},
    }


def _read_day(path: Path, day: str) -> dict:
    """A day's file as it was written, or a new day if there is none."""
    counts = _empty(day)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return counts
    if saved.get("format") != FORMAT or saved.get("day") != day:
        return counts
    for key in ("people", "checks", "loads"):
        counts[key].update(saved.get(key, {}))
    for key in ("pages", "from", "countries", "tools", "bots"):
        counts[key].update(saved.get(key, {}))
    return counts


def _as_written(counts: dict) -> dict:
    loads = counts["loads"]
    out = {
        key: dict(value.most_common()) if isinstance(value, Counter) else value
        for key, value in counts.items()
    }
    out["unconfirmed"] = max(0, loads["browsers"] - loads["confirmed"])
    return out


class Visits:
    """One day's counts in memory, and the folder of daily files."""

    def __init__(
        self,
        folder: Path,
        names: Callable[[], Names],
        today: Callable[[], date] = date.today,
    ):
        self.folder = Path(folder)
        self._names_from = names
        self._names: Optional[Names] = None
        self._today = today
        self._lock = threading.Lock()
        self._counts = _read_day(self._path(today().isoformat()), today().isoformat())
        self._changed = False
        self._failing = False

    # -- what a request is ---------------------------------------------------

    def names(self) -> Names:
        if self._names is None:
            self._names = self._names_from()
        return self._names

    def count_for(self, scope) -> Optional[Callable[[], None]]:
        """
        What a GET adds once it is answered 200, or None when it counts for
        nothing. /visit counts here at once, whatever the app answers.
        """
        request = Request(scope)
        if "cf-ray" not in request.headers:
            return None
        sender = who(request.headers.get("user-agent", ""))
        path, query = scope["path"], scope["query_string"].decode("latin-1")
        if path == VISIT_PATH:
            self._visit(request, sender)
            return None
        if path == "/version":
            return self._add_run if sender == CHECK else None
        m = _READER.fullmatch(path)
        if not (path in ("/library", "/about") or m):
            return None
        if sender != BROWSER:
            return lambda: self._add_other(sender)
        if request.headers.get("hx-request") == "true":
            if m is None:
                return None
            if request.headers.get("x-first-word") == "1":
                return None
            if m[2] in ("word", "passage"):
                return lambda: self._add_tool(m[2])
            if m[2] == "read" and (parse_qs(query).get("q") or [""])[0]:
                return lambda: self._add_tool("search")
            label = page_label(path, query, self.names())
            return (lambda: self._add_view(label)) if label else None
        if m is not None and m[2] in ("word", "passage"):
            return None
        return self._add_load

    def _visit(self, request: Request, sender: str) -> None:
        """visit.js's request: a page a person has read a moment."""
        if sender != BROWSER:
            self._add_other(sender)
            return
        if not from_this_site(request):
            return
        params = request.query_params
        page = urlsplit(params.get("page", "")[:2000])
        label = page_label(page.path, page.query, self.names()) or "other page"
        came_from = referring_site(
            params.get("from", "")[:200], request.headers.get("host", "")
        )
        with self._lock:
            counts = self._day()
            counts["people"]["views"] += 1
            counts["loads"]["confirmed"] += 1
            counts["pages"][label] += 1
            if came_from is not None:
                sites = counts["from"]
                if came_from not in sites and len(sites) >= MOST_SITES:
                    came_from = "other"
                counts["people"]["visits"] += 1
                counts["from"][came_from] += 1
                counts["countries"][country(request.headers)] += 1
            self._changed = True

    # -- adding to the day ---------------------------------------------------

    def _add_view(self, label: str) -> None:
        with self._lock:
            counts = self._day()
            counts["people"]["views"] += 1
            counts["pages"][label] += 1
            self._changed = True

    def _add_tool(self, tool: str) -> None:
        with self._lock:
            self._day()["tools"][tool] += 1
            self._changed = True

    def _add_load(self) -> None:
        with self._lock:
            self._day()["loads"]["browsers"] += 1
            self._changed = True

    def _add_other(self, sender: str) -> None:
        with self._lock:
            counts = self._day()
            if sender == CHECK:
                counts["checks"]["requests"] += 1
            else:
                counts["bots"][sender] += 1
            self._changed = True

    def _add_run(self) -> None:
        with self._lock:
            self._day()["checks"]["runs"] += 1
            self._changed = True

    def _day(self) -> dict:
        """Today's counts; at a new day, the last one written first (locked)."""
        today = self._today().isoformat()
        if self._counts["day"] != today:
            if self._changed:
                self._write(self._counts)
            self._counts = _read_day(self._path(today), today)
            self._changed = False
            self._prune()
        return self._counts

    # -- the files -----------------------------------------------------------

    def _path(self, day: str) -> Path:
        return self.folder / f"{day}.json"

    def write(self) -> None:
        """Today's file, if its counts changed since it was last written."""
        with self._lock:
            if self._changed:
                self._write(self._counts)

    def _write(self, counts: dict) -> None:
        path = self._path(counts["day"])
        part = path.with_name(f".{path.name}.part")
        try:
            part.write_text(
                json.dumps(_as_written(counts), ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8",
            )
            part.chmod(0o644)  # read by the Pi's dashboard
            os.replace(part, path)
        except OSError as e:
            if not self._failing:
                print(f"Visitor counts not written: {e}", file=sys.stderr, flush=True)
            self._failing = True
            return
        self._failing = False
        self._changed = False

    def _prune(self) -> None:
        """Delete the files of days more than KEEP_DAYS ago."""
        oldest = self._today() - timedelta(days=KEEP_DAYS)
        for path in self.folder.glob("*.json"):
            try:
                if date.fromisoformat(path.stem) < oldest:
                    path.unlink()
            except (ValueError, OSError):
                continue

    def start(self) -> None:
        """Write every WRITE_EVERY seconds (VisitCounter writes at the end)."""

        def keep_writing():
            while True:
                time.sleep(WRITE_EVERY)
                self.write()

        with self._lock:
            self._prune()
        threading.Thread(target=keep_writing, name="visits", daemon=True).start()


def _names_from_database() -> Names:
    conn = get_connection()
    try:
        return page_names(conn)
    finally:
        conn.close()


def visits_from_environment() -> Optional[Visits]:
    """The public site's counts, when GREEK_VISITS_DIR names their folder."""
    folder = os.environ.get("GREEK_VISITS_DIR")
    return Visits(Path(folder), _names_from_database) if folder else None


class VisitCounter:
    """
    ASGI middleware that counts the requests Visits.count_for names. It sits
    outside the page cache, so a page the cache answers counts too. When the
    app stops (the lifespan's shutdown), it writes the day's file: on
    SIGTERM, uvicorn shuts down and then dies of the signal, so exit hooks
    don't run.
    """

    def __init__(self, app, visits: Visits):
        self.app = app
        self.visits = visits

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":

            async def written_at_shutdown(message):
                if message["type"] == "lifespan.shutdown.complete":
                    self.visits.write()
                await send(message)

            return await self.app(scope, receive, written_at_shutdown)
        if scope["type"] != "http" or scope["method"] != "GET":
            return await self.app(scope, receive, send)
        count = self.visits.count_for(scope)
        if count is None:
            return await self.app(scope, receive, send)

        async def watch(message):
            if message["type"] == "http.response.start" and message["status"] == 200:
                count()
            await send(message)

        await self.app(scope, receive, watch)
