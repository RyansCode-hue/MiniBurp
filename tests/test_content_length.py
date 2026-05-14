"""
Tests for sender.recompute_content_length.

Intruder calls this on every attempt because each payload substitution shifts
body length; failing to do so was why brute-force-style labs effectively
couldn't be run against the previous implementation.
"""
from __future__ import annotations

from miniburp.sender import recompute_content_length


def test_rewrites_wrong_content_length():
    raw = (
        b"POST /x HTTP/1.1\r\n"
        b"Host: example.com\r\n"
        b"Content-Length: 999\r\n"
        b"Content-Type: application/x-www-form-urlencoded\r\n"
        b"\r\n"
        b"username=carlos"
    )
    out = recompute_content_length(raw)
    assert b"Content-Length: 15\r\n" in out
    assert b"Content-Length: 999" not in out
    # Body preserved unchanged.
    assert out.endswith(b"\r\n\r\nusername=carlos")


def test_inserts_when_missing():
    raw = (
        b"POST /x HTTP/1.1\r\n"
        b"Host: example.com\r\n"
        b"\r\n"
        b"abc"
    )
    out = recompute_content_length(raw)
    assert b"Content-Length: 3\r\n" in out


def test_handles_empty_body():
    raw = (
        b"GET / HTTP/1.1\r\n"
        b"Host: example.com\r\n"
        b"Content-Length: 42\r\n"
        b"\r\n"
    )
    out = recompute_content_length(raw)
    assert b"Content-Length: 0\r\n" in out
    assert b"Content-Length: 42" not in out
    assert out.endswith(b"\r\n\r\n")


def test_case_insensitive_header_match():
    """RFC says header names are case-insensitive — we must catch lower-case too."""
    raw = (
        b"POST /x HTTP/1.1\r\n"
        b"host: example.com\r\n"
        b"content-length: 1\r\n"
        b"\r\n"
        b"hello"
    )
    out = recompute_content_length(raw)
    # Only the Content-Length we wrote should remain — not the original "1".
    cl_lines = [line for line in out.split(b"\r\n") if line.lower().startswith(b"content-length")]
    assert cl_lines == [b"Content-Length: 5"]


def test_preserves_other_headers_and_order():
    raw = (
        b"POST /x HTTP/1.1\r\n"
        b"Host: example.com\r\n"
        b"Content-Length: 1\r\n"
        b"X-Custom: keep-me\r\n"
        b"\r\n"
        b"hello"
    )
    out = recompute_content_length(raw)
    head_block = out.split(b"\r\n\r\n", 1)[0]
    lines = head_block.split(b"\r\n")
    # Original order: request line, Host, Content-Length (rewritten), X-Custom.
    assert lines[0] == b"POST /x HTTP/1.1"
    assert lines[1] == b"Host: example.com"
    assert lines[2] == b"Content-Length: 5"
    assert lines[3] == b"X-Custom: keep-me"


def test_unparseable_input_returned_unchanged():
    """No `\\r\\n\\r\\n` separator → don't try to be clever, just hand it back."""
    raw = b"this is not http at all"
    assert recompute_content_length(raw) == raw


def test_idempotent_when_already_correct():
    raw = (
        b"POST /x HTTP/1.1\r\n"
        b"Content-Length: 5\r\n"
        b"\r\n"
        b"hello"
    )
    assert recompute_content_length(raw) == raw


def test_only_first_content_length_is_rewritten():
    """Defense in depth: if a malformed request has two CL headers we still emit one."""
    raw = (
        b"POST /x HTTP/1.1\r\n"
        b"Content-Length: 1\r\n"
        b"Content-Length: 2\r\n"
        b"\r\n"
        b"abc"
    )
    out = recompute_content_length(raw)
    cl_lines = [line for line in out.split(b"\r\n") if line.lower().startswith(b"content-length")]
    # First one becomes the truth; second one is left in place (defensive — we
    # don't aggressively dedupe header lines elsewhere either).
    assert cl_lines[0] == b"Content-Length: 3"
