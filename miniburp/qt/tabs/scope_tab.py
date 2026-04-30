"""
Scope tab — a single textarea of host glob patterns, one per line.

Empty list = everything in scope.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...scope import SCOPE


class ScopeTab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        help_text = QLabel(
            "One host pattern per line. Shell globs work: "
            "<code>*.web-security-academy.net</code>, <code>lab.example.com</code>, "
            "<code>*.corp.*</code>. Leave empty to treat everything as in scope.",
            self,
        )
        help_text.setTextFormat(Qt.TextFormat.RichText)
        help_text.setWordWrap(True)
        help_text.setProperty("role", "muted")

        self._edit = QPlainTextEdit(self)
        self._edit.setPlainText("\n".join(SCOPE.patterns()))
        self._edit.setPlaceholderText("*.web-security-academy.net")

        self._status = QLabel("", self)
        self._status.setProperty("role", "ok")

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save", self)
        reset_btn = QPushButton("Reset", self)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self._status)

        root.addWidget(help_text)
        root.addWidget(self._edit, 1)
        root.addLayout(btn_row)

        save_btn.clicked.connect(self._save)
        reset_btn.clicked.connect(self._reset)

    def _save(self) -> None:
        patterns = self._edit.toPlainText().splitlines()
        SCOPE.set_patterns(patterns)
        self._status.setText(f"Saved {len(SCOPE.patterns())} pattern(s).")

    def _reset(self) -> None:
        self._edit.setPlainText("\n".join(SCOPE.patterns()))
        self._status.clear()
