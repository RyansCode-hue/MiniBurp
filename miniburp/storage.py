"""
Thread-safe in-memory storage for captured HTTP transactions.

Each transaction has an auto-incrementing ID, the raw request and
response bytes, and some parsed metadata for the history table.

The store also maintains a pub/sub channel so the web UI can stream
live updates over Server-Sent Events. Subscribers receive dicts like
{"kind": "add", "tx": {...}} or {"kind": "update", "tx": {...}}.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Transaction:
    id: int
    method: str
    scheme: str            # "http" or "https"
    host: str
    port: int
    path: str
    status: Optional[int] = None
    req_headers: dict = field(default_factory=dict)
    req_body: bytes = b""
    resp_headers: dict = field(default_factory=dict)
    resp_body: bytes = b""
    raw_request: bytes = b""   # reconstructed request line + headers + body
    raw_response: bytes = b""  # full response as received
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
    # Lazily-populated cache of the response body after gzip/deflate/brotli
    # decoding. Used by the history search so a 'csrf-token' query matches
    # gzipped responses too. Invalidated in Store.update when resp_body or
    # resp_headers change.
    _decoded_resp_body: Optional[bytes] = field(default=None, init=False, repr=False, compare=False)

    def decoded_resp_body(self) -> bytes:
        """Return the response body decoded if Content-Encoding says so.

        Falls back to the raw bytes on decode failure or unknown encoding.
        Memoized; cleared by Store.update when the body or headers change.
        """
        if self._decoded_resp_body is None:
            # Imported lazily to keep storage.py free of decoder deps.
            from .bodydecode import decode_body
            decoded, _note = decode_body(self.resp_body, self.resp_headers)
            self._decoded_resp_body = decoded
        return self._decoded_resp_body

    @property
    def url(self) -> str:
        default_port = 443 if self.scheme == "https" else 80
        host_part = self.host if self.port == default_port else f"{self.host}:{self.port}"
        return f"{self.scheme}://{host_part}{self.path}"

    @property
    def length(self) -> int:
        return len(self.resp_body)

    @property
    def duration_ms(self) -> Optional[int]:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at) * 1000)

    def summary(self) -> dict:
        """Lightweight dict for the history table / SSE wire format."""
        return {
            "id": self.id,
            "method": self.method,
            "url": self.url,
            "host": self.host,
            "path": self.path,
            "status": self.status,
            "length": self.length,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[Transaction] = []
        self._next_id = 1
        self._subs: list[queue.Queue] = []

    # ---- subscriptions -------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def _publish(self, event: dict) -> None:
        # called with the lock held
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # slow subscriber; drop the event rather than block

    # ---- CRUD ----------------------------------------------------------
    def add(self, **kwargs) -> Transaction:
        with self._lock:
            tx = Transaction(id=self._next_id, **kwargs)
            self._next_id += 1
            self._items.append(tx)
            self._publish({"kind": "add", "tx": tx.summary()})
            return tx

    def update(self, tx_id: int, **kwargs) -> None:
        with self._lock:
            for tx in self._items:
                if tx.id == tx_id:
                    for k, v in kwargs.items():
                        setattr(tx, k, v)
                    # If anything that feeds the decoded-body cache changed,
                    # drop it so the next decoded_resp_body() call re-runs.
                    if "resp_body" in kwargs or "resp_headers" in kwargs:
                        tx._decoded_resp_body = None
                    self._publish({"kind": "update", "tx": tx.summary()})
                    return

    def get(self, tx_id: int) -> Optional[Transaction]:
        with self._lock:
            for tx in self._items:
                if tx.id == tx_id:
                    return tx
            return None

    def all(self) -> list[Transaction]:
        with self._lock:
            return list(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._publish({"kind": "clear"})


# Single global store used by both proxy and web UI.
STORE = Store()
