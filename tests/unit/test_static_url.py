"""
Versioned static URLs: a changed file gets a new URL, so browsers refetch it,
and a URL with the file's current version may be kept for a year.
"""

import hashlib
import os

import pytest

from greek.web import templates_config
from greek.web.templates_config import (
    absolute_url,
    static_cache_control,
    static_url,
)


@pytest.fixture
def static_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(templates_config, "STATIC_DIR", tmp_path)
    monkeypatch.setattr(templates_config, "_versions", {})
    return tmp_path


def test_version_is_a_hash_of_the_contents(static_dir):
    (static_dir / "style.css").write_text("body { color: red; }")
    version = hashlib.sha256(b"body { color: red; }").hexdigest()[:10]
    assert static_url("style.css") == f"/static/style.css?v={version}"


def test_nested_path(static_dir):
    (static_dir / "fonts").mkdir()
    (static_dir / "fonts" / "fonts.css").write_text("@font-face {}")
    assert static_url("fonts/fonts.css").startswith("/static/fonts/fonts.css?v=")


def test_changed_file_gets_a_new_version(static_dir):
    css = static_dir / "style.css"
    css.write_text("a { color: red; }")
    first = static_url("style.css")
    assert static_url("style.css") == first  # unchanged: same URL
    css.write_text("a { color: blue; }")
    stat = css.stat()
    os.utime(css, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    second = static_url("style.css")
    assert second != first
    assert second.startswith("/static/style.css?v=")


def test_unchanged_file_is_not_read_again(static_dir, monkeypatch):
    (static_dir / "app.js").write_text("console.log(1);")
    first = static_url("app.js")
    reads = []
    real_read = type(static_dir).read_bytes
    monkeypatch.setattr(
        type(static_dir),
        "read_bytes",
        lambda self: reads.append(self) or real_read(self),
    )
    assert static_url("app.js") == first
    assert reads == []


def test_missing_file_has_no_version(static_dir):
    assert static_url("missing.css") == "/static/missing.css"


def test_the_current_version_is_kept_for_a_year_and_any_other_is_not(static_dir):
    css = static_dir / "style.css"
    css.write_text("a { color: red; }")
    version = static_url("style.css").split("?v=")[1]
    assert static_cache_control("style.css", f"v={version}".encode()) == (
        "public, max-age=31536000, immutable"
    )
    assert static_cache_control("style.css", b"v=0123456789") == "no-cache"
    assert static_cache_control("style.css", b"") == "no-cache"
    assert static_cache_control("missing.css", b"v=") == "no-cache"


def test_a_font_is_kept_for_a_week(static_dir):
    (static_dir / "plex.woff2").write_bytes(b"wOF2")
    assert static_cache_control("plex.woff2", b"") == "public, max-age=604800"


class _Request:
    def __init__(self, headers, scheme="http", netloc="127.0.0.1:8999"):
        self.headers = headers
        self.url = type("URL", (), {"scheme": scheme, "netloc": netloc})()


def test_absolute_url_takes_the_scheme_a_proxy_forwards():
    tunnel = _Request({"host": "reader.example", "x-forwarded-proto": "https"})
    assert absolute_url(tunnel, "/library") == "https://reader.example/library"
    local = _Request({"host": "127.0.0.1:8999"})
    assert absolute_url(local, "/about") == "http://127.0.0.1:8999/about"
    odd = _Request({"host": "reader.example", "x-forwarded-proto": "gopher"})
    assert absolute_url(odd, "/") == "http://reader.example/"
