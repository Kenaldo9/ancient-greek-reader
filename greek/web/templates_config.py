import hashlib
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.templating import Jinja2Templates

from greek.api.models import model_label
from greek.version import read_versions
from greek.web.formatting import (
    count_noun,
    format_date,
    format_time,
    greek_spans,
    parse_items,
    ref_range,
    slash_breaks,
)

STATIC_DIR = Path(__file__).parent / "static"

# path → ((mtime_ns, size), version); a file is hashed again only when it changes
_versions: Dict[str, Tuple[Tuple[int, int], str]] = {}


def static_version(path: str) -> Optional[str]:
    """A static file's version from its contents; None for a missing file."""
    file = STATIC_DIR / path
    try:
        stat = file.stat()
    except OSError:
        return None
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _versions.get(path)
    if cached is None or cached[0] != key:
        cached = (key, hashlib.sha256(file.read_bytes()).hexdigest()[:10])
        _versions[path] = cached
    return cached[1]


def static_url(path: str) -> str:
    """
    The URL of a static file with a version from its contents, e.g.
    /static/style.css?v=3f9a1c2b7e, so a browser fetches a changed file
    instead of reusing a cached copy. A missing file gets no version.
    """
    version = static_version(path)
    return f"/static/{path}?v={version}" if version else f"/static/{path}"


def static_cache_control(path: str, query_string: bytes) -> str:
    """
    How long browsers and Cloudflare may keep a static file: a year for a URL
    with the file's current version (static_url), since a change gives it a
    new URL; a week for a font, which fonts.css names without a version and
    which changes only with a new release of its typeface; anything else is
    asked for again each time (a 304 when it is unchanged).
    """
    asked = parse_qs(query_string.decode("latin-1")).get("v", [None])[0]
    if asked and asked == static_version(path):
        return "public, max-age=31536000, immutable"
    if path.endswith(".woff2"):
        return "public, max-age=604800"
    return "no-cache"


def site_origin(request: Request) -> str:
    """
    This site's scheme and host as the request asked for them, e.g.
    https://reader.example. Behind Cloudflare a request comes from the tunnel
    over plain HTTP, so the scheme is its X-Forwarded-Proto.
    """
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    scheme = forwarded if forwarded in ("http", "https") else request.url.scheme
    return f"{scheme}://{request.headers.get('host') or request.url.netloc}"


def absolute_url(request: Request, path: str) -> str:
    """A path on this site as a whole URL, for the sitemap and canonical links."""
    return site_origin(request) + path


templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["date"] = format_date
templates.env.filters["time"] = format_time
templates.env.filters["count_noun"] = count_noun
templates.env.filters["model_label"] = model_label
templates.env.filters["slash_breaks"] = slash_breaks
templates.env.filters["greek_spans"] = greek_spans
templates.env.globals["ref_range"] = ref_range
templates.env.globals["parse_items"] = parse_items
templates.env.globals["static_url"] = static_url
templates.env.globals["absolute_url"] = absolute_url
templates.env.globals["versions"] = read_versions
