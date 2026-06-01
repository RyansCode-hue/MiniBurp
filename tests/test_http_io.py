"""
Tests for the request-forwarding robustness helpers in http_io:

* ``ensure_content_length`` — re-declares a body's length when a chunked
  request was decoded and Transfer-Encoding stripped, so the upstream doesn't
  read a zero-length (dropped) body.
* ``parse_status_code`` — tolerant status-line parsing so a malformed upstream
  response yields ``None`` instead of crashing the keep-alive loop.
"""
from __future__ import annotations

from miniburp.http_io import ensure_content_length, parse_status_code


# ---- ensure_content_length -------------------------------------------------

def test_adds_content_length_when_missing_and_body_present():
    headers = {"Host": "example.com"}
    out = ensure_content_length(headers, b"username=carlos")
    assert out["Content-Length"] == "15"
    # mutates in place and returns the same dict
    assert out is headers


def test_no_content_length_for_empty_body():
    headers = {"Host": "example.com"}
    out = ensure_content_length(headers, b"")
    assert "Content-Length" not in out


def test_preserves_existing_content_length():
    headers = {"Host": "example.com", "Content-Length": "99"}
    out = ensure_content_length(headers, b"abc")
    # We don't override a length the caller already declared.
    assert out["Content-Length"] == "99"


def test_preserves_existing_content_length_case_insensitive():
    headers = {"Host": "example.com", "content-length": "99"}
    out = ensure_content_length(headers, b"abc")
    # No second (canonical-cased) header gets added.
    assert "Content-Length" not in out
    assert out["content-length"] == "99"


# ---- parse_status_code -----------------------------------------------------

def test_parses_normal_status_line():
    assert parse_status_code("HTTP/1.1 200 OK") == 200
    assert parse_status_code("HTTP/1.1 404 Not Found") == 404


def test_status_line_without_code_returns_none():
    assert parse_status_code("HTTP/1.1") is None


def test_status_line_non_numeric_returns_none():
    assert parse_status_code("HTTP/1.1 BORK Bad") is None


def test_empty_status_line_returns_none():
    assert parse_status_code("") is None
