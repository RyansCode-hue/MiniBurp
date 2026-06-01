"""
Core intercepting proxy.

Listens on a local port. For each incoming connection:

* If the first line is CONNECT host:port, we answer 200, wrap the client
  socket with TLS using a freshly-minted cert for `host`, and open a real
  TLS connection to `host:port` upstream. Then we read and forward HTTP
  messages in plaintext, logging them.
* Otherwise we treat it as a plain HTTP request, parse it, and forward.

Each completed transaction lands in the shared STORE so the web UI can
display it, replay it (Repeater), or fuzz it (Intruder).
"""
from __future__ import annotations

import select
import socket
import ssl
import threading
import traceback
from typing import Optional
from urllib.parse import urlsplit

from . import ca
from .http_io import (
    HTTPParseError,
    build_request_bytes,
    ensure_content_length,
    parse_status_code,
    read_request,
    read_response,
    validate_request_bytes,
)
from .intercept import INTERCEPT, InterceptResult
from .noise import is_noise
from .rules import RULES
from .scope import SCOPE
from .sender import recompute_content_length
from .storage import STORE


# ---------------------------------------------------------------------------
# Upstream helpers
# ---------------------------------------------------------------------------


def _open_upstream(host: str, port: int, use_tls: bool) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=30)
    if use_tls:
        ctx = ssl.create_default_context()
        # In a real tool you'd validate; here we prioritize being able to
        # reach labs that may use self-signed certs. Document this trade-off.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=host)
    return sock


def _absolute_to_relative(path_or_url: str) -> tuple[str, Optional[str], Optional[int]]:
    """
    If the request line has an absolute URL (as plain-HTTP proxies receive),
    split out the path and Host info. Returns (path, host_or_None, port_or_None).
    """
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        parts = urlsplit(path_or_url)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
        rel = parts.path or "/"
        if parts.query:
            rel += "?" + parts.query
        return rel, host, port
    return path_or_url, None, None


def _apply_request_rules(host: str, raw: bytes) -> bytes:
    """Run Match & Replace request rules for in-scope hosts, keeping
    Content-Length honest if a body rule shifted the body length."""
    if not SCOPE.in_scope(host):
        return raw
    modified = RULES.apply_request(raw)
    if modified == raw:
        return raw
    sp = modified.find(b"\r\n\r\n")
    if sp != -1 and modified[sp + 4:]:
        modified = recompute_content_length(modified)
    return modified


def _sanitize_headers_for_forward(headers: dict[str, str]) -> dict[str, str]:
    """Strip hop-by-hop headers and force Connection: close for simplicity."""
    hop_by_hop = {
        "proxy-connection",
        "connection",
        "keep-alive",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
    out = {k: v for k, v in headers.items() if k.lower() not in hop_by_hop}
    out["Connection"] = "close"
    return out


# ---------------------------------------------------------------------------
# Per-connection handler
# ---------------------------------------------------------------------------


class ProxyHandler:
    def __init__(self, client_sock: socket.socket, client_addr) -> None:
        self.client = client_sock
        self.client_addr = client_addr

    # ---- entry ----------------------------------------------------------
    def handle(self) -> None:
        try:
            self.client.settimeout(60)
            first = read_request(self.client)
            if first is None:
                return
            start_line, headers, body, raw = first
            method = start_line.split(" ", 1)[0].upper()
            if method == "CONNECT":
                self._handle_connect(start_line, headers)
            else:
                self._handle_plain(start_line, headers, body, raw)
        except (HTTPParseError, ConnectionError, socket.timeout, ssl.SSLError) as e:
            print(f"[proxy] {self.client_addr} error: {e}")
        except Exception:
            traceback.print_exc()
        finally:
            try:
                self.client.close()
            except OSError:
                pass

    # ---- plain HTTP -----------------------------------------------------
    def _handle_plain(
        self,
        start_line: str,
        headers: dict[str, str],
        body: bytes,
        raw: bytes,
    ) -> None:
        method, target, version = start_line.split(" ", 2)
        rel, abs_host, abs_port = _absolute_to_relative(target)
        host_header = headers.get("Host", "")
        host = abs_host or host_header.split(":", 1)[0]
        if abs_port is not None:
            port = abs_port
        elif ":" in host_header:
            port = int(host_header.split(":", 1)[1])
        else:
            port = 80

        if not host:
            self._send_error(400, "Bad Request: missing Host")
            return

        # Lab-only invariant: drop background chatter on the floor instead of
        # logging it. Chrome's plain-HTTP chatter is rare; close the connection
        # and let the client deal with it (most chatter is best-effort anyway).
        if is_noise(host):
            return

        # Rebuild the start line with a relative path for upstream.
        forwarded_start = f"{method} {rel} {version}"
        forwarded_headers = _sanitize_headers_for_forward(headers)
        forwarded_headers.setdefault("Host", host if port == 80 else f"{host}:{port}")
        ensure_content_length(forwarded_headers, body)
        upstream_request = build_request_bytes(forwarded_start, forwarded_headers, body)
        upstream_request = _apply_request_rules(host, upstream_request)

        tx = STORE.add(
            method=method,
            scheme="http",
            host=host,
            port=port,
            path=rel,
            req_headers=dict(headers),
            req_body=body,
            raw_request=upstream_request,
        )

        # Intercept only in-scope requests. Out-of-scope passes straight through.
        if SCOPE.in_scope(host) and INTERCEPT.is_enabled():
            action, new_raw = INTERCEPT.hold(tx.id, upstream_request)
            if action == InterceptResult.DROP:
                STORE.update(tx.id, error="dropped by intercept",
                             finished_at=__import__("time").time())
                self._send_error(503, "Request dropped by MiniBurp intercept")
                return
            if new_raw is not None and new_raw != upstream_request:
                err = validate_request_bytes(new_raw)
                if err is not None:
                    STORE.update(tx.id, error=f"malformed edit: {err}",
                                 finished_at=__import__("time").time())
                    self._send_error(400, f"Edited request is malformed: {err}")
                    return
                upstream_request = new_raw
                STORE.update(tx.id, raw_request=new_raw)

        try:
            upstream = _open_upstream(host, port, use_tls=False)
        except OSError as e:
            self._send_error(502, f"Upstream connect failed: {e}")
            STORE.update(tx.id, error=str(e))
            return

        try:
            upstream.sendall(upstream_request)
            resp_start, resp_headers, resp_body, raw_resp = read_response(upstream)
            status = parse_status_code(resp_start)
            STORE.update(
                tx.id,
                status=status,
                resp_headers=resp_headers,
                resp_body=resp_body,
                raw_response=raw_resp,
                finished_at=__import__("time").time(),
            )
            # Forward to client, strip hop-by-hop, force close.
            client_headers = _sanitize_headers_for_forward(resp_headers)
            client_headers["Content-Length"] = str(len(resp_body))
            client_bytes = build_request_bytes(resp_start, client_headers, resp_body)
            self.client.sendall(client_bytes)
        finally:
            upstream.close()

    # ---- noise pass-through --------------------------------------------
    def _transparent_tunnel(self, host: str, port: int) -> None:
        """
        Open a raw TCP tunnel to host:port and splice bytes both ways.
        Used for known-chatty hosts so we don't mint a MITM leaf cert
        or log them to history. Chrome's flags should keep most of this
        traffic from happening at all; this is the safety net.
        """
        try:
            upstream = socket.create_connection((host, port), timeout=30)
        except OSError:
            # Best-effort tell the client we couldn't connect, then bail.
            try:
                self.client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n"
                                    b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            return

        try:
            self.client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        except OSError:
            upstream.close()
            return

        socks = [self.client, upstream]
        try:
            while True:
                ready, _, _ = select.select(socks, [], [], 60)
                if not ready:
                    break
                done = False
                for sock in ready:
                    try:
                        data = sock.recv(65536)
                    except OSError:
                        done = True
                        break
                    if not data:
                        done = True
                        break
                    peer = upstream if sock is self.client else self.client
                    try:
                        peer.sendall(data)
                    except OSError:
                        done = True
                        break
                if done:
                    break
        finally:
            try:
                upstream.close()
            except OSError:
                pass

    # ---- HTTPS CONNECT + MITM ------------------------------------------
    def _handle_connect(self, start_line: str, headers: dict[str, str]) -> None:
        # start_line: "CONNECT host:port HTTP/1.1"
        try:
            target = start_line.split(" ", 2)[1]
            host, port_s = target.split(":")
            port = int(port_s)
        except Exception:
            self._send_error(400, "Bad CONNECT line")
            return

        # Lab-only invariant: never MITM Chrome's background chatter.
        # Transparent-tunnel the bytes so Chrome stays functional, but
        # don't mint a leaf cert and don't log to history.
        if is_noise(host):
            self._transparent_tunnel(host, port)
            return

        self.client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")

        # Wrap client socket with a cert for `host`.
        cert_path, key_path = ca.get_host_cert(host)
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)

        try:
            tls_client = server_ctx.wrap_socket(self.client, server_side=True)
        except (ssl.SSLError, OSError) as e:
            print(f"[proxy] TLS handshake with client failed for {host}: {e}")
            return

        # Keep-alive loop: handle requests until the client closes.
        tls_client.settimeout(60)
        try:
            while True:
                parsed = read_request(tls_client)
                if parsed is None:
                    break
                start_line2, headers2, body2, _raw = parsed
                try:
                    self._forward_https(tls_client, host, port, start_line2, headers2, body2)
                except (HTTPParseError, ValueError, OSError, ssl.SSLError) as e:
                    # One malformed message (bad status line, dropped upstream,
                    # half-broken endpoint) ends this connection cleanly instead
                    # of escaping to handle()'s blanket except as a traceback.
                    print(f"[proxy] upstream error on {host}: {e}")
                    break
                # Honor client Connection: close.
                if headers2.get("Connection", "").lower() == "close":
                    break
        finally:
            try:
                tls_client.unwrap()
            except (OSError, ssl.SSLError):
                pass

    def _forward_https(
        self,
        tls_client: ssl.SSLSocket,
        host: str,
        port: int,
        start_line: str,
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        method, target, version = start_line.split(" ", 2)
        # target is usually relative ("/path?x") when we've already done CONNECT.
        rel = target if target.startswith("/") else _absolute_to_relative(target)[0]

        forwarded_start = f"{method} {rel} {version}"
        forwarded_headers = _sanitize_headers_for_forward(headers)
        forwarded_headers.setdefault("Host", host if port == 443 else f"{host}:{port}")
        ensure_content_length(forwarded_headers, body)
        upstream_request = build_request_bytes(forwarded_start, forwarded_headers, body)
        upstream_request = _apply_request_rules(host, upstream_request)

        tx = STORE.add(
            method=method,
            scheme="https",
            host=host,
            port=port,
            path=rel,
            req_headers=dict(headers),
            req_body=body,
            raw_request=upstream_request,
        )

        if SCOPE.in_scope(host) and INTERCEPT.is_enabled():
            action, new_raw = INTERCEPT.hold(tx.id, upstream_request)
            if action == InterceptResult.DROP:
                STORE.update(tx.id, error="dropped by intercept",
                             finished_at=__import__("time").time())
                tls_client.sendall(
                    build_request_bytes(
                        "HTTP/1.1 503 Service Unavailable",
                        {"Content-Length": "0", "Connection": "close"},
                        b"",
                    )
                )
                return
            if new_raw is not None and new_raw != upstream_request:
                err = validate_request_bytes(new_raw)
                if err is not None:
                    STORE.update(tx.id, error=f"malformed edit: {err}",
                                 finished_at=__import__("time").time())
                    body = f"Edited request is malformed: {err}".encode("utf-8")
                    tls_client.sendall(
                        build_request_bytes(
                            "HTTP/1.1 400 Bad Request",
                            {"Content-Length": str(len(body)), "Connection": "close"},
                            body,
                        )
                    )
                    return
                upstream_request = new_raw
                STORE.update(tx.id, raw_request=new_raw)

        try:
            upstream = _open_upstream(host, port, use_tls=True)
        except OSError as e:
            STORE.update(tx.id, error=str(e))
            tls_client.sendall(
                build_request_bytes(
                    "HTTP/1.1 502 Bad Gateway",
                    {"Content-Length": "0", "Connection": "close"},
                    b"",
                )
            )
            return

        try:
            upstream.sendall(upstream_request)
            resp_start, resp_headers, resp_body, raw_resp = read_response(upstream)
            status = parse_status_code(resp_start)
            STORE.update(
                tx.id,
                status=status,
                resp_headers=resp_headers,
                resp_body=resp_body,
                raw_response=raw_resp,
                finished_at=__import__("time").time(),
            )
            client_headers = _sanitize_headers_for_forward(resp_headers)
            client_headers["Content-Length"] = str(len(resp_body))
            tls_client.sendall(build_request_bytes(resp_start, client_headers, resp_body))
        finally:
            upstream.close()

    # ---- misc -----------------------------------------------------------
    def _send_error(self, code: int, msg: str) -> None:
        body = msg.encode("utf-8")
        resp = (
            f"HTTP/1.1 {code} {msg}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("iso-8859-1") + body
        try:
            self.client.sendall(resp)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Server loop
# ---------------------------------------------------------------------------


def bind(host: str = "127.0.0.1", port: int = 8080) -> socket.socket:
    """Bind and listen synchronously. Raises OSError if the port is taken."""
    ca.load_or_create_ca()  # ensure CA exists at startup
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(64)
    return srv


def bind_with_fallback(
    host: str = "127.0.0.1",
    preferred_port: int = 8080,
    max_tries: int = 10,
) -> socket.socket:
    """
    Try `preferred_port`, then `preferred_port+1..+max_tries-1`. Returns the
    first socket that bound. Raises the last OSError if none worked.
    """
    last: OSError | None = None
    for offset in range(max_tries):
        try:
            return bind(host, preferred_port + offset)
        except OSError as e:
            last = e
    assert last is not None
    raise last


def accept_loop(srv: socket.socket) -> None:
    """Blocking accept loop on an already-bound listener socket."""
    sockname = srv.getsockname()
    print(f"[proxy] listening on {sockname[0]}:{sockname[1]}")
    while True:
        client_sock, addr = srv.accept()
        t = threading.Thread(
            target=ProxyHandler(client_sock, addr).handle,
            daemon=True,
            name=f"proxy-{addr[0]}:{addr[1]}",
        )
        t.start()


def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Blocking call. Bind + accept. Used by the legacy --web mode."""
    accept_loop(bind(host, port))
