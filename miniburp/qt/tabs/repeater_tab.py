"""
Repeater tab.

Each session is its own sub-tab inside this widget. A session has a target
(scheme/host/port), an editable request, and a response viewer.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ... import bodydecode
from ...sender import send_raw
from ...storage import STORE
from ..widgets.http_viewer import HttpEditor, HttpViewer


class _SendWorker(QThread):
    """Runs send_raw on a background thread so the UI stays responsive."""

    finished_with = Signal(dict)

    def __init__(self, host: str, port: int, scheme: str, raw: bytes) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.scheme = scheme
        self.raw = raw

    def run(self) -> None:
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
        self._scheme = QComboBox(self)
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

        self._send_btn = QPushButton("Send", self)
        self._send_btn.setMinimumWidth(90)
        target_row.addWidget(self._send_btn)

        self._status_label = QLabel("", self)
        self._status_label.setProperty("role", "muted")
        target_row.addWidget(self._status_label)

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
        self._send_btn.clicked.connect(self._send)

    def _send(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._send_btn.setEnabled(False)
        self._status_label.setText("Sending…")
        worker = _SendWorker(
            self._host.text().strip(),
            int(self._port.value()),
            self._scheme.currentText(),
            self._req_editor.bytes_value(),
        )
        worker.finished_with.connect(self._on_response)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_response(self, result: dict) -> None:
        self._send_btn.setEnabled(True)
        if result.get("error"):
            self._status_label.setText(f"error: {result['error']}")
            self._res_viewer.set_bytes(
                f"ERROR: {result['error']}".encode(),
                title="Response (error)",
            )
            return
        status = result.get("status")
        dur = result.get("duration_ms", 0)
        body = result.get("body", b"")
        self._status_label.setText(f"status {status} · {len(body)} bytes · {dur} ms")

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
        self._res_viewer.set_bytes(display, title=title)


class RepeaterTab(QWidget):
    """Container for multiple Repeater sessions as sub-tabs."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        self._tabs = QTabWidget(self)
        self._tabs.setTabsClosable(True)
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
        session = RepeaterSession(
            scheme=tx.scheme,
            host=tx.host,
            port=tx.port,
            raw=tx.raw_request,
            parent=self,
        )
        return self._add_session(session, label=f"#{tx.id} {tx.host}")

    def _add_session(self, session: RepeaterSession, label: Optional[str] = None) -> RepeaterSession:
        self._counter += 1
        title = label or f"Session {self._counter}"
        index = self._tabs.addTab(session, title)
        self._tabs.setCurrentIndex(index)
        return session
