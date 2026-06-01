"""
Low-level helpers for reading and writing HTTP/1.1 messages over sockets.

We deliberately do not use http.client here because we want the exact
raw bytes both ways. That gives the Repeater and Intruder authentic
material to replay and mutate.
"""
from __future__ import annotations

import socket
from typing import Optional


class HTTPParseError(Exception):
    pass


def _read_until_double_crlf(sock: socket.socket) -> bytes:
    """Read from sock until we see the end of HTTP headers."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            if not buf:
                return b""
            raise HTTPParseError("connection closed before end of headers")
        buf += chunk
        if len(buf) > 1024 * 1024:  # 1 MB header cap
            raise HTTPParseError("headers too large")
    return buf


def _recv_exact(sock: socket.socket, n: int, initial: bytes = b"") -> bytes:
    """Read exactly n bytes, starting with any leftover we already have."""
    buf = bytearray(initial)
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise HTTPParseError(f"connection closed with {n - len(buf)} bytes still expected")
        buf.extend(chunk)
    return bytes(buf)


def _recv_until_close(sock: socket.socket, initial: bytes = b"") -> bytes:
    buf = bytearray(initial)
    while True:
        try:
            chunk = sock.recv(65536)
        except OSError:
            break
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def _read_chunked_body(sock: socket.socket, initial: bytes = b"") -> bytes:
    """Decode a Transfer-Encoding: chunked body. Returns the DECODED bytes."""
    buf = bytearray(initial)
    out = bytearray()

    def ensure(min_bytes: int) -> None:
        while len(buf) < min_bytes:
            chunk = sock.recv(4096)
            if not chunk:
                raise HTTPParseError("connection closed mid-chunk")
            buf.extend(chunk)

    while True:
        # find end of chunk-size line
        while b"\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise HTTPParseError("connection closed reading chunk size")
            buf.extend(chunk)
        line_end = buf.index(b"\r\n")
        size_line = bytes(buf[:line_end])
        del buf[: line_end + 2]
        size_hex = size_line.split(b";", 1)[0].strip()
        try:
            size = int(size_hex, 16)
        except ValueError:
            raise HTTPParseError(f"bad chunk size: {size_line!r}")
        if size == 0:
            # consume trailing CRLF (and any trailer headers we ignore)
            while b"\r\n\r\n" not in buf and b"\r\n" not in buf[:2]:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
            return bytes(out)
        ensure(size + 2)
        out.extend(buf[:size])
        del buf[: size + 2]  # chunk data + trailing CRLF


def parse_headers(header_block: bytes) -> tuple[str, dict[str, str]]:
    """Split the raw header block into (start_line, {header: value})."""
    text = header_block.decode("iso-8859-1")
    lines = text.split("\r\n")
    start_line = lines[0]
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers[k.strip()] = v.strip()
    return start_line, headers


def read_request(sock: socket.socket) -> Optional[tuple[str, dict[str, str], bytes, bytes]]:
    """
    Read a full HTTP request from sock.

    Returns (start_line, headers, body, raw_bytes) or None if the peer closed
    cleanly before sending anything.
    """
    data = _read_until_double_crlf(sock)
    if not data:
        return None
    split = data.index(b"\r\n\r\n")
    header_block = data[:split]
    leftover = data[split + 4 :]
    start_line, headers = parse_headers(header_block)

    body = b""
    if "Content-Length" in headers:
        length = int(headers["Content-Length"])
        body = _recv_exact(sock, length, initial=leftover)
    elif headers.get("Transfer-Encoding", "").lower() == "chunked":
        body = _read_chunked_body(sock, initial=leftover)

    raw = header_block + b"\r\n\r\n" + body
    return start_line, headers, body, raw


def read_response(sock: socket.socket) -> tuple[str, dict[str, str], bytes, bytes]:
    """Read a full HTTP response from sock. Returns (start_line, headers, body, raw)."""
    data = _read_until_double_crlf(sock)
    if not data:
        raise HTTPParseError("empty response")
    split = data.index(b"\r\n\r\n")
    header_block = data[:split]
    leftover = data[split + 4 :]
    start_line, headers = parse_headers(header_block)

    body = b""
    if "Content-Length" in headers:
        body = _recv_exact(sock, int(headers["Content-Length"]), initial=leftover)
    elif headers.get("Transfer-Encoding", "").lower() == "chunked":
        body = _read_chunked_body(sock, initial=leftover)
    else:
        # Read until close
        body = _recv_until_close(sock, initial=leftover)

    raw = header_block + b"\r\n\r\n" + body
    return start_line, headers, body, raw


def build_request_bytes(start_line: str, headers: dict[str, str], body: bytes) -> bytes:
    """Serialize a request for sending upstream."""
    lines = [start_line]
    for k, v in headers.items():
        lines.append(f"{k}: {v}")
    head = ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")
    return head + body


def build_response_bytes(start_line: str, headers: dict[str, str], body: bytes) -> bytes:
    return build_request_bytes(start_line, headers, body)  # same shape


def ensure_content_length(headers: dict[str, str], body: bytes) -> dict[str, str]:
    """Set ``Content-Length`` to ``len(body)`` when a body is present and no
    Content-Length header survived.

    Mutates and returns ``headers`` for convenience. This is the request-side
    mirror of what the response path already does: ``read_request`` decodes a
    ``Transfer-Encoding: chunked`` body into plain bytes and the proxy strips
    ``transfer-encoding`` when forwarding, leaving a body with no length
    declaration. Without this, upstream treats the request as zero-length
    (dropping the payload) or reads until close. No-op for empty bodies or
    when a Content-Length is already present (case-insensitive).
    """
    if not body:
        return headers
    for k in headers:
        if k.lower() == "content-length":
            return headers
    headers["Content-Length"] = str(len(body))
    return headers


def validate_request_bytes(raw: bytes) -> Optional[str]:
    """Return a human-readable error if ``raw`` isn't a forwardable request.

    Guards the proxy against a hand-edited Intercept request whose start line
    lost a token (deleted HTTP version, double-spaced path) or whose Host header
    was removed — both would otherwise raise deep inside the worker
    (``start_line.split(" ", 2)``) and drop the connection with no feedback.
    Returns ``None`` when the request looks well-formed.
    """
    split = raw.find(b"\r\n\r\n")
    head = raw[:split] if split != -1 else raw
    if not head.strip():
        return "empty request"
    try:
        text = head.decode("iso-8859-1")
    except Exception:
        return "request headers are not decodable"
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3:
        return f"request line must be 'METHOD TARGET HTTP/x.y' (got {lines[0]!r})"
    method, target, version = parts
    if not method or not target:
        return "request line is missing the method or target"
    if not version.upper().startswith("HTTP/"):
        return f"request line version must look like HTTP/1.1 (got {version!r})"
    has_host = any(
        line.split(":", 1)[0].strip().lower() == "host"
        for line in lines[1:]
        if ":" in line
    )
    if not has_host:
        return "request is missing a Host header"
    return None


def parse_status_code(status_line: str) -> Optional[int]:
    """Pull the numeric status out of a response start line, tolerantly.

    Returns ``None`` for a truncated or non-standard status line (e.g.
    ``HTTP/1.1`` with no code) instead of raising — a single malformed
    upstream response shouldn't tear down a keep-alive connection.
    """
    try:
        return int(status_line.split(" ", 2)[1])
    except (IndexError, ValueError):
        return None
