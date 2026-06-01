"""
Tests for the request parameter parse/serialize layer (params.py).
"""
from __future__ import annotations

from miniburp.params import Param, apply_params, parse_params


def test_parses_query_cookie_and_body():
    raw = (
        b"POST /search?q=hello&page=2 HTTP/1.1\r\n"
        b"Host: lab.net\r\n"
        b"Cookie: session=abc; theme=dark\r\n"
        b"Content-Type: application/x-www-form-urlencoded\r\n"
        b"\r\n"
        b"role=user&csrf=tok"
    )
    params = parse_params(raw)
    locs = {(p.location, p.name): p.value for p in params}
    assert locs[("query", "q")] == "hello"
    assert locs[("query", "page")] == "2"
    assert locs[("cookie", "session")] == "abc"
    assert locs[("cookie", "theme")] == "dark"
    assert locs[("body", "role")] == "user"
    assert locs[("body", "csrf")] == "tok"


def test_edit_query_value_writes_back():
    raw = b"GET /image?filename=10.jpg HTTP/1.1\r\nHost: lab.net\r\n\r\n"
    params = parse_params(raw)
    params[0].value = "../../../etc/passwd"
    out = apply_params(raw, params)
    # Slashes stay literal — the traversal payload isn't percent-mangled.
    assert b"GET /image?filename=../../../etc/passwd HTTP/1.1" in out


def test_edit_body_value_recomputes_content_length():
    raw = (
        b"POST /login HTTP/1.1\r\nHost: lab.net\r\n"
        b"Content-Type: application/x-www-form-urlencoded\r\n"
        b"Content-Length: 9\r\n\r\nrole=user"
    )
    params = parse_params(raw)
    for p in params:
        if p.name == "role":
            p.value = "administrator"
    out = apply_params(raw, params)
    assert b"role=administrator" in out
    assert b"Content-Length: 18" in out  # len("role=administrator")


def test_edit_cookie_value_writes_back():
    raw = b"GET / HTTP/1.1\r\nHost: lab.net\r\nCookie: admin=false\r\n\r\n"
    params = parse_params(raw)
    params[0].value = "true"
    out = apply_params(raw, params)
    assert b"Cookie: admin=true" in out


def test_json_body_is_not_treated_as_params():
    raw = (
        b"POST /api HTTP/1.1\r\nHost: lab.net\r\n"
        b"Content-Type: application/json\r\n\r\n"
        b'{"role": "user"}'
    )
    params = parse_params(raw)
    assert all(p.location != "body" for p in params)
    # Round trip leaves the JSON body intact.
    out = apply_params(raw, params)
    assert b'{"role": "user"}' in out


def test_round_trip_without_edits_is_stable():
    raw = b"GET /a?x=1&y=two HTTP/1.1\r\nHost: lab.net\r\n\r\n"
    out = apply_params(raw, parse_params(raw))
    assert b"GET /a?x=1&y=two HTTP/1.1" in out
    assert b"Host: lab.net" in out
