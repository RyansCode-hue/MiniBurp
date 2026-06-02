"""
Thread-safe bridge from the headless STORE / INTERCEPT pub-sub into Qt signals.

The proxy runs on background threads and calls STORE.add / STORE.update, which
push events onto a queue.Queue shared with every subscriber. Qt widgets MUST be
touched only from the Qt main thread, so a plain daemon thread drains the queue
and re-emits events via a Qt signal. Because the emit happens on the pump
thread and the receiver (``_dispatch``) lives on the Qt main thread, Qt's
AutoConnection downgrades to a queued connection and the slot runs on the main
thread — which is exactly what we need.

Why not QThread?
----------------
On shutdown, PySide's atexit handler destroys the QApplication and walks the
Qt parent/child tree. If any ``QThread`` child is still running at that point,
Qt aborts the process with ``QThread: Destroyed while thread is still running``
(SIGABRT). A native Python daemon thread is invisible to Qt, so it can't be
torn down in the wrong order, and the daemon flag guarantees it won't keep the
process alive on exit either.
"""
from __future__ import annotations

import queue
import threading
from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal

from ..storage import STORE


class StoreBridge(QObject):
    """
    Public Qt-native view of STORE events.

    Connect to these signals from widgets:
        added(dict summary)         - new transaction captured
        updated(dict summary)       - transaction updated (status, body, etc.)
        cleared()                   - history was wiped
        intercept_held(int tx_id)   - a request was held by INTERCEPT
        intercept_released(int)     - a held request was forwarded / dropped
        intercept_response_held(int)     - a response was held by INTERCEPT
        intercept_response_released(int) - a held response was forwarded / dropped
    """

    added = Signal(dict)
    updated = Signal(dict)
    cleared = Signal()
    intercept_held = Signal(int)
    intercept_released = Signal(int)
    intercept_response_held = Signal(int)
    intercept_response_released = Signal(int)

    # Internal signal: emitted from the pump thread, routed onto the Qt main
    # thread via a queued connection and handed to ``_dispatch``.
    _raw_event = Signal(dict)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._stop = threading.Event()
        self._shutdown_done = False
        self._q: queue.Queue = STORE.subscribe()

        # QueuedConnection is explicit here (AutoConnection would also work,
        # since the pump thread is not the Qt main thread).
        self._raw_event.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)

        self._pump = threading.Thread(
            target=self._pump_loop, name="StoreBridge-pump", daemon=True
        )
        self._pump.start()

    # ---- public API ----------------------------------------------------
    def shutdown(self) -> None:
        """Stop the pump thread and unsubscribe from STORE. Idempotent."""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._stop.set()
        # Best-effort join: pump wakes up from the queue.get timeout shortly.
        self._pump.join(timeout=2.0)
        try:
            STORE.unsubscribe(self._q)
        except Exception:
            pass

    # ---- internals -----------------------------------------------------
    def _pump_loop(self) -> None:
        q = self._q
        while not self._stop.is_set():
            try:
                ev = q.get(timeout=0.25)
            except queue.Empty:
                continue
            # Cross-thread emit; delivered on the Qt main thread via queued conn.
            self._raw_event.emit(ev)

    def _dispatch(self, ev: dict) -> None:
        kind = ev.get("kind")
        if kind == "add":
            self.added.emit(ev.get("tx") or {})
        elif kind == "update":
            self.updated.emit(ev.get("tx") or {})
        elif kind == "clear":
            self.cleared.emit()
        elif kind == "intercept":
            tid = ev.get("tx_id")
            if isinstance(tid, int):
                self.intercept_held.emit(tid)
        elif kind == "intercept-clear":
            tid = ev.get("tx_id")
            if isinstance(tid, int):
                self.intercept_released.emit(tid)
        elif kind == "intercept-response":
            tid = ev.get("tx_id")
            if isinstance(tid, int):
                self.intercept_response_held.emit(tid)
        elif kind == "intercept-response-clear":
            tid = ev.get("tx_id")
            if isinstance(tid, int):
                self.intercept_response_released.emit(tid)
