"""
Intercept coordinator.

When request interception is enabled, the proxy calls `INTERCEPT.hold(tx_id, raw)`
for each in-scope request and the calling thread blocks until the UI forwards
(optionally with modified bytes) or drops it. When response interception is
enabled, the proxy calls `INTERCEPT.hold_response(tx_id, raw)` after it reads the
upstream response, blocking the same way before the response reaches the browser
— that's what lets you edit a `Set-Cookie` (or any header/body) on the way back.
Disabled holds return immediately with ("forward", raw).

Held items publish to the same subscriber channel as the store so the UI can
live-stream their arrivals:
  requests   -> {"kind": "intercept"} / {"kind": "intercept-clear"}
  responses  -> {"kind": "intercept-response"} / {"kind": "intercept-response-clear"}
"""
from __future__ import annotations

import threading
from typing import Optional

from .storage import STORE


class InterceptResult:
    FORWARD = "forward"
    DROP = "drop"


class Intercept:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.enabled: bool = False           # request interception
        self.responses_enabled: bool = False  # response interception
        # tx_id -> [Event, original_bytes, result_tuple]
        self._held: dict[int, list] = {}
        self._held_resp: dict[int, list] = {}

    # ---- shared helpers ------------------------------------------------
    def _release_all(self, store: dict[int, list]) -> None:
        """Forward-release everything still held in `store` (lock held)."""
        for slot in list(store.values()):
            if slot[2] is None:
                slot[2] = (InterceptResult.FORWARD, slot[1])
                slot[0].set()

    def _forward(self, store: dict[int, list], tx_id: int, new_bytes: Optional[bytes]) -> bool:
        with self._lock:
            slot = store.get(tx_id)
            if not slot or slot[2] is not None:
                return False
            out = new_bytes if new_bytes is not None else slot[1]
            slot[2] = (InterceptResult.FORWARD, out)
            slot[0].set()
            return True

    def _drop(self, store: dict[int, list], tx_id: int) -> bool:
        with self._lock:
            slot = store.get(tx_id)
            if not slot or slot[2] is not None:
                return False
            slot[2] = (InterceptResult.DROP, None)
            slot[0].set()
            return True

    def _forward_all(self, store: dict[int, list]) -> int:
        with self._lock:
            n = 0
            for slot in store.values():
                if slot[2] is None:
                    slot[2] = (InterceptResult.FORWARD, slot[1])
                    slot[0].set()
                    n += 1
            return n

    def _hold(
        self,
        store: dict[int, list],
        tx_id: int,
        raw: bytes,
        enabled: bool,
        held_kind: str,
        clear_kind: str,
    ) -> tuple[str, Optional[bytes]]:
        if not enabled:
            return (InterceptResult.FORWARD, raw)
        ev = threading.Event()
        with self._lock:
            store[tx_id] = [ev, raw, None]
        STORE._publish({"kind": held_kind, "tx_id": tx_id})  # type: ignore[attr-defined]
        ev.wait()
        with self._lock:
            slot = store.pop(tx_id, None)
        if slot is None:
            return (InterceptResult.FORWARD, raw)
        action, body = slot[2]
        STORE._publish({"kind": clear_kind, "tx_id": tx_id})  # type: ignore[attr-defined]
        return action, body

    # ---- requests: UI-facing -------------------------------------------
    def set_enabled(self, value: bool) -> None:
        with self._lock:
            self.enabled = bool(value)
            if not self.enabled:
                self._release_all(self._held)

    def is_enabled(self) -> bool:
        return self.enabled

    def held_ids(self) -> list[int]:
        with self._lock:
            return sorted(t for t, s in self._held.items() if s[2] is None)

    def held_bytes(self, tx_id: int) -> Optional[bytes]:
        with self._lock:
            slot = self._held.get(tx_id)
            return slot[1] if slot else None

    def forward(self, tx_id: int, new_bytes: Optional[bytes] = None) -> bool:
        return self._forward(self._held, tx_id, new_bytes)

    def drop(self, tx_id: int) -> bool:
        return self._drop(self._held, tx_id)

    def forward_all(self) -> int:
        return self._forward_all(self._held)

    # ---- responses: UI-facing ------------------------------------------
    def set_responses_enabled(self, value: bool) -> None:
        with self._lock:
            self.responses_enabled = bool(value)
            if not self.responses_enabled:
                self._release_all(self._held_resp)

    def is_responses_enabled(self) -> bool:
        return self.responses_enabled

    def held_response_ids(self) -> list[int]:
        with self._lock:
            return sorted(t for t, s in self._held_resp.items() if s[2] is None)

    def held_response_bytes(self, tx_id: int) -> Optional[bytes]:
        with self._lock:
            slot = self._held_resp.get(tx_id)
            return slot[1] if slot else None

    def forward_response(self, tx_id: int, new_bytes: Optional[bytes] = None) -> bool:
        return self._forward(self._held_resp, tx_id, new_bytes)

    def drop_response(self, tx_id: int) -> bool:
        return self._drop(self._held_resp, tx_id)

    def forward_all_responses(self) -> int:
        return self._forward_all(self._held_resp)

    # ---- proxy-facing --------------------------------------------------
    def hold(self, tx_id: int, raw: bytes) -> tuple[str, Optional[bytes]]:
        """Block until the UI decides on a request; return (action, bytes_or_None)."""
        return self._hold(
            self._held, tx_id, raw, self.enabled, "intercept", "intercept-clear",
        )

    def hold_response(self, tx_id: int, raw: bytes) -> tuple[str, Optional[bytes]]:
        """Block until the UI decides on a response; return (action, bytes_or_None)."""
        return self._hold(
            self._held_resp, tx_id, raw, self.responses_enabled,
            "intercept-response", "intercept-response-clear",
        )


INTERCEPT = Intercept()
