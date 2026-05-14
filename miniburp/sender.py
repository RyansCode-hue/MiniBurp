"""
Send an arbitrary raw HTTP request to a target and return the raw response.

Used by the Repeater (single send) and the Intruder (many sends).

Also provides `send_with_redirects` for the Repeater's optional follow-redirects
mode: standard HTTP semantics (301/302/303 rewrite to GET, 307/308 preserve
method+body), with a minimal in-memory cookie jar so multi-hop login flows
survive the chain.
"""
from __future__ import annotations

import socket
import ssl
import time
from typing import Optional
from urllib.parse import urljoin, urlsplit

from .http_io import HTTPParseError, build_request_bytes, read_response


def recompute_content_length(raw_request: bytes) -> bytes:
    """
    Return a copy of `raw_request` with `Content-Length` matching the actual
    body bytes. Adds the header before the blank line if it isn't present.

    Intruder calls this on every attempt because each payload substitution
    changes the body length; sending the original header would either
    truncate the body server-side or hang the connection. Repeater
    intentionally does *not* call this — it ships exactly what the user
    typed, matching Burp's default behavior.
    """
    split = raw_request.find(b"\r\n\r\n")
    if split == -1:
        return raw_request
    header_block = raw_request[:split]
    body = raw_request[split + 4:]
    new_value = f"Content-Length: {len(body)}".encode("iso-8859-1")

    lines = header_block.split(b"\r\n")
    found = False
    for i, line in enumerate(lines):
        # Case-insensitive header name match; preserve the canonical casing
        # by writing our own line on replacement.
        if line.lower().startswith(b"content-length:"):
            lines[i] = new_value
            found = True
            break
    if not found:
        lines.append(new_value)

    return b"\r\n".join(lines) + b"\r\n\r\n" + body


# Status codes that trigger a redirect follow.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# 301/302/303 historically (and in practice for browsers + Burp) coerce a
# request to GET and drop the body. 307/308 preserve the method and body.
_COERCE_TO_GET = frozenset({301, 302, 303})


def _open(host: str, port: int, use_tls: bool, timeout: float = 20.0) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=timeout)
    if use_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=host)
    return sock


def send_raw(
    host: str,
    port: int,
    scheme: str,
    raw_request: bytes,
    timeout: float = 20.0,
) -> dict:
    """
    Send raw bytes to host:port. If the request lacks a Content-Length
    header but has a body, we don't rewrite it — the user is responsible.
    Returns a dict with status, raw response, duration, and any error.
    """
    started = time.time()
    try:
        sock = _open(host, port, use_tls=(scheme == "https"), timeout=timeout)
    except OSError as e:
        return {"error": f"connect failed: {e}", "status": None, "raw": b"", "duration_ms": 0}

    try:
        sock.sendall(raw_request)
        try:
            start_line, headers, body, raw = read_response(sock)
        except HTTPParseError as e:
            return {"error": f"parse: {e}", "status": None, "raw": b"", "duration_ms": int((time.time() - started) * 1000)}
        status = None
        try:
            status = int(start_line.split(" ", 2)[1])
        except Exception:
            pass
        return {
            "error": None,
            "status": status,
            "start_line": start_line,
            "headers": headers,
            "body": body,
            "raw": raw,
            "duration_ms": int((time.time() - started) * 1000),
        }
    finally:
        try:
            sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Redirect-following helpers (pure functions, easy to unit-test)
# ---------------------------------------------------------------------------


def parse_raw_request(raw: bytes) -> tuple[str, str, str, dict[str, str], bytes]:
    """
    Parse raw HTTP request bytes into (method, target, version, headers, body).

    Tolerant: a missing body or malformed header line just yields empty values
    rather than raising — the Repeater hands us whatever the user typed.
    """
    split = raw.find(b"\r\n\r\n")
    if split == -1:
        head_block = raw
        body = b""
    else:
        head_block = raw[:split]
        body = raw[split + 4:]
    text = head_block.decode("iso-8859-1")
    lines = text.split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) != 3:
        raise ValueError(f"bad request line: {lines[0]!r}")
    method, target, version = parts
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers[k.strip()] = v.strip()
    return method, target, version, headers, body


def _request_url(scheme: str, host: str, port: int, target: str) -> str:
    """Reconstruct the absolute URL of a request (used as the urljoin base)."""
    if target.startswith("http://") or target.startswith("https://"):
        return target
    default_port = 443 if scheme == "https" else 80
    host_part = host if port == default_port else f"{host}:{port}"
    if not target.startswith("/"):
        target = "/" + target
    return f"{scheme}://{host_part}{target}"


def extract_set_cookies(raw_response: bytes) -> list[tuple[str, str]]:
    """
    Pull every Set-Cookie line out of a raw response header block.

    We re-scan the raw bytes instead of using the parsed headers dict because
    `parse_headers` collapses repeated headers to the last one — and any real
    session response sets more than one cookie.
    """
    split = raw_response.find(b"\r\n\r\n")
    head = raw_response[:split] if split != -1 else raw_response
    text = head.decode("iso-8859-1")
    out: list[tuple[str, str]] = []
    for line in text.split("\r\n")[1:]:
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        if key.strip().lower() != "set-cookie":
            continue
        # Take name=value before the first attribute separator. We don't honor
        # Path / Domain / Secure / Expires — this is a "make multi-hop login
        # flows work" jar, not a browser.
        cookie_part = val.strip().split(";", 1)[0]
        if "=" in cookie_part:
            name, value = cookie_part.split("=", 1)
            name = name.strip()
            if name:
                out.append((name, value.strip()))
    return out


def _merge_cookies(jar: dict[str, str], new_cookies: list[tuple[str, str]]) -> None:
    for name, value in new_cookies:
        jar[name] = value


def _cookie_header_value(jar: dict[str, str], existing: str = "") -> str:
    """Merge jar entries into any pre-existing Cookie header; jar wins on conflict."""
    parts: list[str] = []
    seen: dict[str, int] = {}  # name -> index in parts
    if existing:
        for piece in existing.split(";"):
            piece = piece.strip()
            if "=" not in piece:
                continue
            name = piece.split("=", 1)[0].strip()
            if not name:
                continue
            seen[name] = len(parts)
            parts.append(piece)
    for name, value in jar.items():
        rendered = f"{name}={value}"
        if name in seen:
            parts[seen[name]] = rendered
        else:
            seen[name] = len(parts)
            parts.append(rendered)
    return "; ".join(parts)


def build_redirect_request(
    prev_scheme: str,
    prev_host: str,
    prev_port: int,
    prev_method: str,
    prev_headers: dict[str, str],
    prev_body: bytes,
    prev_target: str,
    status: int,
    location: str,
    cookie_jar: dict[str, str],
) -> tuple[str, str, int, bytes]:
    """
    Compute the follow-up request for a redirect.

    Returns (new_scheme, new_host, new_port, new_raw_request).

    * Relative `Location` values are resolved against the previous URL.
    * 301/302/303 rewrite the method to GET and drop the body.
    * 307/308 preserve method and body.
    * `Host`, `Content-Length`, and `Connection` are always recomputed.
    * The cookie jar is folded into the `Cookie` header.
    """
    base_url = _request_url(prev_scheme, prev_host, prev_port, prev_target)
    new_url = urljoin(base_url, location)
    parts = urlsplit(new_url)
    new_scheme = parts.scheme or prev_scheme
    new_host = parts.hostname or prev_host
    new_port = parts.port or (443 if new_scheme == "https" else 80)
    new_target = parts.path or "/"
    if parts.query:
        new_target += "?" + parts.query

    if status in _COERCE_TO_GET:
        new_method = "GET"
        new_body = b""
    else:
        new_method = prev_method
        new_body = prev_body

    drop = {"host", "content-length", "connection"}
    if not new_body:
        drop.update({"content-type", "transfer-encoding"})
    new_headers: dict[str, str] = {}
    for k, v in prev_headers.items():
        if k.lower() in drop:
            continue
        new_headers[k] = v

    default_port = 443 if new_scheme == "https" else 80
    new_headers["Host"] = new_host if new_port == default_port else f"{new_host}:{new_port}"
    if new_body:
        new_headers["Content-Length"] = str(len(new_body))

    if cookie_jar:
        new_headers["Cookie"] = _cookie_header_value(cookie_jar, new_headers.get("Cookie", ""))

    raw = build_request_bytes(f"{new_method} {new_target} HTTP/1.1", new_headers, new_body)
    return new_scheme, new_host, new_port, raw


def send_with_redirects(
    host: str,
    port: int,
    scheme: str,
    raw_request: bytes,
    max_redirects: int = 10,
    timeout: float = 20.0,
) -> dict:
    """
    Send a request and follow up to `max_redirects` HTTP redirects.

    Returns the same dict shape as `send_raw` for the *final* hop, plus:

    * ``chain``     — list of ``{"method", "url", "status", "duration_ms"}``
    * ``hops``      — number of redirects followed (0 means no redirect)
    * ``truncated`` — True if we hit ``max_redirects`` with another redirect pending
    * ``total_ms``  — wall-clock time across all hops
    """
    chain: list[dict] = []
    cookie_jar: dict[str, str] = {}
    started_total = time.time()

    try:
        method, target, _version, headers, body = parse_raw_request(raw_request)
    except ValueError as e:
        return {
            "error": f"bad request: {e}",
            "status": None,
            "raw": b"",
            "duration_ms": 0,
            "chain": [],
            "hops": 0,
            "truncated": False,
            "total_ms": 0,
        }

    cur_scheme, cur_host, cur_port = scheme, host, port
    cur_method, cur_target = method, target
    cur_headers, cur_body = headers, body
    cur_raw = raw_request

    result = send_raw(cur_host, cur_port, cur_scheme, cur_raw, timeout=timeout)
    chain.append({
        "method": cur_method,
        "url": _request_url(cur_scheme, cur_host, cur_port, cur_target),
        "status": result.get("status"),
        "duration_ms": result.get("duration_ms"),
    })

    hops = 0
    while (
        result.get("error") is None
        and result.get("status") in REDIRECT_STATUSES
        and hops < max_redirects
    ):
        _merge_cookies(cookie_jar, extract_set_cookies(result.get("raw", b"")))
        # Headers dict is case-sensitive; check both common spellings.
        resp_headers = result.get("headers") or {}
        location = resp_headers.get("Location") or resp_headers.get("location")
        if not location:
            break
        try:
            new_scheme, new_host, new_port, new_raw = build_redirect_request(
                cur_scheme, cur_host, cur_port,
                cur_method, cur_headers, cur_body, cur_target,
                int(result["status"]), location, cookie_jar,
            )
        except Exception as e:
            result = dict(result)
            result["error"] = f"redirect build failed: {e}"
            break

        # Re-parse so subsequent hop has correct method/body/headers context.
        try:
            new_method, new_target, _v, new_headers, new_body = parse_raw_request(new_raw)
        except ValueError as e:
            result = dict(result)
            result["error"] = f"redirect parse failed: {e}"
            break

        cur_scheme, cur_host, cur_port = new_scheme, new_host, new_port
        cur_method, cur_target = new_method, new_target
        cur_headers, cur_body = new_headers, new_body
        cur_raw = new_raw
        hops += 1

        result = send_raw(cur_host, cur_port, cur_scheme, cur_raw, timeout=timeout)
        chain.append({
            "method": cur_method,
            "url": _request_url(cur_scheme, cur_host, cur_port, cur_target),
            "status": result.get("status"),
            "duration_ms": result.get("duration_ms"),
        })

    truncated = (
        result.get("error") is None
        and result.get("status") in REDIRECT_STATUSES
        and hops >= max_redirects
    )
    total_ms = int((time.time() - started_total) * 1000)
    out = dict(result)
    out["chain"] = chain
    out["hops"] = hops
    out["truncated"] = truncated
    out["total_ms"] = total_ms
    return out
