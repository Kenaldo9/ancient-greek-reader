"""
Catalog of Greek texts available on Perseus Digital Library (GitHub).
Built once from the GitHub repo, then cached in data/catalog.json.
"""

import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

GITHUB_RAW = "https://raw.githubusercontent.com/PerseusDL/canonical-greekLit/master"
GITHUB_API_TREE = "https://api.github.com/repos/PerseusDL/canonical-greekLit/git/trees/master?recursive=1"
CTS_NS = "http://chs.harvard.edu/xmlns/cts"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


@dataclass
class CatalogEntry:
    author: str
    title: str
    tlg_author: str
    tlg_work: str
    filename: str

    @property
    def raw_url(self) -> str:
        return f"{GITHUB_RAW}/data/{self.tlg_author}/{self.tlg_work}/{self.filename}"

    def __str__(self) -> str:
        return f"{self.author} — {self.title}"


def fetch_url(url: str, timeout: int = 30) -> bytes:
    """Fetch URL and return response bytes.

    Raises urllib.error.HTTPError for 4xx/5xx responses, urllib.error.URLError
    for connection and timeout failures. Callers are responsible for handling
    these exceptions with user-facing messages.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": "ancient-greek-reader/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_eng_text(root, tag: str) -> Optional[str]:
    """Find a tag's text preferring xml:lang='eng'."""
    best = None
    for elem in root.findall(f"{{{CTS_NS}}}{tag}"):
        if elem.get(XML_LANG) == "eng":
            return elem.text
        best = elem.text
    return best


def _fetch_author_name(tlg_author: str) -> Optional[str]:
    url = f"{GITHUB_RAW}/data/{tlg_author}/__cts__.xml"
    try:
        root = ET.fromstring(fetch_url(url))
        return _get_eng_text(root, "groupname")
    except Exception:
        return None


def _fetch_work_title(tlg_author: str, tlg_work: str) -> Optional[str]:
    url = f"{GITHUB_RAW}/data/{tlg_author}/{tlg_work}/__cts__.xml"
    try:
        root = ET.fromstring(fetch_url(url))
        return _get_eng_text(root, "title")
    except Exception:
        return None


def build_catalog(console=None) -> List[CatalogEntry]:
    """Fetch and build the full catalog from Perseus GitHub. Takes ~1-2 minutes.

    Raises urllib.error.URLError or urllib.error.HTTPError on network failure.
    Callers should catch these and display a user-facing error.
    """
    if console:
        console.print("[dim]Fetching file index from Perseus GitHub...[/dim]")

    tree_data = json.loads(fetch_url(GITHUB_API_TREE, timeout=60))
    paths = [
        item["path"] for item in tree_data.get("tree", []) if item["type"] == "blob"
    ]

    # Find all Greek text files: data/{tlg_author}/{tlg_work}/*-grc{1,2}.xml
    grc_re = re.compile(r"^data/(tlg\d+)/(tlg\d+)/.*-grc[12]\.xml$")

    # {tlg_author: {tlg_work: filename}} — prefer -grc2 over -grc1
    texts: Dict[str, Dict[str, str]] = {}
    for path in paths:
        m = grc_re.match(path)
        if m:
            ta, tw = m.group(1), m.group(2)
            fname = path.split("/")[-1]
            texts.setdefault(ta, {})
            existing = texts[ta].get(tw, "")
            if not existing or ("grc2" in fname and "grc2" not in existing):
                texts[ta][tw] = fname

    total_works = sum(len(works) for works in texts.values())
    if console:
        console.print(
            f"[dim]Found {total_works} Greek texts across {len(texts)} authors. "
            f"Fetching titles...[/dim]"
        )

    entries = []
    for tlg_author, works in sorted(texts.items()):
        author_name = _fetch_author_name(tlg_author) or tlg_author
        for tlg_work, filename in sorted(works.items()):
            title = _fetch_work_title(tlg_author, tlg_work) or tlg_work
            entries.append(
                CatalogEntry(
                    author=author_name,
                    title=title,
                    tlg_author=tlg_author,
                    tlg_work=tlg_work,
                    filename=filename,
                )
            )

    return sorted(entries, key=lambda e: (e.author.lower(), e.title.lower()))


def load_catalog(
    data_dir: Path, refresh: bool = False, console=None
) -> List[CatalogEntry]:
    cache_path = data_dir / "catalog.json"
    if not refresh and cache_path.exists():
        raw = json.loads(cache_path.read_text())
        return [CatalogEntry(**e) for e in raw]

    entries = build_catalog(console=console)
    cache_path.write_text(
        json.dumps([asdict(e) for e in entries], ensure_ascii=False, indent=2)
    )
    return entries


def search_catalog(entries: List[CatalogEntry], query: str) -> List[CatalogEntry]:
    tokens = query.lower().split()
    return [
        e for e in entries if all(t in f"{e.author} {e.title}".lower() for t in tokens)
    ]
