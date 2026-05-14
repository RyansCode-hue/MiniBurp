"""
Tests for the pure helpers in `miniburp.sender` that drive
`send_with_redirects`. The orchestrator itself does socket I/O, so it's
not unit-tested here — the helpers are where all the interesting logic
(method rewriting, cookie merging, URL resolution) lives.
"""
from __future__ import annotations

import pytest

from miniburp.sender import (
    build_redirect_request,
    extract_set_cookies,
    parse_raw_request,
    _cookie_header_value,
    _request_url,
)


# ---------------------------------------------------------------------------
# parse_raw_request
# ---------------------------------------------------------------------------


def test_parse_raw_request_basic_get():
    raw = b"GET /a/b?x=1 HTTP/1.1\r\nHost: example.com\r\nAccept: */*\r\n\r\n"
    method, target, version, headers, body = parse_raw_request(raw)
    assert method == "GET"
    assert target == "/a/b?x=1"
    assert version == "HTTP/1.1"
    assert headers == {"Host": "example.com", "Accept": "*/*"}
    assert body == b""


def test_parse_raw_request_post_with_body():
    raw = (
        b"POST /forgot-password?temp-forgot-password-token=abc HTTP/1.1\r\n"
        b"Host: lab.example.com\r\n"
        b"Content-Type: application/x-www-form-urlencoded\r\n"
        b"Content-Length: 27\r\n\r\n"
        b"username=wiener&password=hi"
    )
    method, target, _v, headers, body = parse_raw_request(raw)
    assert method == "POST"
    assert target.startswith("/forgot-password?")
    assert headers["Content-Length"] == "27"
    assert body == b"username=wiener&password=hi"


def test_parse_raw_request_bad_request_line_raises():
    with pytest.raises(ValueError):
        parse_raw_request(b"GARBAGE\r\nHost: x\r\n\r\n")


# ---------------------------------------------------------------------------
# extract_set_cookies — multi-cookie scan from raw bytes
# ---------------------------------------------------------------------------


def test_extract_set_cookies_picks_up_multiple():
    raw = (
        b"HTTP/1.1 302 Found\r\n"
        b"Location: /next\r\n"
        b"Set-Cookie: session=abc123; Path=/; HttpOnly\r\n"
        b"Set-Cookie: csrf=def456; SameSite=Lax\r\n"
        b"\r\n"
    )
    cookies = extract_set_cookies(raw)
    assert cookies == [("session", "abc123"), ("csrf", "def456")]


def test_extract_set_cookies_handles_empty_and_malformed():
    raw = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Length: 0\r\n"
        b"Set-Cookie: bare-name\r\n"
        b"Set-Cookie: =novalue; Path=/\r\n"
        b"\r\n"
    )
    # bare-name has no '=' → skipped; '=novalue' has empty name → skipped.
    assert extract_set_cookies(raw) == []


# ---------------------------------------------------------------------------
# _cookie_header_value — merging jar into existing Cookie header
# ---------------------------------------------------------------------------


def test_cookie_header_merges_jar_into_existing():
    out = _cookie_header_value({"session": "new"}, existing="theme=dark; session=old")
    # Jar wins on conflict, original ordering preserved.
    assert out == "theme=dark; session=new"


def test_cookie_header_appends_new_entries():
    out = _cookie_header_value({"a": "1", "b": "2"}, existing="")
    assert out == "a=1; b=2"


# ---------------------------------------------------------------------------
# build_redirect_request — method rewriting + URL resolution
# ---------------------------------------------------------------------------


def _parse(raw: bytes):
    return parse_raw_request(raw)


def test_302_post_coerces_to_get_and_drops_body():
    prev_headers = {
        "Host": "lab.example.com",
        "Content-Type": "application/x-www-form-urlencoded",
        "Content-Length": "27",
        "User-Agent": "miniburp",
    }
    scheme, host, port, raw = build_redirect_request(
        prev_scheme="https",
        prev_host="lab.example.com",
        prev_port=443,
        prev_method="POST",
        prev_headers=prev_headers,
        prev_body=b"username=wiener&password=hi",
        prev_target="/login",
        status=302,
        location="/my-account",
        cookie_jar={"session": "abc"},
    )
    assert (scheme, host, port) == ("https", "lab.example.com", 443)
    method, target, _v, headers, body = _parse(raw)
    assert method == "GET"
    assert target == "/my-account"
    assert body == b""
    # Body-related headers should be gone.
    assert "Content-Length" not in headers
    assert "Content-Type" not in headers
    # User-Agent carried over.
    assert headers["User-Agent"] == "miniburp"
    # Host recomputed.
    assert headers["Host"] == "lab.example.com"
    # Cookie applied.
    assert headers["Cookie"] == "session=abc"


def test_307_preserves_method_and_body():
    scheme, host, port, raw = build_redirect_request(
        prev_scheme="https",
        prev_host="lab.example.com",
        prev_port=443,
        prev_method="POST",
        prev_headers={
            "Host": "lab.example.com",
            "Content-Type": "application/json",
            "Content-Length": "11",
        },
        prev_body=b'{"a":"b"}\r\n',
        prev_target="/api/x",
        status=307,
        location="/api/x-v2",
        cookie_jar={},
    )
    method, target, _v, headers, body = _parse(raw)
    assert method == "POST"
    assert target == "/api/x-v2"
    assert body == b'{"a":"b"}\r\n'
    assert headers["Content-Type"] == "application/json"
    # Content-Length recomputed from current body.
    assert headers["Content-Length"] == str(len(body))


def test_absolute_location_can_switch_host_scheme_and_port():
    scheme, host, port, raw = build_redirect_request(
        prev_scheme="http",
        prev_host="lab.example.com",
        prev_port=80,
        prev_method="GET",
        prev_headers={"Host": "lab.example.com"},
        prev_body=b"",
        prev_target="/old",
        status=301,
        location="https://other.example.com:8443/new?q=1",
        cookie_jar={},
    )
    assert (scheme, host, port) == ("https", "other.example.com", 8443)
    _m, target, _v, headers, _b = _parse(raw)
    assert target == "/new?q=1"
    # Non-default port → included in Host.
    assert headers["Host"] == "other.example.com:8443"


def test_relative_location_resolves_against_previous_url():
    scheme, host, port, raw = build_redirect_request(
        prev_scheme="https",
        prev_host="lab.example.com",
        prev_port=443,
        prev_method="GET",
        prev_headers={"Host": "lab.example.com"},
        prev_body=b"",
        prev_target="/section/page",
        status=302,
        location="other",  # → /section/other
        cookie_jar={},
    )
    _m, target, _v, _h, _b = _parse(raw)
    assert target == "/section/other"
    assert (scheme, host, port) == ("https", "lab.example.com", 443)


# ---------------------------------------------------------------------------
# _request_url — sanity check
# ---------------------------------------------------------------------------


def test_request_url_omits_default_ports():
    assert _request_url("https", "h.test", 443, "/a") == "https://h.test/a"
    assert _request_url("http", "h.test", 80, "/a") == "http://h.test/a"
    assert _request_url("https", "h.test", 8443, "/a") == "https://h.test:8443/a"
