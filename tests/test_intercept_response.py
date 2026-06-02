"""
Tests for response interception in the Intercept coordinator + the response
parser used to re-deliver an edited response.
"""
from __future__ import annotations

import threading

from miniburp.http_io import parse_response_bytes
from miniburp.intercept import Intercept, InterceptResult


def test_disabled_response_hold_returns_forward_immediately():
    ic = Intercept()
    # responses_enabled defaults False — must not block.
    action, raw = ic.hold_response(1, b"HTTP/1.1 200 OK\r\n\r\nhi")
    assert action == InterceptResult.FORWARD
    assert raw == b"HTTP/1.1 200 OK\r\n\r\nhi"


def test_request_and_response_toggles_are_independent():
    ic = Intercept()
    ic.set_responses_enabled(True)
    assert ic.is_responses_enabled() is True
    assert ic.is_enabled() is False  # request interception untouched
    ic.set_enabled(True)
    ic.set_responses_enabled(False)
    assert ic.is_enabled() is True
    assert ic.is_responses_enabled() is False


def test_forward_drop_unknown_response_ids_return_false():
    ic = Intercept()
    assert ic.forward_response(999) is False
    assert ic.drop_response(999) is False


def test_response_hold_roundtrip_forward_with_edit():
    ic = Intercept()
    ic.set_responses_enabled(True)
    original = b"HTTP/1.1 200 OK\r\nSet-Cookie: Admin=false\r\n\r\nbody"
    edited = b"HTTP/1.1 200 OK\r\nSet-Cookie: Admin=true\r\n\r\nbody"
    result = {}

    def worker():
        result["action"], result["bytes"] = ic.hold_response(7, original)

    t = threading.Thread(target=worker)
    t.start()
    # Wait until the proxy thread has registered the hold, then edit+forward.
    assert _wait(lambda: 7 in ic.held_response_ids())
    assert ic.held_response_bytes(7) == original
    assert ic.forward_response(7, edited) is True
    t.join(timeout=2)
    assert result["action"] == InterceptResult.FORWARD
    assert result["bytes"] == edited
    # Request-side hold store is unaffected.
    assert ic.held_ids() == []


def test_response_hold_drop():
    ic = Intercept()
    ic.set_responses_enabled(True)
    result = {}

    def worker():
        result["action"], result["bytes"] = ic.hold_response(3, b"HTTP/1.1 200 OK\r\n\r\n")

    t = threading.Thread(target=worker)
    t.start()
    assert _wait(lambda: 3 in ic.held_response_ids())
    assert ic.drop_response(3) is True
    t.join(timeout=2)
    assert result["action"] == InterceptResult.DROP
    assert result["bytes"] is None


def test_disabling_responses_releases_held():
    ic = Intercept()
    ic.set_responses_enabled(True)
    result = {}

    def worker():
        result["action"], result["bytes"] = ic.hold_response(5, b"HTTP/1.1 200 OK\r\n\r\nx")

    t = threading.Thread(target=worker)
    t.start()
    assert _wait(lambda: 5 in ic.held_response_ids())
    ic.set_responses_enabled(False)  # should release as forward-unchanged
    t.join(timeout=2)
    assert result["action"] == InterceptResult.FORWARD
    assert result["bytes"] == b"HTTP/1.1 200 OK\r\n\r\nx"


def test_parse_response_bytes_roundtrip():
    raw = b"HTTP/1.1 302 Found\r\nLocation: /\r\nSet-Cookie: Admin=true\r\n\r\nbody!"
    start, headers, body = parse_response_bytes(raw)
    assert start == "HTTP/1.1 302 Found"
    assert headers["Set-Cookie"] == "Admin=true"
    assert headers["Location"] == "/"
    assert body == b"body!"


def _wait(pred, timeout: float = 2.0) -> bool:
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False
