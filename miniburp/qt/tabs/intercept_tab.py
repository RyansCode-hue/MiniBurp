"""
Intercept tab.

Shows every request currently held by INTERCEPT.hold(). Each held request gets
an editable HttpEditor and Forward / Drop buttons. A top toolbar toggles
Intercept ON/OFF and Forward-all.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
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
from ...storage import STORE
from ..signals import StoreBridge
from ..widgets.http_viewer import HttpEditor


class _HoldPane(QWidget):
    """One editable viewer + action buttons for a single held tx_id."""

    def __init__(self, tx_id: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._tx_id = tx_id

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self._title = QLabel(f"#{tx_id}", self)
        self._title.setProperty("role", "title")

        self.editor = HttpEditor(f"Held #{tx_id}", parent=self)

        # Populate current held bytes.
        raw = INTERCEPT.held_bytes(tx_id)
        if raw is not None:
            self.editor.set_bytes(raw, title=f"Held #{tx_id}")

        # Also enrich the title with method + URL from STORE when possible.
        tx = STORE.get(tx_id)
        if tx is not None:
            self._title.setText(f"#{tx_id}  {tx.method}  {tx.url}")

        btn_row = QHBoxLayout()
        self._forward_btn = QPushButton("Forward", self)
        self._forward_btn.setProperty("role", "primary")
        self._forward_btn.setToolTip("Forward this request  (⌘↵ / Ctrl+Enter)")
        self._drop_btn = QPushButton("Drop", self)
        self._drop_btn.setProperty("role", "danger")
        self._drop_btn.setToolTip("Drop this request  (⌘⌫ / Ctrl+Backspace)")
        btn_row.addWidget(self._forward_btn)
        btn_row.addWidget(self._drop_btn)
        btn_row.addStretch(1)

        root.addWidget(self._title)
        root.addWidget(self.editor, 1)
        root.addLayout(btn_row)

        self._forward_btn.clicked.connect(self._forward)
        self._drop_btn.clicked.connect(self._drop)

        # Forward / Drop without leaving the keyboard. Scoped to this pane so an
        # intercept-heavy lab can be cleared by hand quickly.
        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(self._forward)
        sc_drop = QShortcut(QKeySequence("Ctrl+Backspace"), self)
        sc_drop.setContext(Qt.WidgetWithChildrenShortcut)
        sc_drop.activated.connect(self._drop)

    def _forward(self) -> None:
        INTERCEPT.forward(self._tx_id, self.editor.bytes_value())

    def _drop(self) -> None:
        INTERCEPT.drop(self._tx_id)


class InterceptTab(QWidget):
    def __init__(self, bridge: StoreBridge, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # --- toolbar ---
        toolbar = QHBoxLayout()
        self._toggle_btn = QPushButton("Turn ON", self)
        self._toggle_btn.setCheckable(True)
        self._forward_all_btn = QPushButton("Forward all", self)
        self._forward_all_btn.setProperty("role", "primary")
        self._status_label = QLabel("Intercept: OFF", self)
        self._status_label.setProperty("role", "muted")

        toolbar.addWidget(self._toggle_btn)
        toolbar.addWidget(self._forward_all_btn)
        toolbar.addWidget(self._status_label)
        toolbar.addStretch(1)

        # --- hold list + stack ---
        self._list = QListWidget(self)
        self._list.setMaximumWidth(220)
        self._stack = QStackedWidget(self)
        self._empty = QLabel(
            "No requests are held.\n\n"
            "Turn Intercept ON and trigger a request in the tracked browser.\n"
            "In-scope requests will appear here for editing.",
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

        # --- state ---
        self._panes: dict[int, _HoldPane] = {}

        # --- wire ---
        self._toggle_btn.clicked.connect(self._toggle)
        self._forward_all_btn.clicked.connect(INTERCEPT.forward_all)
        self._list.currentRowChanged.connect(self._select_index)
        bridge.intercept_held.connect(self._on_held)
        bridge.intercept_released.connect(self._on_released)

        self._refresh_status()
        # Populate any already-held requests (reconnect case).
        for tid in INTERCEPT.held_ids():
            self._on_held(tid)

    # ---- toolbar -------------------------------------------------------
    def _toggle(self) -> None:
        INTERCEPT.set_enabled(not INTERCEPT.is_enabled())
        self._refresh_status()

    def _refresh_status(self) -> None:
        on = INTERCEPT.is_enabled()
        self._toggle_btn.setText("Turn OFF" if on else "Turn ON")
        # Keep the visual checked state in sync — Intercept can also be flipped
        # from the main toolbar, so we mirror the model rather than relying on
        # click-driven toggling alone.
        if self._toggle_btn.isChecked() != on:
            self._toggle_btn.blockSignals(True)
            self._toggle_btn.setChecked(on)
            self._toggle_btn.blockSignals(False)
        self._status_label.setText(
            f"Intercept: {'ON' if on else 'OFF'} · held: {len(self._panes)}"
        )

    # ---- held lifecycle ------------------------------------------------
    def _on_held(self, tx_id: int) -> None:
        if tx_id in self._panes:
            return
        pane = _HoldPane(tx_id, parent=self)
        self._panes[tx_id] = pane
        self._stack.addWidget(pane)
        item = QListWidgetItem(f"#{tx_id}", self._list)
        item.setData(Qt.UserRole, tx_id)
        self._list.addItem(item)
        if self._list.count() == 1:
            self._list.setCurrentRow(0)
        self._refresh_status()

    def _on_released(self, tx_id: int) -> None:
        pane = self._panes.pop(tx_id, None)
        if pane is not None:
            self._stack.removeWidget(pane)
            pane.deleteLater()
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it and it.data(Qt.UserRole) == tx_id:
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
        tx_id = it.data(Qt.UserRole)
        pane = self._panes.get(tx_id)
        if pane is not None:
            self._stack.setCurrentWidget(pane)
