"""
Tests for curl.to_curl — the Proxy history "Copy as curl" renderer.
"""
from __future__ import annotations

import shlex

from miniburp.curl import to_curl


def test_simple_get_has_no_dash_x():
    cmd = to_curl("GET", "https://lab.net/image?filename=10.jpg", {"Host": "lab.net"})
    assert cmd.startswith("curl ")
    assert "-X" not in cmd
    assert "'https://lab.net/image?filename=10.jpg'" in cmd
    assert "-H 'Host: lab.net'" in cmd


def test_traversal_payload_url_survives_quoting():
    url = "https://lab.net/image?filename=../../../etc/passwd"
    cmd = to_curl("GET", url, {})
    # The whole URL is single-quoted as one shell token.
    tokens = shlex.split(cmd.replace("\\\n", " "))
    assert url in tokens


def _data_raw_value(cmd: str) -> str:
    """Recover the --data-raw argument by re-parsing the shell tokens."""
    tokens = shlex.split(cmd.replace("\\\n", " "))
    return tokens[tokens.index("--data-raw") + 1]


def test_post_emits_method_and_body():
    cmd = to_curl(
        "POST",
        "https://lab.net/login",
        {"Host": "lab.net", "Content-Type": "application/x-www-form-urlencoded"},
        b"username=carlos&password=hunter2",
    )
    assert "-X POST" in cmd
    assert _data_raw_value(cmd) == "username=carlos&password=hunter2"


def test_content_length_header_is_dropped():
    cmd = to_curl(
        "POST",
        "https://lab.net/x",
        {"Host": "lab.net", "Content-Length": "5"},
        b"hello",
    )
    assert "Content-Length" not in cmd
    assert _data_raw_value(cmd) == "hello"


def test_body_with_single_quote_is_escaped():
    cmd = to_curl("POST", "https://lab.net/x", {}, b"name=O'Brien")
    # Re-parsing the shell tokens recovers the exact body.
    tokens = shlex.split(cmd.replace("\\\n", " "))
    assert "name=O'Brien" in tokens
