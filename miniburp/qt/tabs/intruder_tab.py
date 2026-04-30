"""
Intruder tab.

Attack-mode combo + target row + marker template + N payload lists + a
background worker that runs the attack and streams results into a table.
"""
from __future__ import annotations

import itertools
import re
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...sender import send_raw
from ...storage import STORE
from ..widgets.http_viewer import HttpEditor


MARK_RE = re.compile(r"§[^§]*§")


def _replace_markers(template: str, replacements: list[str]) -> str:
    it = iter(replacements)
    return MARK_RE.sub(lambda _m: next(it, ""), template)


def _build_attempts(
    mode: str,
    n_positions: int,
    payload_lists: list[list[str]],
    template: str,
) -> list[tuple[str, str]]:
    """Return list of (label, fully-substituted-text)."""
    if n_positions == 0:
        return []
    if mode == "sniper":
        payloads = payload_lists[0] if payload_lists else []
        out = []
        for pos in range(n_positions):
            for p in payloads:
                replacements = ["" for _ in range(n_positions)]
                replacements[pos] = p
                out.append((f"pos {pos+1}: {p}", _replace_markers(template, replacements)))
        return out
    if mode == "battering-ram":
        payloads = payload_lists[0] if payload_lists else []
        return [(p, _replace_markers(template, [p] * n_positions)) for p in payloads]
    if mode == "pitchfork":
        if len(payload_lists) < n_positions or any(not pl for pl in payload_lists[:n_positions]):
            return []
        return [
            (" | ".join(row), _replace_markers(template, list(row)))
            for row in zip(*payload_lists[:n_positions])
        ]
    if mode == "clusterbomb":
        if len(payload_lists) < n_positions or any(not pl for pl in payload_lists[:n_positions]):
            return []
        return [
            (" | ".join(row), _replace_markers(template, list(row)))
            for row in itertools.product(*payload_lists[:n_positions])
        ]
    return []


class _AttackWorker(QThread):
    result_ready = Signal(int, dict)  # index, row-dict
    all_done = Signal()

    def __init__(
        self,
        host: str,
        port: int,
        scheme: str,
        attempts: list[tuple[str, str]],
    ) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.scheme = scheme
        self.attempts = attempts
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self) -> None:
        for i, (label, text) in enumerate(self.attempts):
            if self._abort:
                break
            raw = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("iso-8859-1")
            r = send_raw(self.host, self.port, self.scheme, raw)
            self.result_ready.emit(i, {
                "payload": label,
                "status": r.get("status"),
                "length": len(r.get("body", b"")),
                "duration_ms": r.get("duration_ms", 0),
                "error": r.get("error"),
            })
        self.all_done.emit()


class IntruderSession(QWidget):
    """A single Intruder attack configuration."""

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

        # ---- top controls ----
        controls = QGridLayout()
        controls.addWidget(QLabel("Scheme:", self), 0, 0)
        self._scheme = QComboBox(self)
        self._scheme.addItems(["http", "https"])
        self._scheme.setCurrentText(scheme)
        controls.addWidget(self._scheme, 0, 1)

        controls.addWidget(QLabel("Host:", self), 0, 2)
        self._host = QLineEdit(host, self)
        controls.addWidget(self._host, 0, 3)

        controls.addWidget(QLabel("Port:", self), 0, 4)
        self._port = QSpinBox(self)
        self._port.setRange(1, 65535)
        self._port.setValue(port)
        controls.addWidget(self._port, 0, 5)

        controls.addWidget(QLabel("Mode:", self), 0, 6)
        self._mode = QComboBox(self)
        self._mode.addItem("Sniper (list 1, one marker at a time)", "sniper")
        self._mode.addItem("Battering ram (list 1, every marker)", "battering-ram")
        self._mode.addItem("Pitchfork (N lists, lockstep)", "pitchfork")
        self._mode.addItem("Cluster bomb (N lists, cartesian)", "clusterbomb")
        controls.addWidget(self._mode, 0, 7, 1, 2)

        self._start_btn = QPushButton("Start attack", self)
        self._stop_btn = QPushButton("Stop", self)
        self._stop_btn.setEnabled(False)
        controls.addWidget(self._start_btn, 1, 7)
        controls.addWidget(self._stop_btn, 1, 8)

        self._pos_label = QLabel("positions: 0", self)
        self._pos_label.setProperty("role", "muted")
        controls.addWidget(self._pos_label, 1, 0, 1, 3)

        # ---- template + payloads + results splitter ----
        self._template = HttpEditor("Request template (wrap values in §…§)", parent=self)
        self._template.set_bytes(raw, title="Request template (wrap values in §…§)")
        self._template.edit.textChanged.connect(self._rebuild_payload_panels)

        self._payloads_wrap = QWidget(self)
        self._payloads_layout = QVBoxLayout(self._payloads_wrap)
        self._payloads_layout.setContentsMargins(0, 0, 0, 0)
        self._payloads_layout.setSpacing(6)
        self._payload_edits: list[QPlainTextEdit] = []

        left = QSplitter(Qt.Vertical, self)
        left.addWidget(self._template)
        left.addWidget(self._payloads_wrap)
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 2)

        self._results = QTableWidget(0, 5, self)
        self._results.setHorizontalHeaderLabels(["#", "Payload", "Status", "Length", "Time"])
        self._results.verticalHeader().setVisible(False)
        self._results.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self._results.horizontalHeader().setStretchLastSection(True)
        self._results.setColumnWidth(0, 50)
        self._results.setColumnWidth(1, 260)
        self._results.setColumnWidth(2, 60)
        self._results.setColumnWidth(3, 80)

        split = QSplitter(Qt.Horizontal, self)
        split.addWidget(left)
        split.addWidget(self._results)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        root.addLayout(controls)
        root.addWidget(split, 1)

        # initial payload panels based on current marker count
        self._rebuild_payload_panels()

        # wire actions
        self._start_btn.clicked.connect(self._start)
        self._stop_btn.clicked.connect(self._stop)
        self._mode.currentIndexChanged.connect(self._rebuild_payload_panels)

        self._worker: Optional[_AttackWorker] = None

    # ---- payload panel reconcile ---------------------------------------
    def _rebuild_payload_panels(self) -> None:
        template_text = self._template.text_value()
        n = len(MARK_RE.findall(template_text))
        self._pos_label.setText(f"positions: {n}")

        # Sniper / battering-ram → always exactly 1 payload panel
        mode = self._mode.currentData()
        wanted = 1 if mode in ("sniper", "battering-ram") else max(1, n)

        # preserve existing payload text where we can
        existing_texts = [w.toPlainText() for w in self._payload_edits]

        # wipe layout
        while self._payloads_layout.count():
            item = self._payloads_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()
        self._payload_edits.clear()

        for i in range(wanted):
            wrap = QWidget(self)
            v = QVBoxLayout(wrap)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            label = QLabel(f"List {i+1}", self)
            label.setProperty("role", "section")
            edit = QPlainTextEdit(self)
            edit.setPlaceholderText("One payload per line")
            if i < len(existing_texts):
                edit.setPlainText(existing_texts[i])
            v.addWidget(label)
            v.addWidget(edit)
            self._payloads_layout.addWidget(wrap, 1)
            self._payload_edits.append(edit)

    # ---- attack lifecycle ----------------------------------------------
    def _start(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        template_text = self._template.text_value()
        n_positions = len(MARK_RE.findall(template_text))
        if n_positions == 0:
            self._set_results_error("mark at least one position with §…§")
            return

        mode = self._mode.currentData()
        lists = [
            [p for p in w.toPlainText().splitlines() if p.strip() != ""]
            for w in self._payload_edits
        ]
        # pad to n_positions so pitchfork/clusterbomb don't IndexError
        while len(lists) < n_positions:
            lists.append([])

        attempts = _build_attempts(mode, n_positions, lists, template_text)
        if not attempts:
            self._set_results_error(
                "supply payloads — Pitchfork/Cluster bomb need one non-empty list per position."
            )
            return

        # Pre-populate rows with blanks so results can stream in by index.
        self._results.setRowCount(0)
        self._results.setRowCount(len(attempts))
        for i, (label, _text) in enumerate(attempts):
            self._results.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self._results.setItem(i, 1, QTableWidgetItem(label))
            self._results.setItem(i, 2, QTableWidgetItem("…"))
            self._results.setItem(i, 3, QTableWidgetItem(""))
            self._results.setItem(i, 4, QTableWidgetItem(""))

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        worker = _AttackWorker(
            self._host.text().strip(),
            int(self._port.value()),
            self._scheme.currentText(),
            attempts,
        )
        worker.result_ready.connect(self._on_result)
        worker.all_done.connect(self._on_done)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.abort()

    def _on_result(self, i: int, row: dict) -> None:
        status = row.get("status")
        status_item = QTableWidgetItem(str(status) if status is not None else "-")
        color = "#9a9aa8"
        s = status or 0
        if 200 <= s < 300:
            color = "#5ecb7b"
        elif 300 <= s < 400:
            color = "#7aa2ff"
        elif 400 <= s < 500:
            color = "#e7b648"
        elif s >= 500:
            color = "#ef6767"
        status_item.setForeground(QBrush(QColor(color)))

        self._results.setItem(i, 2, status_item)
        self._results.setItem(i, 3, QTableWidgetItem(str(row.get("length", 0))))
        self._results.setItem(i, 4, QTableWidgetItem(f"{row.get('duration_ms', 0)} ms"))
        err = row.get("error")
        if err:
            self._results.item(i, 2).setText("ERR")
            self._results.item(i, 1).setToolTip(str(err))

    def _on_done(self) -> None:
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    def _set_results_error(self, msg: str) -> None:
        self._results.setRowCount(1)
        self._results.setItem(0, 0, QTableWidgetItem("-"))
        self._results.setItem(0, 1, QTableWidgetItem(msg))
        self._results.setItem(0, 2, QTableWidgetItem("-"))
        self._results.setItem(0, 3, QTableWidgetItem("-"))
        self._results.setItem(0, 4, QTableWidgetItem("-"))


class IntruderTab(QWidget):
    """Container for multiple Intruder sessions as sub-tabs."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        top = QHBoxLayout()
        add_btn = QPushButton("+ New session", self)
        top.addWidget(add_btn)
        top.addStretch(1)

        self._tabs = QTabWidget(self)
        self._tabs.setTabsClosable(True)
        self._tabs.tabCloseRequested.connect(self._close)

        root.addLayout(top)
        root.addWidget(self._tabs, 1)

        add_btn.clicked.connect(self.new_session_blank)
        self._counter = 0
        self.new_session_blank()

    def _close(self, i: int) -> None:
        if self._tabs.count() <= 1:
            return
        w = self._tabs.widget(i)
        self._tabs.removeTab(i)
        if w is not None:
            w.deleteLater()

    def new_session_blank(self) -> IntruderSession:
        return self._add(IntruderSession(parent=self))

    def new_session_from_tx(self, tx_id: int) -> Optional[IntruderSession]:
        tx = STORE.get(tx_id)
        if not tx:
            return None
        session = IntruderSession(
            scheme=tx.scheme,
            host=tx.host,
            port=tx.port,
            raw=tx.raw_request,
            parent=self,
        )
        return self._add(session, label=f"#{tx.id} {tx.host}")

    def _add(self, session: IntruderSession, label: Optional[str] = None) -> IntruderSession:
        self._counter += 1
        title = label or f"Session {self._counter}"
        i = self._tabs.addTab(session, title)
        self._tabs.setCurrentIndex(i)
        return session
