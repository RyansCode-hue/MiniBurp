"""
Tests for response body decoding (gzip / deflate / brotli / identity).
"""
from __future__ import annotations

import gzip
import zlib

from miniburp.bodydecode import HAVE_BROTLI, decode_body


def test_identity_returns_unchanged() -> None:
    body = b"raw body"
    out, note = decode_body(body, {"Content-Encoding": "identity"})
    assert out == body
    assert note == ""


def test_no_encoding_header_returns_unchanged() -> None:
    body = b"raw body"
    out, note = decode_body(body, {})
    assert out == body
    assert note == ""


def test_gzip_decodes() -> None:
    payload = b"hello gzip world"
    compressed = gzip.compress(payload)
    out, note = decode_body(compressed, {"Content-Encoding": "gzip"})
    assert out == payload
    assert note == ""


def test_deflate_zlib_wrapped() -> None:
    payload = b"hello deflate"
    compressed = zlib.compress(payload)
    out, note = decode_body(compressed, {"Content-Encoding": "deflate"})
    assert out == payload
    assert note == ""


def test_deflate_raw_falls_through() -> None:
    """Some servers send raw deflate (no zlib header). bodydecode handles both."""
    payload = b"hello raw deflate"
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)  # raw deflate
    raw_compressed = compressor.compress(payload) + compressor.flush()
    out, note = decode_body(raw_compressed, {"Content-Encoding": "deflate"})
    assert out == payload
    assert note == ""


def test_unknown_encoding_returns_raw_with_note() -> None:
    out, note = decode_body(b"unchanged", {"Content-Encoding": "rot13"})
    assert out == b"unchanged"
    assert note == "rot13"


def test_header_lookup_is_case_insensitive() -> None:
    payload = b"case test"
    compressed = gzip.compress(payload)
    out, note = decode_body(compressed, {"content-encoding": "gzip"})
    assert out == payload
    assert note == ""


def test_brotli_when_available() -> None:
    if not HAVE_BROTLI:
        # If brotli isn't installed in this CI env, the call must still be
        # safe and produce a helpful note.
        out, note = decode_body(b"\x00", {"Content-Encoding": "br"})
        assert out == b"\x00"
        assert "brotli" in note.lower()
        return

    import brotli  # type: ignore

    payload = b"hello brotli"
    compressed = brotli.compress(payload)
    out, note = decode_body(compressed, {"Content-Encoding": "br"})
    assert out == payload
    assert note == ""
