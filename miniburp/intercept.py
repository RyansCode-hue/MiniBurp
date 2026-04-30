"""
Intercept coordinator.

When intercept is enabled, the proxy calls `INTERCEPT.hold(tx_id, raw)`
for each in-scope request. The calling thread blocks until the web UI
tells the request to be forwarded (optionally with modified bytes) or
dropped. When intercept is disabled, `hold` returns immediately with
("forward", raw).

Held transactions publish to the same subscriber channel as the regular
store so the UI can live-stream their arrivals too.
"""
from __future__ import annotations

import queue
import threading
from typing import Optional

from .storage import STORE


class InterceptResult:
    FORWARD = "forward"
    DROP = "drop"


class Intercept:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.enabled: bool = False
        # tx_id -> [Event, original_bytes, result_tuple]
        self._held: dict[int, list] = {}

    # ---- UI-facing API -------------------------------------------------
    def set_enabled(self, value: bool) -> None:
        with self._lock:
            self.enabled = bool(value)
            if not self.enabled:
                # release everything still held as "forward unchanged"
                for slot in list(self._held.values()):
                    if slot[2] is None:
                        slot[2] = (InterceptResult.FORWARD, slot[1])
                        slot[0].set()

    def is_enabled(self) -> bool:
        return self.enabled

    def held_ids(self) -> list[int]:
        with self._lock:
            return sorted(tid for tid, slot in self._held.items() if slot[2] is None)

    def held_bytes(self, tx_id: int) -> Optional[bytes]:
        with self._lock:
            slot = self._held.get(tx_id)
            return slot[1] if slot else None

    def forward(self, tx_id: int, new_bytes: Optional[bytes] = None) -> bool:
        with self._lock:
            slot = self._held.get(tx_id)
            if not slot or slot[2] is not None:
                return False
            out = new_bytes if new_bytes is not None else slot[1]
            slot[2] = (InterceptResult.FORWARD, out)
            slot[0].set()
            return True

    def drop(self, tx_id: int) -> bool:
        with self._lock:
            slot = self._held.get(tx_id)
            if not slot or slot[2] is not None:
                return False
            slot[2] = (InterceptResult.DROP, None)
            slot[0].set()
            return True

    def forward_all(self) -> int:
        with self._lock:
            n = 0
            for slot in self._held.values():
                if slot[2] is None:
                    slot[2] = (InterceptResult.FORWARD, slot[1])
                    slot[0].set()
                    n += 1
            return n

    # ---- proxy-facing API ----------------------------------------------
    def hold(self, tx_id: int, raw: bytes) -> tuple[str, Optional[bytes]]:
        """Block until the UI decides; return (action, bytes_or_None)."""
        if not self.enabled:
            return (InterceptResult.FORWARD, raw)

        ev = threading.Event()
        with self._lock:
            self._held[tx_id] = [ev, raw, None]
        # Tell subscribers a request is held so the UI can refresh.
        STORE._publish({"kind": "intercept", "tx_id": tx_id})  # type: ignore[attr-defined]

        ev.wait()

        with self._lock:
            slot = self._held.pop(tx_id, None)
        if slot is None:
            return (InterceptResult.FORWARD, raw)
        action, body = slot[2]
        STORE._publish({"kind": "intercept-clear", "tx_id": tx_id})  # type: ignore[attr-defined]
        return action, body


INTERCEPT = Intercept()
