"""
Tests for the request-forwarding robustness helpers in http_io:

* ``ensure_content_length`` — re-declares a body's length when a chunked
  request was decoded and Transfer-Encoding stripped, so the upstream doesn't
  read a zero-length (dropped) body.
* ``parse_status_code`` — tolerant status-line parsing so a malformed upstream
  response yields ``None`` instead of crashing the keep-alive loop.
"""
from __future__ import annotations

from miniburp.http_io import (
    ensure_content_length,
    parse_status_code,
    validate_request_bytes,
)


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


# ---- validate_request_bytes ------------------------------------------------

def test_valid_request_passes():
    raw = b"GET /image?filename=../../../etc/passwd HTTP/1.1\r\nHost: lab.net\r\n\r\n"
    assert validate_request_bytes(raw) is None


def test_valid_post_with_body_passes():
    raw = b"POST /login HTTP/1.1\r\nHost: lab.net\r\nContent-Length: 3\r\n\r\nabc"
    assert validate_request_bytes(raw) is None


def test_missing_host_header_flagged():
    raw = b"GET / HTTP/1.1\r\nAccept: */*\r\n\r\n"
    err = validate_request_bytes(raw)
    assert err is not None and "Host" in err


def test_missing_http_version_flagged():
    # User deleted the version token — only two tokens remain.
    raw = b"GET /etc/passwd\r\nHost: lab.net\r\n\r\n"
    err = validate_request_bytes(raw)
    assert err is not None and "request line" in err


def test_double_spaced_path_flagged():
    # An unencoded space in the target yields four tokens.
    raw = b"GET /et c/passwd HTTP/1.1\r\nHost: lab.net\r\n\r\n"
    assert validate_request_bytes(raw) is not None


def test_empty_request_flagged():
    assert validate_request_bytes(b"") is not None
    assert validate_request_bytes(b"\r\n\r\n") is not None
