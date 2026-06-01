"""
Tests for reflect.request_tokens / reflected (reflection highlighting inputs).
"""
from __future__ import annotations

from miniburp.reflect import reflected, request_tokens


def test_extracts_query_values_and_path_segments():
    raw = b"GET /profile/carlos?search=hello-world HTTP/1.1\r\nHost: lab.net\r\n\r\n"
    toks = request_tokens(raw)
    assert "hello-world" in toks
    assert "carlos" in toks
    assert "profile" in toks


def test_extracts_urlencoded_body_values():
    raw = (
        b"POST /login HTTP/1.1\r\nHost: lab.net\r\n"
        b"Content-Type: application/x-www-form-urlencoded\r\n\r\n"
        b"username=wiener&comment=injectme"
    )
    toks = request_tokens(raw)
    assert "wiener" in toks
    assert "injectme" in toks


def test_filters_short_and_numeric_tokens():
    raw = b"GET /a/12/ab?x=1&y=ok123 HTTP/1.1\r\nHost: lab.net\r\n\r\n"
    toks = request_tokens(raw)
    assert "12" not in toks   # pure number
    assert "ab" not in toks   # too short
    assert "ok123" in toks    # alphanumeric, long enough


def test_tokens_sorted_longest_first():
    raw = b"GET /?a=cat&b=category HTTP/1.1\r\nHost: lab.net\r\n\r\n"
    toks = request_tokens(raw)
    # "category" must come before "cat" so it isn't masked by the substring.
    assert toks.index("category") < toks.index("cat")


def test_reflected_finds_present_needles():
    needles = ["injectme", "absent"]
    haystack = "<p>you said: injectme</p>"
    assert reflected(needles, haystack) == ["injectme"]
