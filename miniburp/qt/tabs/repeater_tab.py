"""
Repeater tab.

Each session is its own sub-tab inside this widget. A session has a target
(scheme/host/port), an editable request, and a response viewer.
"""
from __future__ import annotations

import difflib
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ... import bodydecode
from ...sender import recompute_content_length, send_raw, send_with_redirects
from ...storage import STORE
from ..widgets.combobox import MiniBurpComboBox
from ..widgets.http_viewer import HttpEditor, HttpViewer


class _SendWorker(QThread):
    """Runs send_raw on a background thread so the UI stays responsive."""

    finished_with = Signal(dict)

    def __init__(
        self,
        host: str,
        port: int,
        scheme: str,
        raw: bytes,
        follow_redirects: bool = False,
        max_redirects: int = 10,
    ) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.scheme = scheme
        self.raw = raw
        self.follow_redirects = follow_redirects
        self.max_redirects = max_redirects

    def run(self) -> None:
        if self.follow_redirects:
            result = send_with_redirects(
                self.host, self.port, self.scheme, self.raw,
                max_redirects=self.max_redirects,
            )
        else:
            result = send_raw(self.host, self.port, self.scheme, self.raw)
        self.finished_with.emit(result)


class RepeaterSession(QWidget):
    """A single Repeater session."""

    def __init__(
        self,
        scheme: str = "https",
        host: str = "",
        port: int = 443,
        raw: bytes = b"",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ---- target row ----
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Scheme:", self))
        self._scheme = MiniBurpComboBox(self)
        self._scheme.addItems(["http", "https"])
        self._scheme.setCurrentText(scheme)

        target_row.addWidget(self._scheme)
        target_row.addWidget(QLabel("Host:", self))
        self._host = QLineEdit(host, self)

        target_row.addWidget(self._host, 1)
        target_row.addWidget(QLabel("Port:", self))
        self._port = QSpinBox(self)
        self._port.setRange(1, 65535)
        self._port.setValue(port)
        target_row.addWidget(self._port)

        self._follow_check = QCheckBox("Follow redirects", self)
        self._follow_check.setToolTip(
            "Re-send the request for any 3xx response and show the final hop.\n"
            "301/302/303 rewrite to GET; 307/308 preserve the method and body."
        )
        target_row.addWidget(self._follow_check)

        self._cl_check = QCheckBox("Update CL", self)
        self._cl_check.setToolTip(
            "Recompute Content-Length to match the body before sending.\n"
            "Off by default — Repeater ships exactly what you typed (Burp-style);\n"
            "turn on after editing a body so it isn't truncated server-side."
        )
        target_row.addWidget(self._cl_check)

        target_row.addWidget(QLabel("Max:", self))
        self._max_redirects = QSpinBox(self)
        self._max_redirects.setRange(1, 20)
        self._max_redirects.setValue(10)
        self._max_redirects.setEnabled(False)
        self._follow_check.toggled.connect(self._max_redirects.setEnabled)
        target_row.addWidget(self._max_redirects)

        self._send_btn = QPushButton("Send", self)
        self._send_btn.setProperty("role", "primary")
        self._send_btn.setMinimumWidth(90)
        self._send_btn.setToolTip("Send the request  (⌘↵ / Ctrl+Enter)")
        target_row.addWidget(self._send_btn)

        self._status_label = QLabel("", self)
        self._status_label.setProperty("role", "muted")
        target_row.addWidget(self._status_label)

        target_row.addStretch(1)

        # ---- response history nav (◀ n/m ▶ + Diff) ----
        # Each send is kept so the user can step back and compare payloads
        # without re-sending. Diff shows the body delta vs the previous entry.
        self._prev_btn = QPushButton("◀", self)
        self._prev_btn.setFixedWidth(34)
        self._prev_btn.setToolTip("Previous response")
        self._hist_label = QLabel("0/0", self)
        self._hist_label.setProperty("role", "muted")
        self._next_btn = QPushButton("▶", self)
        self._next_btn.setFixedWidth(34)
        self._next_btn.setToolTip("Next response")
        self._diff_check = QCheckBox("Diff", self)
        self._diff_check.setToolTip(
            "Show a line diff of this response's body against the previous send."
        )
        target_row.addWidget(self._prev_btn)
        target_row.addWidget(self._hist_label)
        target_row.addWidget(self._next_btn)
        target_row.addWidget(self._diff_check)

        # ---- splitter: request | response ----
        self._req_editor = HttpEditor("Request (editable)", parent=self)
        self._req_editor.set_bytes(raw, title="Request (editable)")
        self._res_viewer = HttpViewer("Response", editable=False, parent=self)

        split = QSplitter(Qt.Horizontal, self)
        split.addWidget(self._req_editor)
        split.addWidget(self._res_viewer)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        root.addLayout(target_row)
        root.addWidget(split, 1)

        self._worker: Optional[_SendWorker] = None
        self._pending_raw: bytes = b""
        # Per-session send history: each entry is {"request": bytes, "result": dict}.
        self._history: list[dict] = []
        self._hist_index = -1
        self._send_btn.clicked.connect(self._send)
        self._prev_btn.clicked.connect(lambda: self._step_history(-1))
        self._next_btn.clicked.connect(lambda: self._step_history(1))
        self._diff_check.toggled.connect(self._render_current)
        self._update_nav()

        # Cmd/Ctrl+Return (and numpad Enter) sends, even while the cursor is in
        # the request editor — the tight edit-then-send loop shouldn't require
        # reaching for the mouse. Scoped to this session widget.
        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(self._send)

    def _send(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._send_btn.setEnabled(False)
        self._status_label.setText("Sending…")
        raw = self._req_editor.bytes_value()
        if self._cl_check.isChecked():
            raw = recompute_content_length(raw)
        self._pending_raw = raw
        worker = _SendWorker(
            self._host.text().strip(),
            int(self._port.value()),
            self._scheme.currentText(),
            raw,
            follow_redirects=self._follow_check.isChecked(),
            max_redirects=int(self._max_redirects.value()),
        )
        worker.finished_with.connect(self._on_response)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_response(self, result: dict) -> None:
        # Drop our reference to the worker before its scheduled deleteLater()
        # runs. Without this, the next click into _send() calls isRunning() on
        # a deleted C++ QThread and the click silently no-ops.
        self._worker = None
        self._send_btn.setEnabled(True)
        self._history.append({"request": self._pending_raw, "result": result})
        self._hist_index = len(self._history) - 1
        self._update_nav()
        self._render_current()

    # ---- response rendering + history nav ------------------------------
    def _format_result(self, result: dict) -> tuple[str, bytes, str]:
        """Return (status_text, display_bytes, title) for a result dict."""
        if result.get("error"):
            return (
                f"error: {result['error']}",
                f"ERROR: {result['error']}".encode(),
                "Response (error)",
            )
        status = result.get("status")
        dur = result.get("duration_ms", 0)
        body = result.get("body", b"")

        # If we followed redirects, append the hop chain so the user can see
        # what happened without scrolling through history.
        chain = result.get("chain") or []
        suffix = ""
        if len(chain) > 1:
            arrow = " → ".join(str(hop.get("status") or "?") for hop in chain)
            hops = result.get("hops") or (len(chain) - 1)
            total = result.get("total_ms", dur)
            suffix = f" · {hops} hop{'s' if hops != 1 else ''}: {arrow} · {total} ms total"
            if result.get("truncated"):
                suffix += " · max-redirects reached"
        status_text = f"status {status} · {len(body)} bytes · {dur} ms{suffix}"

        decoded, note = bodydecode.decode_body(body, result.get("headers", {}))
        raw = result.get("raw", b"")
        if raw:
            split = raw.find(b"\r\n\r\n")
            display = raw[: split + 4] + decoded if note == "" and split != -1 else raw
        else:
            display = b""
        title = f"Response  [status {status}]"
        if note:
            title += f"  [body decode: {note}]"
        return status_text, display, title

    def _render_current(self) -> None:
        if not self._history:
            return
        result = self._history[self._hist_index]["result"]
        status_text, display, title = self._format_result(result)

        if (
            self._diff_check.isChecked()
            and self._hist_index > 0
            and not result.get("error")
        ):
            prev = self._history[self._hist_index - 1]["result"]
            cur_dec, _ = bodydecode.decode_body(result.get("body", b""), result.get("headers", {}))
            prev_dec, _ = bodydecode.decode_body(prev.get("body", b""), prev.get("headers", {}))
            diff = difflib.unified_diff(
                prev_dec.decode("iso-8859-1", errors="replace").splitlines(),
                cur_dec.decode("iso-8859-1", errors="replace").splitlines(),
                fromfile=f"send #{self._hist_index}",
                tofile=f"send #{self._hist_index + 1}",
                lineterm="",
            )
            diff_text = "\n".join(diff) or "(no differences in the decoded body)"
            self._status_label.setText(status_text + " · diff vs previous")
            self._res_viewer.set_bytes(
                diff_text.encode("utf-8", errors="replace"),
                title="Diff vs previous response",
            )
            return

        self._status_label.setText(status_text)
        self._res_viewer.set_bytes(display, title=title)

    def _step_history(self, delta: int) -> None:
        new = self._hist_index + delta
        if not self._history or new < 0 or new >= len(self._history):
            return
        self._hist_index = new
        # Restore the request that produced this response so the two panes
        # stay in sync as the user steps back through payload variants.
        req = self._history[new].get("request") or b""
        if req:
            self._req_editor.set_bytes(req, title="Request (editable)")
        self._update_nav()
        self._render_current()

    def _update_nav(self) -> None:
        n = len(self._history)
        shown = self._hist_index + 1 if n else 0
        self._hist_label.setText(f"{shown}/{n}")
        self._prev_btn.setEnabled(self._hist_index > 0)
        self._next_btn.setEnabled(0 <= self._hist_index < n - 1)
        self._diff_check.setEnabled(n > 1)


class RepeaterTab(QWidget):
    """Container for multiple Repeater sessions as sub-tabs."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        self._tabs = QTabWidget(self)
        self._tabs.setTabsClosable(True)
        # Match the main window's tab styling: flat strip with left-aligned
        # session tabs. Without these two lines Qt expands each tab to share
        # the full bar width, so a single open session pads out across the
        # whole pane.
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().setExpanding(False)
        # macOS's native QTabBar painter ignores QSS background; force it to
        # honor chrome_bg so the strip right of the last session tab isn't
        # a dark patch in light mode.
        self._tabs.tabBar().setAttribute(Qt.WA_StyledBackground, True)
        self._tabs.tabCloseRequested.connect(self._close_session)

        add_btn = QPushButton("+ New session", self)
        add_btn.clicked.connect(lambda: self.new_session_blank())

        top = QHBoxLayout()
        top.addWidget(add_btn)
        top.addStretch(1)

        root.addLayout(top)
        root.addWidget(self._tabs, 1)

        self._counter = 0
        self.new_session_blank()

    def _close_session(self, index: int) -> None:
        if self._tabs.count() <= 1:
            return
        w = self._tabs.widget(index)
        self._tabs.removeTab(index)
        if w is not None:
            w.deleteLater()

    def new_session_blank(self) -> RepeaterSession:
        return self._add_session(RepeaterSession(parent=self))

    def new_session_from_tx(self, tx_id: int) -> Optional[RepeaterSession]:
        tx = STORE.get(tx_id)
        if not tx:
            return None
        return self.new_session_from_bytes(
            tx.scheme, tx.host, tx.port, tx.raw_request, label=f"#{tx.id} {tx.host}"
        )

    def new_session_from_bytes(
        self,
        scheme: str,
        host: str,
        port: int,
        raw: bytes,
        label: Optional[str] = None,
    ) -> RepeaterSession:
        """Seed a session from explicit bytes — used by 'Send to Repeater' from
        the Intercept tab so the user's *edited* held bytes carry over rather
        than the originally-captured request."""
        session = RepeaterSession(
            scheme=scheme, host=host, port=port, raw=bytes(raw), parent=self
        )
        return self._add_session(session, label=label)

    def _add_session(self, session: RepeaterSession, label: Optional[str] = None) -> RepeaterSession:
        self._counter += 1
        title = label or f"Session {self._counter}"
        index = self._tabs.addTab(session, title)
        # Replace the native close button with a plain × that matches the
        # tab text color (fg_muted) and shifts to fg on hover — styled via
        # the `[role="tabClose"]` QSS rule in main_window. Captures `session`
        # by closure so reorder/insert doesn't desync the index.
        close_btn = QPushButton("×", self)
        close_btn.setProperty("role", "tabClose")
        close_btn.setFlat(True)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFocusPolicy(Qt.NoFocus)
        close_btn.setFixedSize(18, 18)
        close_btn.clicked.connect(lambda _checked=False, w=session: self._close_session_widget(w))
        self._tabs.tabBar().setTabButton(index, QTabBar.RightSide, close_btn)
        self._tabs.setCurrentIndex(index)
        return session

    def _close_session_widget(self, w: QWidget) -> None:
        index = self._tabs.indexOf(w)
        if index >= 0:
            self._close_session(index)
