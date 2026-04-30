"""
Tests for the Decoder utility — Burp's Decoder tab equivalent.

We exercise every operation in OPS via round-trips and known-vector hashes.
No Qt, no networking — these tests run anywhere Python does.
"""
from __future__ import annotations

import pytest

from miniburp.decoder import OPS, run


@pytest.mark.parametrize("op_id, _label", OPS)
def test_every_op_returns_two_string_tuple(op_id: str, _label: str) -> None:
    """Every op returns (str, str). Empty input shouldn't crash."""
    result, err = run(op_id, "")
    assert isinstance(result, str)
    assert isinstance(err, str)


def test_unknown_op_returns_error() -> None:
    result, err = run("nonexistent-op", "hello")
    assert result == ""
    assert "unknown op" in err


# ---- base64 -----------------------------------------------------------------


def test_base64_round_trip_ascii() -> None:
    encoded, err = run("base64-encode", "hello world")
    assert err == ""
    assert encoded == "aGVsbG8gd29ybGQ="
    decoded, err = run("base64-decode", encoded)
    assert err == ""
    assert decoded == "hello world"


def test_base64_decode_handles_missing_padding() -> None:
    # "Man" -> "TWFu" (no padding required)
    # "Ma"  -> "TWE=" (one '=' of padding)
    # If a user pastes "TWE" without padding, we should still decode.
    decoded, err = run("base64-decode", "TWE")
    assert err == ""
    assert decoded == "Ma"


def test_base64_decode_handles_url_safe_alphabet() -> None:
    # base64-url replaces +/ with -_
    decoded, err = run("base64-decode", "_-_-")
    assert err == ""  # any 4-char string with valid alphabet decodes


# ---- url --------------------------------------------------------------------


def test_url_encode_escapes_reserved_characters() -> None:
    encoded, err = run("url-encode", "hello world?foo=bar&baz=qux")
    assert err == ""
    assert encoded == "hello%20world%3Ffoo%3Dbar%26baz%3Dqux"


def test_url_round_trip() -> None:
    original = "tag=<script>alert('x')</script>"
    encoded, _ = run("url-encode", original)
    decoded, _ = run("url-decode", encoded)
    assert decoded == original


# ---- hex --------------------------------------------------------------------


def test_hex_round_trip() -> None:
    # M=0x4d i=0x69 n=0x6e i=0x69 B=0x42 u=0x75 r=0x72 p=0x70
    encoded, err = run("hex-encode", "MiniBurp")
    assert err == ""
    assert encoded == "4d696e6942757270"
    decoded, err = run("hex-decode", encoded)
    assert err == ""
    assert decoded == "MiniBurp"


def test_hex_decode_strips_whitespace() -> None:
    # Spaces and newlines in pasted hex are common; the decoder handles them.
    decoded, err = run("hex-decode", "48 65 6c 6c\n6f")
    assert err == ""
    assert decoded == "Hello"


def test_hex_decode_invalid_returns_error() -> None:
    _, err = run("hex-decode", "not-hex")
    assert err != ""


# ---- html entities ----------------------------------------------------------


def test_html_round_trip() -> None:
    encoded, _ = run("html-encode", '<a href="x">&</a>')
    assert "&lt;" in encoded and "&amp;" in encoded and "&quot;" in encoded
    decoded, _ = run("html-decode", encoded)
    assert decoded == '<a href="x">&</a>'


# ---- hashes -----------------------------------------------------------------


def test_md5_known_vector() -> None:
    # The PortSwigger Web Security Academy "offline password cracking" lab
    # explicitly references md5("password") -> 5f4dcc3b5aa765d61d8327deb882cf99.
    digest, err = run("md5", "password")
    assert err == ""
    assert digest == "5f4dcc3b5aa765d61d8327deb882cf99"


def test_sha1_known_vector() -> None:
    digest, err = run("sha1", "abc")
    assert err == ""
    assert digest == "a9993e364706816aba3e25717850c26c9cd0d89d"


def test_sha256_known_vector() -> None:
    digest, err = run("sha256", "abc")
    assert err == ""
    assert digest == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
