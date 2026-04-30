"""
Small encoding/decoding utility — the equivalent of Burp's Decoder tab.

Supports: base64, url, hex, html-entity encode+decode, plus md5/sha1/sha256
hashes. All operations return a (result, error) tuple; on failure the result
is the empty string and error contains a human-readable message.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import html
import urllib.parse


OPS = [
    ("base64-encode", "Base64 encode"),
    ("base64-decode", "Base64 decode"),
    ("url-encode", "URL encode"),
    ("url-decode", "URL decode"),
    ("hex-encode", "Hex encode"),
    ("hex-decode", "Hex decode"),
    ("html-encode", "HTML entity encode"),
    ("html-decode", "HTML entity decode"),
    ("md5", "MD5 hash"),
    ("sha1", "SHA-1 hash"),
    ("sha256", "SHA-256 hash"),
]


def run(op: str, text: str) -> tuple[str, str]:
    try:
        if op == "base64-encode":
            return base64.b64encode(text.encode("utf-8")).decode("ascii"), ""
        if op == "base64-decode":
            # Be lenient: accept URL-safe chars and missing padding.
            s = text.strip().replace("-", "+").replace("_", "/")
            s += "=" * (-len(s) % 4)
            raw = base64.b64decode(s, validate=False)
            try:
                return raw.decode("utf-8"), ""
            except UnicodeDecodeError:
                return raw.decode("iso-8859-1", errors="replace"), ""
        if op == "url-encode":
            return urllib.parse.quote(text, safe=""), ""
        if op == "url-decode":
            return urllib.parse.unquote(text), ""
        if op == "hex-encode":
            return text.encode("utf-8").hex(), ""
        if op == "hex-decode":
            s = text.strip().replace(" ", "").replace("\n", "")
            raw = binascii.unhexlify(s)
            try:
                return raw.decode("utf-8"), ""
            except UnicodeDecodeError:
                return raw.decode("iso-8859-1", errors="replace"), ""
        if op == "html-encode":
            return html.escape(text, quote=True), ""
        if op == "html-decode":
            return html.unescape(text), ""
        if op in ("md5", "sha1", "sha256"):
            return hashlib.new(op, text.encode("utf-8")).hexdigest(), ""
        return "", f"unknown op: {op}"
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"
