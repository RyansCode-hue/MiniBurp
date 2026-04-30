"""
Decode compressed response bodies for display.

Looks at the `Content-Encoding` header and tries gzip, deflate, or brotli.
Brotli requires the `brotli` package; if unavailable we fall back to the
raw bytes and note the missing dependency.
"""
from __future__ import annotations

import gzip
import zlib

try:
    import brotli  # type: ignore
    HAVE_BROTLI = True
except ImportError:
    HAVE_BROTLI = False


def decode_body(body: bytes, headers: dict) -> tuple[bytes, str]:
    """
    Return (decoded_bytes, note). `note` is '' on success or the raw
    encoding name we couldn't decode.
    """
    enc = ""
    for k, v in headers.items():
        if k.lower() == "content-encoding":
            enc = v.lower().strip()
            break

    if not enc or enc == "identity":
        return body, ""

    try:
        if enc == "gzip":
            return gzip.decompress(body), ""
        if enc == "deflate":
            # Some servers send raw deflate, others zlib-wrapped. Try both.
            try:
                return zlib.decompress(body), ""
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS), ""
        if enc == "br":
            if not HAVE_BROTLI:
                return body, "br (install 'brotli' package to decode)"
            return brotli.decompress(body), ""
    except Exception as e:
        return body, f"{enc} (decode failed: {e})"

    return body, enc  # unknown encoding
