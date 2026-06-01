"""
Parse a raw HTTP request into editable parameters and serialize edits back.

Backs the "Params" panel in the request editors: a key/value view of the query
string, Cookie pairs, and urlencoded body so a learner can change one field
without fumbling raw request-line spacing — and see plainly where parameters
live.

Kept headless and tested directly. Query/body values are re-encoded with ``/``
left literal, so a hand-entered traversal payload like ``../../../etc/passwd``
survives the round trip instead of becoming ``..%2F..%2F..%2Fetc%2Fpasswd``.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_FORM_CT = "application/x-www-form-urlencoded"
# Keep '/' literal so traversal/path payloads aren't percent-mangled.
_SAFE = "/"


@dataclass
class Param:
    location: str   # "query" | "cookie" | "body"
    name: str
    value: str


def _split(raw: bytes) -> tuple[bytes, list[bytes], bytes]:
    sp = raw.find(b"\r\n\r\n")
    if sp == -1:
        head, body = raw, b""
    else:
        head, body = raw[:sp], raw[sp + 4:]
    lines = head.split(b"\r\n")
    return (lines[0] if lines else b""), lines[1:], body


def _header_value(header_lines: list[bytes], name: str) -> str | None:
    nl = name.lower().encode()
    for line in header_lines:
        if b":" in line and line.split(b":", 1)[0].strip().lower() == nl:
            return line.split(b":", 1)[1].strip().decode("iso-8859-1")
    return None


def _body_is_form(header_lines: list[bytes], body: bytes) -> bool:
    ct = (_header_value(header_lines, "Content-Type") or "").lower()
    if _FORM_CT in ct:
        return True
    # Heuristic for an unlabelled form: single-line key=value pairs.
    text = body.decode("iso-8859-1", errors="replace")
    return bool(body) and "=" in text and "\n" not in text and " " not in text


def parse_params(raw: bytes) -> list[Param]:
    """Extract query, cookie, and urlencoded-body params from a raw request."""
    req_line, header_lines, body = _split(raw)
    out: list[Param] = []

    parts = req_line.decode("iso-8859-1", errors="replace").split(" ")
    if len(parts) >= 2:
        for k, v in parse_qsl(urlsplit(parts[1]).query, keep_blank_values=True):
            out.append(Param("query", k, v))

    cookie = _header_value(header_lines, "Cookie")
    if cookie:
        for pair in cookie.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                out.append(Param("cookie", k.strip(), v.strip()))

    if _body_is_form(header_lines, body):
        for k, v in parse_qsl(body.decode("iso-8859-1", errors="replace"), keep_blank_values=True):
            out.append(Param("body", k, v))

    return out


def apply_params(raw: bytes, params: list[Param]) -> bytes:
    """Write ``params`` back into ``raw``, returning new request bytes.

    Replaces the request-line query string, the Cookie header, and the body
    (when body params are present). Content-Length is recomputed when the body
    is rewritten. Non-form bodies (JSON, etc.) are left untouched because
    ``parse_params`` produces no body params for them.
    """
    req_line, header_lines, body = _split(raw)
    had_blank = b"\r\n\r\n" in raw

    query = [(p.name, p.value) for p in params if p.location == "query"]
    cookies = [(p.name, p.value) for p in params if p.location == "cookie"]
    body_params = [(p.name, p.value) for p in params if p.location == "body"]
    has_body_params = any(p.location == "body" for p in params)

    # ---- request-line query ----
    parts = req_line.decode("iso-8859-1", errors="replace").split(" ")
    if len(parts) >= 2:
        sp = urlsplit(parts[1])
        new_q = urlencode(query, safe=_SAFE)
        parts[1] = urlunsplit(("", "", sp.path or "/", new_q, sp.fragment))
        req_line = " ".join(parts).encode("iso-8859-1")

    # ---- Cookie header ----
    new_cookie = "; ".join(f"{k}={v}" for k, v in cookies)
    rebuilt: list[bytes] = []
    cookie_seen = False
    for line in header_lines:
        if b":" in line and line.split(b":", 1)[0].strip().lower() == b"cookie":
            cookie_seen = True
            if new_cookie:
                rebuilt.append(("Cookie: " + new_cookie).encode("iso-8859-1"))
            # else: drop an emptied Cookie header
        else:
            rebuilt.append(line)
    if cookies and not cookie_seen:
        rebuilt.append(("Cookie: " + new_cookie).encode("iso-8859-1"))
    header_lines = rebuilt

    # ---- body ----
    if has_body_params:
        body = urlencode(body_params, safe=_SAFE).encode("iso-8859-1")

    head = req_line if not header_lines else req_line + b"\r\n" + b"\r\n".join(header_lines)
    out = head + (b"\r\n\r\n" + body if had_blank else b"")

    if has_body_params:
        from .sender import recompute_content_length
        out = recompute_content_length(out)
    return out
