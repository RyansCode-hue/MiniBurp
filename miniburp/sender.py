"""
Send an arbitrary raw HTTP request to a target and return the raw response.

Used by the Repeater (single send) and the Intruder (many sends).
"""
from __future__ import annotations

import socket
import ssl
import time
from typing import Optional

from .http_io import HTTPParseError, read_response


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
