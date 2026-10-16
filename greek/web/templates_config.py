import hashlib
from pathlib import Path
from typing import Dict, Tuple

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


def static_url(path: str) -> str:
    """
    The URL of a static file with a version from its contents, e.g.
    /static/style.css?v=3f9a1c2b7e, so a browser fetches a changed file
    instead of reusing a cached copy. A missing file gets no version.
    """
    file = STATIC_DIR / path
    try:
        stat = file.stat()
    except OSError:
        return f"/static/{path}"
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _versions.get(path)
    if cached is None or cached[0] != key:
        cached = (key, hashlib.sha256(file.read_bytes()).hexdigest()[:10])
        _versions[path] = cached
    return f"/static/{path}?v={cached[1]}"


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
templates.env.globals["versions"] = read_versions
