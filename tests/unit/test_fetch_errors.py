"""
Tests for error handling in greek/fetch/ — network timeouts, bad XML,
HTTP errors, and missing references.
"""

import socket
import urllib.error
import xml.etree.ElementTree as ET
from unittest.mock import patch

import pytest

from greek.fetch.catalog import fetch_url
from greek.fetch.parser import extract_entries


# ---------------------------------------------------------------------------
# fetch_url — network timeout → propagates URLError
# ---------------------------------------------------------------------------


class TestFetchUrlTimeout:
    def test_timeout_raises_url_error_with_timeout_reason(self):
        """fetch_url propagates URLError wrapping socket.timeout on timeout."""
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.URLError(socket.timeout("timed out"))
            with pytest.raises(urllib.error.URLError) as exc_info:
                fetch_url("https://example.com/fake")
            assert "timed out" in str(exc_info.value.reason).lower()

    def test_timeout_is_url_error(self):
        """Timeout wraps as URLError (not a raw socket.timeout)."""
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.URLError(socket.timeout())
            with pytest.raises(urllib.error.URLError):
                fetch_url("https://example.com/fake")


# ---------------------------------------------------------------------------
# fetch_url — connection error → propagates URLError
# ---------------------------------------------------------------------------


class TestFetchUrlConnectError:
    def test_connect_error_raises_url_error(self):
        """fetch_url propagates URLError on connection refusal."""
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.URLError("Connection refused")
            with pytest.raises(urllib.error.URLError) as exc_info:
                fetch_url("https://example.com/fake")
            assert "connection refused" in str(exc_info.value).lower()

    def test_connect_error_reason_is_propagated(self):
        """The reason string is accessible from the raised URLError."""
        err = urllib.error.URLError("Name or service not known")
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = err
            with pytest.raises(urllib.error.URLError) as exc_info:
                fetch_url("https://example.com/fake")
            assert exc_info.value is err


# ---------------------------------------------------------------------------
# fetch_url — HTTP 4xx/5xx → HTTPError raised
# ---------------------------------------------------------------------------


class TestFetchUrlHttpErrors:
    def test_http_404_raises_http_error(self):
        """fetch_url propagates HTTPError for 404 responses."""
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                url="https://example.com/notfound",
                code=404,
                msg="Not Found",
                hdrs={},
                fp=None,
            )
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                fetch_url("https://example.com/notfound")
            assert exc_info.value.code == 404

    def test_http_500_raises_http_error(self):
        """fetch_url propagates HTTPError for 500 responses."""
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                url="https://example.com/error",
                code=500,
                msg="Internal Server Error",
                hdrs={},
                fp=None,
            )
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                fetch_url("https://example.com/error")
            assert exc_info.value.code == 500

    def test_http_error_is_subclass_of_url_error(self):
        """HTTPError is a URLError — callers can catch either."""
        assert issubclass(urllib.error.HTTPError, urllib.error.URLError)


# ---------------------------------------------------------------------------
# extract_entries — bad XML → ParseError with readable message
# ---------------------------------------------------------------------------


class TestExtractEntriesParseError:
    def test_malformed_xml_raises_parse_error(self):
        """extract_entries raises ET.ParseError on malformed XML input."""
        bad_xml = b"<not valid xml <<<"
        with pytest.raises(ET.ParseError):
            extract_entries(bad_xml)

    def test_truncated_xml_raises_parse_error(self):
        """extract_entries raises ET.ParseError on truncated XML."""
        truncated = b"<?xml version='1.0'?><TEI><text><body>"
        with pytest.raises(ET.ParseError):
            extract_entries(truncated)

    def test_empty_bytes_raises_parse_error(self):
        """extract_entries raises ET.ParseError on completely empty input."""
        with pytest.raises(ET.ParseError):
            extract_entries(b"")

    def test_parse_error_has_message(self):
        """The ParseError has a non-empty message (not silently swallowed)."""
        bad_xml = b"<root><unclosed>"
        with pytest.raises(ET.ParseError) as exc_info:
            extract_entries(bad_xml)
        assert str(exc_info.value)  # message is non-empty


# ---------------------------------------------------------------------------
# _import_entry helper — missing reference warnings (via fetch_cmd internals)
# ---------------------------------------------------------------------------


class TestImportEntryNoPairs:
    """When extract_entries returns [] (unsupported structure), _import_entry
    should print a warning and return without crashing."""

    def test_no_pairs_does_not_crash(self, db, tmp_path):
        """_import_entry returns gracefully when no lines are parsed."""
        from greek.fetch.catalog import CatalogEntry
        from greek.cli.fetch_cmd import _import_entry

        entry = CatalogEntry(
            author="TestAuthor",
            title="TestWork",
            tlg_author="tlg9999",
            tlg_work="tlg999",
            filename="tlg9999.tlg999.perseus-grc2.xml",
        )
        # Minimal well-formed TEI XML that has no parseable content
        empty_xml = (
            b'<?xml version="1.0"?>'
            b'<TEI xmlns="http://www.tei-c.org/ns/1.0">'
            b"<text><body></body></text></TEI>"
        )
        with patch("greek.cli.fetch_cmd.fetch_url", return_value=empty_xml):
            # Should not raise, should print a warning
            _import_entry(db, entry, tmp_path)

    def test_no_pairs_prints_warning(self, db, tmp_path):
        """_import_entry prints a warning when no lines are found."""
        from greek.fetch.catalog import CatalogEntry
        from greek.cli.fetch_cmd import _import_entry

        entry = CatalogEntry(
            author="TestAuthor",
            title="TestWork",
            tlg_author="tlg9999",
            tlg_work="tlg999",
            filename="tlg9999.tlg999.perseus-grc2.xml",
        )
        empty_xml = (
            b'<?xml version="1.0"?>'
            b'<TEI xmlns="http://www.tei-c.org/ns/1.0">'
            b"<text><body></body></text></TEI>"
        )
        output_lines = []
        with patch("greek.cli.fetch_cmd.fetch_url", return_value=empty_xml):
            with patch("greek.cli.fetch_cmd.console") as mock_console:
                mock_console.print = lambda msg, **kw: output_lines.append(str(msg))
                _import_entry(db, entry, tmp_path)
        # At least one message should mention the problem
        combined = " ".join(output_lines).lower()
        assert (
            "no lines" in combined
            or "not supported" in combined
            or "warning" in combined
        )


# ---------------------------------------------------------------------------
# _import_entry — network error → handled without traceback, no raise
# ---------------------------------------------------------------------------


class TestImportEntryNetworkError:
    def test_timeout_handled_gracefully(self, db, tmp_path):
        """_import_entry catches URLError and returns without raising."""
        from greek.fetch.catalog import CatalogEntry
        from greek.cli.fetch_cmd import _import_entry

        entry = CatalogEntry(
            author="Homer",
            title="Iliad",
            tlg_author="tlg0012",
            tlg_work="tlg001",
            filename="tlg0012.tlg001.perseus-grc2.xml",
        )
        with patch(
            "greek.cli.fetch_cmd.fetch_url",
            side_effect=urllib.error.URLError(socket.timeout("timed out")),
        ):
            # Must not raise — errors should be caught and displayed
            _import_entry(db, entry, tmp_path)

    def test_http_404_handled_gracefully(self, db, tmp_path):
        """_import_entry catches HTTPError and returns without raising."""
        from greek.fetch.catalog import CatalogEntry
        from greek.cli.fetch_cmd import _import_entry

        entry = CatalogEntry(
            author="Homer",
            title="Iliad",
            tlg_author="tlg0012",
            tlg_work="tlg001",
            filename="tlg0012.tlg001.perseus-grc2.xml",
        )
        with patch(
            "greek.cli.fetch_cmd.fetch_url",
            side_effect=urllib.error.HTTPError(
                url=entry.raw_url, code=404, msg="Not Found", hdrs={}, fp=None
            ),
        ):
            _import_entry(db, entry, tmp_path)

    def test_http_error_message_contains_status_code(self, db, tmp_path):
        """_import_entry's error output mentions the HTTP status code."""
        from greek.fetch.catalog import CatalogEntry
        from greek.cli.fetch_cmd import _import_entry

        entry = CatalogEntry(
            author="Homer",
            title="Iliad",
            tlg_author="tlg0012",
            tlg_work="tlg001",
            filename="tlg0012.tlg001.perseus-grc2.xml",
        )
        output_lines = []
        with patch(
            "greek.cli.fetch_cmd.fetch_url",
            side_effect=urllib.error.HTTPError(
                url=entry.raw_url, code=503, msg="Service Unavailable", hdrs={}, fp=None
            ),
        ):
            with patch("greek.cli.fetch_cmd.console") as mock_console:
                mock_console.print = lambda msg, **kw: output_lines.append(str(msg))
                _import_entry(db, entry, tmp_path)
        assert any("503" in line for line in output_lines)

    def test_timeout_message_is_user_friendly(self, db, tmp_path):
        """_import_entry's timeout message is readable to end users."""
        from greek.fetch.catalog import CatalogEntry
        from greek.cli.fetch_cmd import _import_entry

        entry = CatalogEntry(
            author="Homer",
            title="Iliad",
            tlg_author="tlg0012",
            tlg_work="tlg001",
            filename="tlg0012.tlg001.perseus-grc2.xml",
        )
        output_lines = []
        with patch(
            "greek.cli.fetch_cmd.fetch_url",
            side_effect=urllib.error.URLError(socket.timeout("timed out")),
        ):
            with patch("greek.cli.fetch_cmd.console") as mock_console:
                mock_console.print = lambda msg, **kw: output_lines.append(str(msg))
                _import_entry(db, entry, tmp_path)
        # Should mention timeout or network in the message
        combined = " ".join(output_lines).lower()
        assert "timeout" in combined or "network" in combined or "timed out" in combined

    def test_connect_error_message_mentions_network(self, db, tmp_path):
        """_import_entry's connect error message mentions network or connection."""
        from greek.fetch.catalog import CatalogEntry
        from greek.cli.fetch_cmd import _import_entry

        entry = CatalogEntry(
            author="Homer",
            title="Iliad",
            tlg_author="tlg0012",
            tlg_work="tlg001",
            filename="tlg0012.tlg001.perseus-grc2.xml",
        )
        output_lines = []
        with patch(
            "greek.cli.fetch_cmd.fetch_url",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            with patch("greek.cli.fetch_cmd.console") as mock_console:
                mock_console.print = lambda msg, **kw: output_lines.append(str(msg))
                _import_entry(db, entry, tmp_path)
        combined = " ".join(output_lines).lower()
        assert "network" in combined or "error" in combined or "connection" in combined


# ---------------------------------------------------------------------------
# _import_entry — bad XML → caught, user-friendly message, no traceback
# ---------------------------------------------------------------------------


class TestImportEntryBadXml:
    def test_parse_error_handled_gracefully(self, db, tmp_path):
        """_import_entry catches ET.ParseError and returns without raising."""
        from greek.fetch.catalog import CatalogEntry
        from greek.cli.fetch_cmd import _import_entry

        entry = CatalogEntry(
            author="Homer",
            title="Iliad",
            tlg_author="tlg0012",
            tlg_work="tlg001",
            filename="tlg0012.tlg001.perseus-grc2.xml",
        )
        with patch("greek.cli.fetch_cmd.fetch_url", return_value=b"<not valid xml <<<"):
            # Must not raise — ParseError should be caught and displayed
            _import_entry(db, entry, tmp_path)

    def test_parse_error_message_mentions_xml(self, db, tmp_path):
        """_import_entry's parse error output mentions XML and the source."""
        from greek.fetch.catalog import CatalogEntry
        from greek.cli.fetch_cmd import _import_entry

        entry = CatalogEntry(
            author="Homer",
            title="Iliad",
            tlg_author="tlg0012",
            tlg_work="tlg001",
            filename="tlg0012.tlg001.perseus-grc2.xml",
        )
        output_lines = []
        with patch("greek.cli.fetch_cmd.fetch_url", return_value=b"<not valid xml <<<"):
            with patch("greek.cli.fetch_cmd.console") as mock_console:
                mock_console.print = lambda msg, **kw: output_lines.append(str(msg))
                _import_entry(db, entry, tmp_path)
        combined = " ".join(output_lines).lower()
        assert "xml" in combined or "parse" in combined


# ---------------------------------------------------------------------------
# parser — ParseError propagates cleanly (not silently swallowed)
# ---------------------------------------------------------------------------


class TestParserErrorPropagation:
    def test_parse_error_is_not_swallowed(self):
        """extract_entries does not silently return [] on invalid XML."""
        # An empty bytes would be ET.ParseError, not an empty list
        with pytest.raises(ET.ParseError):
            extract_entries(b"")

    def test_valid_but_empty_xml_returns_empty_list(self):
        """Valid XML with no text content returns [] without error."""
        xml = (
            b'<?xml version="1.0"?>'
            b'<TEI xmlns="http://www.tei-c.org/ns/1.0">'
            b"<text><body></body></text></TEI>"
        )
        result = extract_entries(xml)
        assert result == []
