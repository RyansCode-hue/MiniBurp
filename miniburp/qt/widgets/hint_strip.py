"""
HintStrip — a slim, dismissible one-line tip shown at the top of a tab.

MiniBurp is a teaching tool but had no in-app guidance, so first-timers hit
silent failures (a request "sails through un-held" because Scope and Intercept
weren't both on) with nothing in the UI to explain why. A HintStrip carries a
short, theme-aware tip; the × dismisses it for the session.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class HintStrip(QWidget):
    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)

        icon = QLabel("💡", self)
        self._label = QLabel(text, self)
        self._label.setProperty("role", "muted")
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.TextFormat.RichText)

        close = QPushButton("×", self)
        close.setProperty("role", "tabClose")
        close.setFixedSize(18, 18)
        close.setFocusPolicy(Qt.NoFocus)
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip("Dismiss")
        close.clicked.connect(self.hide)

        lay.addWidget(icon)
        lay.addWidget(self._label, 1)
        lay.addWidget(close)

    def set_text(self, text: str) -> None:
        self._label.setText(text)
