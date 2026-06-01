"""
Tests for the Match & Replace request engine (rules.py).
"""
from __future__ import annotations

from miniburp.rules import Rule, Ruleset

_REQ = (
    b"GET /image?filename=10.jpg HTTP/1.1\r\n"
    b"Host: lab.net\r\n"
    b"Accept: */*\r\n"
    b"\r\n"
)


def _rs(*rules: Rule) -> Ruleset:
    rs = Ruleset()
    rs.set_rules(list(rules))
    return rs


def test_no_rules_returns_input_unchanged():
    assert Ruleset().apply_request(_REQ) == _REQ


def test_request_line_literal_replace():
    rs = _rs(Rule("req-line", "10.jpg", "../../../etc/passwd"))
    out = rs.apply_request(_REQ)
    assert out.split(b"\r\n", 1)[0] == b"GET /image?filename=../../../etc/passwd HTTP/1.1"
    # headers untouched
    assert b"Host: lab.net" in out


def test_header_injection_via_regex_on_host_line():
    # Append an X-Forwarded-For header after Host using a regex backreference.
    rs = _rs(Rule("req-header", r"(Host: .*)", r"\1\r\nX-Forwarded-For: 127.0.0.1", is_regex=True))
    out = rs.apply_request(_REQ)
    assert b"X-Forwarded-For: 127.0.0.1" in out
    assert b"Host: lab.net" in out


def test_body_replace_changes_only_body():
    req = b"POST /login HTTP/1.1\r\nHost: lab.net\r\n\r\nrole=user"
    rs = _rs(Rule("req-body", "role=user", "role=admin"))
    out = rs.apply_request(req)
    assert out.endswith(b"\r\n\r\nrole=admin")
    assert b"POST /login HTTP/1.1" in out


def test_disabled_rule_is_skipped():
    rs = _rs(Rule("req-line", "10.jpg", "passwd", enabled=False))
    assert rs.apply_request(_REQ) == _REQ


def test_rules_compose_in_order():
    rs = _rs(
        Rule("req-line", "10.jpg", "20.jpg"),
        Rule("req-line", "20.jpg", "30.jpg"),
    )
    out = rs.apply_request(_REQ)
    assert b"filename=30.jpg" in out


def test_invalid_regex_is_noop_not_crash():
    rs = _rs(Rule("req-line", "(unterminated", "x", is_regex=True))
    assert rs.apply_request(_REQ) == _REQ


def test_empty_literal_match_is_noop():
    rs = _rs(Rule("req-body", "", "injected"))
    req = b"POST / HTTP/1.1\r\nHost: a\r\n\r\nbody"
    assert rs.apply_request(req) == req


def test_target_only_touches_its_segment():
    # A req-body rule must not alter a matching string in the request line.
    rs = _rs(Rule("req-body", "lab.net", "evil.net"))
    out = rs.apply_request(_REQ)
    assert b"Host: lab.net" in out  # header untouched


def test_has_active_request_rules():
    rs = _rs(Rule("req-line", "a", "b", enabled=False))
    assert rs.has_active_request_rules() is False
    rs.set_rules([Rule("req-line", "a", "b")])
    assert rs.has_active_request_rules() is True


def test_proxy_helper_respects_scope_and_fixes_content_length():
    """The proxy wiring gates on scope and keeps Content-Length honest when a
    body rule changes the length."""
    from miniburp.proxy import _apply_request_rules
    from miniburp.rules import RULES
    from miniburp.scope import SCOPE

    req = b"POST /x HTTP/1.1\r\nHost: lab.net\r\nContent-Length: 9\r\n\r\nrole=user"
    new_body = b"role=administrator"
    try:
        RULES.set_rules([Rule("req-body", "role=user", "role=administrator")])
        SCOPE.set_patterns(["lab.net"])

        out = _apply_request_rules("lab.net", req)
        assert new_body in out
        assert f"Content-Length: {len(new_body)}".encode() in out

        # Out-of-scope host is left completely untouched.
        assert _apply_request_rules("other.net", req) == req
    finally:
        RULES.set_rules([])
        SCOPE.set_patterns([])


def test_proxy_helper_emptying_body_zeroes_content_length():
    """A rule that empties the body must drop Content-Length to 0, not leave a
    stale length that hangs the upstream."""
    from miniburp.proxy import _apply_request_rules
    from miniburp.rules import RULES
    from miniburp.scope import SCOPE

    req = b"POST /x HTTP/1.1\r\nHost: lab.net\r\nContent-Length: 7\r\n\r\npayload"
    try:
        RULES.set_rules([Rule("req-body", "payload", "")])
        SCOPE.set_patterns(["lab.net"])
        out = _apply_request_rules("lab.net", req)
        assert out.endswith(b"\r\n\r\n")  # body gone
        assert b"Content-Length: 0" in out
        assert b"Content-Length: 7" not in out
    finally:
        RULES.set_rules([])
        SCOPE.set_patterns([])
