"""
Intercept tab.

Shows requests held by INTERCEPT.hold() and, when response interception is on,
responses held by INTERCEPT.hold_response() before they reach the browser. Each
held item gets an editable HttpEditor and Forward / Drop buttons. The toolbar
toggles request interception, response interception, and Forward-all.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...intercept import INTERCEPT
from ...sender import recompute_content_length
from ...storage import STORE
from ..signals import StoreBridge
from ..widgets.hint_strip import HintStrip
from ..widgets.http_viewer import HttpEditor

REQUEST = "request"
RESPONSE = "response"


class _HoldPane(QWidget):
    """One editable viewer + action buttons for a single held request/response."""

    # (tx_id, edited raw bytes) — bubbled up to MainWindow which knows the tabs.
    to_repeater = Signal(int, object)
    to_intruder = Signal(int, object)

    def __init__(
        self,
        tx_id: int,
        kind: str = REQUEST,
        auto_content_length: Optional[Callable[[], bool]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._tx_id = tx_id
        self._kind = kind
        self._auto_cl = auto_content_length or (lambda: False)
        is_resp = kind == RESPONSE
        noun = "response" if is_resp else "request"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self._title = QLabel(f"#{tx_id}", self)
        self._title.setProperty("role", "title")

        self.editor = HttpEditor(f"Held {noun} #{tx_id}", parent=self)
        raw = INTERCEPT.held_response_bytes(tx_id) if is_resp else INTERCEPT.held_bytes(tx_id)
        if raw is not None:
            self.editor.set_bytes(raw, title=f"Held {noun} #{tx_id}")

        tx = STORE.get(tx_id)
        if tx is not None:
            label = f"#{tx_id}  RESPONSE  {tx.url}" if is_resp else f"#{tx_id}  {tx.method}  {tx.url}"
            self._title.setText(label)

        btn_row = QHBoxLayout()
        self._forward_btn = QPushButton("Forward", self)
        self._forward_btn.setProperty("role", "primary")
        self._forward_btn.setToolTip(f"Forward this {noun}  (⌘↵ / Ctrl+Enter)")
        self._drop_btn = QPushButton("Drop", self)
        self._drop_btn.setProperty("role", "danger")
        self._drop_btn.setToolTip(f"Drop this {noun}  (⌘⌫ / Ctrl+Backspace)")
        btn_row.addWidget(self._forward_btn)
        btn_row.addWidget(self._drop_btn)
        btn_row.addStretch(1)

        # Send-to-* only makes sense for requests.
        if not is_resp:
            self._to_rep_btn = QPushButton("Send to Repeater", self)
            self._to_rep_btn.setToolTip(
                "Open this request (with your edits) in a new Repeater session.\n"
                "The request stays held — Forward or Drop it separately."
            )
            self._to_intr_btn = QPushButton("Send to Intruder", self)
            btn_row.addWidget(self._to_rep_btn)
            btn_row.addWidget(self._to_intr_btn)
            self._to_rep_btn.clicked.connect(
                lambda: self.to_repeater.emit(self._tx_id, self.editor.bytes_value())
            )
            self._to_intr_btn.clicked.connect(
                lambda: self.to_intruder.emit(self._tx_id, self.editor.bytes_value())
            )

        root.addWidget(self._title)
        root.addWidget(self.editor, 1)
        root.addLayout(btn_row)

        self._forward_btn.clicked.connect(self._forward)
        self._drop_btn.clicked.connect(self._drop)

        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(self._forward)
        sc_drop = QShortcut(QKeySequence("Ctrl+Backspace"), self)
        sc_drop.setContext(Qt.WidgetWithChildrenShortcut)
        sc_drop.activated.connect(self._drop)

    def _forward(self) -> None:
        raw = self.editor.bytes_value()
        if self._kind == RESPONSE:
            # The proxy re-sanitizes the response and recomputes Content-Length
            # on delivery, so we ship the edited bytes as-is.
            INTERCEPT.forward_response(self._tx_id, raw)
            return
        # Requests: a body edit leaves the original Content-Length stale, which
        # truncates or hangs the request server-side. On by default.
        if self._auto_cl():
            raw = recompute_content_length(raw)
        INTERCEPT.forward(self._tx_id, raw)

    def _drop(self) -> None:
        if self._kind == RESPONSE:
            INTERCEPT.drop_response(self._tx_id)
        else:
            INTERCEPT.drop(self._tx_id)


class InterceptTab(QWidget):
    # Re-emitted from the focused hold pane (tx_id, edited raw bytes).
    send_to_repeater = Signal(int, object)
    send_to_intruder = Signal(int, object)

    def __init__(self, bridge: StoreBridge, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        root.addWidget(HintStrip(
            "Only <b>in-scope</b> traffic is held. Turn Intercept <b>ON</b> for "
            "requests; tick <b>Intercept responses</b> to also edit responses on "
            "the way back (e.g. flip a <code>Set-Cookie</code>). Forward (⌘↵) / "
            "Drop (⌘⌫)."
        ))

        # --- toolbar ---
        toolbar = QHBoxLayout()
        self._toggle_btn = QPushButton("Turn ON", self)
        self._toggle_btn.setCheckable(True)
        self._resp_check = QCheckBox("Intercept responses", self)
        self._resp_check.setToolTip(
            "Also hold responses for editing before they reach the browser.\n"
            "Lets you edit a Set-Cookie / header / body on the way back."
        )
        self._forward_all_btn = QPushButton("Forward all", self)
        self._forward_all_btn.setProperty("role", "primary")
        self._auto_cl_check = QCheckBox("Auto Content-Length", self)
        self._auto_cl_check.setChecked(True)
        self._auto_cl_check.setToolTip(
            "Recompute Content-Length to match an edited request body before\n"
            "forwarding. On by default so a lengthened body isn't truncated."
        )
        self._status_label = QLabel("Intercept: OFF", self)
        self._status_label.setProperty("role", "muted")

        toolbar.addWidget(self._toggle_btn)
        toolbar.addWidget(self._resp_check)
        toolbar.addWidget(self._forward_all_btn)
        toolbar.addWidget(self._auto_cl_check)
        toolbar.addWidget(self._status_label)
        toolbar.addStretch(1)

        # --- hold list + stack ---
        self._list = QListWidget(self)
        self._list.setMaximumWidth(240)
        self._stack = QStackedWidget(self)
        self._empty = QLabel(
            "Nothing held.\n\n"
            "Turn Intercept ON (and optionally 'Intercept responses') and trigger\n"
            "traffic in the tracked browser. In-scope items appear here to edit.",
            self,
        )
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setProperty("role", "muted")
        self._stack.addWidget(self._empty)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(self._list)
        splitter.addWidget(self._stack)
        splitter.setStretchFactor(1, 1)

        root.addLayout(toolbar)
        root.addWidget(splitter, 1)

        # --- state: panes keyed by (tx_id, kind) ---
        self._panes: dict[tuple, _HoldPane] = {}

        # --- wire ---
        self._toggle_btn.clicked.connect(self._toggle)
        self._resp_check.toggled.connect(self._toggle_responses)
        self._forward_all_btn.clicked.connect(self._forward_all)
        self._list.currentRowChanged.connect(self._select_index)
        bridge.intercept_held.connect(lambda t: self._on_held(t, REQUEST))
        bridge.intercept_released.connect(lambda t: self._on_released(t, REQUEST))
        bridge.intercept_response_held.connect(lambda t: self._on_held(t, RESPONSE))
        bridge.intercept_response_released.connect(lambda t: self._on_released(t, RESPONSE))

        self._refresh_status()
        # Populate anything already held (reconnect case).
        for tid in INTERCEPT.held_ids():
            self._on_held(tid, REQUEST)
        for tid in INTERCEPT.held_response_ids():
            self._on_held(tid, RESPONSE)

    # ---- toolbar -------------------------------------------------------
    def _toggle(self) -> None:
        INTERCEPT.set_enabled(not INTERCEPT.is_enabled())
        self._refresh_status()

    def _toggle_responses(self, on: bool) -> None:
        INTERCEPT.set_responses_enabled(on)
        self._refresh_status()

    def _forward_all(self) -> None:
        INTERCEPT.forward_all()
        INTERCEPT.forward_all_responses()

    def _refresh_status(self) -> None:
        on = INTERCEPT.is_enabled()
        self._toggle_btn.setText("Turn OFF" if on else "Turn ON")
        if self._toggle_btn.isChecked() != on:
            self._toggle_btn.blockSignals(True)
            self._toggle_btn.setChecked(on)
            self._toggle_btn.blockSignals(False)
        resp_on = INTERCEPT.is_responses_enabled()
        if self._resp_check.isChecked() != resp_on:
            self._resp_check.blockSignals(True)
            self._resp_check.setChecked(resp_on)
            self._resp_check.blockSignals(False)
        self._status_label.setText(
            f"Requests: {'ON' if on else 'OFF'} · "
            f"Responses: {'ON' if resp_on else 'OFF'} · held: {len(self._panes)}"
        )

    # ---- held lifecycle ------------------------------------------------
    def _on_held(self, tx_id: int, kind: str) -> None:
        key = (tx_id, kind)
        if key in self._panes:
            return
        pane = _HoldPane(
            tx_id, kind=kind,
            auto_content_length=self._auto_cl_check.isChecked, parent=self,
        )
        pane.to_repeater.connect(self.send_to_repeater)
        pane.to_intruder.connect(self.send_to_intruder)
        self._panes[key] = pane
        self._stack.addWidget(pane)
        label = f"#{tx_id} resp" if kind == RESPONSE else f"#{tx_id}"
        item = QListWidgetItem(label, self._list)
        item.setData(Qt.UserRole, key)
        self._list.addItem(item)
        if self._list.count() == 1:
            self._list.setCurrentRow(0)
        self._refresh_status()

    def _on_released(self, tx_id: int, kind: str) -> None:
        key = (tx_id, kind)
        pane = self._panes.pop(key, None)
        if pane is not None:
            self._stack.removeWidget(pane)
            pane.deleteLater()
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it and it.data(Qt.UserRole) == key:
                self._list.takeItem(i)
                break
        if self._list.count() == 0:
            self._stack.setCurrentWidget(self._empty)
        self._refresh_status()

    def _select_index(self, row: int) -> None:
        it = self._list.item(row) if row >= 0 else None
        if it is None:
            self._stack.setCurrentWidget(self._empty)
            return
        pane = self._panes.get(it.data(Qt.UserRole))
        if pane is not None:
            self._stack.setCurrentWidget(pane)
