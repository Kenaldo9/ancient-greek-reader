"""Versioned static URLs: a changed file gets a new URL, so browsers refetch it."""

import hashlib
import os

import pytest

from greek.web import templates_config
from greek.web.templates_config import static_url


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
