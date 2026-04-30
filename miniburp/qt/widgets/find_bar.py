"""Reusable Cmd-F find bar for QPlainTextEdit-style viewers."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)


class FindBar(QWidget):
    """
    A slim search bar. Give it a QPlainTextEdit/QTextEdit via `attach()` and
    Cmd-F / Ctrl-F will toggle its visibility and search within that widget.
    """

    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._target = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(6)

        self.input = QLineEdit(self)
        self.input.setPlaceholderText("Find…")
        self.input.returnPressed.connect(self.find_next)

        self.count_label = QLabel("", self)
        self.count_label.setProperty("role", "muted")

        prev_btn = QPushButton("Prev", self)
        next_btn = QPushButton("Next", self)
        close_btn = QPushButton("×", self)
        close_btn.setFixedWidth(24)

        prev_btn.clicked.connect(self.find_prev)
        next_btn.clicked.connect(self.find_next)
        close_btn.clicked.connect(self.hide_bar)

        lay.addWidget(self.input, 1)
        lay.addWidget(self.count_label)
        lay.addWidget(prev_btn)
        lay.addWidget(next_btn)
        lay.addWidget(close_btn)

        self.hide()

    def attach(self, text_edit) -> None:
        self._target = text_edit
        sc_find = QShortcut(QKeySequence.Find, text_edit)
        sc_find.activated.connect(self.show_bar)
        sc_hide = QShortcut(QKeySequence(Qt.Key_Escape), self.input)
        sc_hide.activated.connect(self.hide_bar)

    def show_bar(self) -> None:
        self.show()
        self.input.setFocus()
        self.input.selectAll()

    def hide_bar(self) -> None:
        self.hide()
        if self._target is not None:
            self._target.setFocus()
        self.closed.emit()

    def find_next(self) -> None:
        self._search(backwards=False)

    def find_prev(self) -> None:
        self._search(backwards=True)

    def _search(self, backwards: bool) -> None:
        if self._target is None:
            return
        needle = self.input.text()
        if not needle:
            self.count_label.setText("")
            return
        flags = QTextDocument.FindFlag(0)
        if backwards:
            flags |= QTextDocument.FindBackward
        found = self._target.find(needle, flags)
        if not found:
            # wrap to start / end
            cursor = self._target.textCursor()
            cursor.movePosition(QTextCursor.End if backwards else QTextCursor.Start)
            self._target.setTextCursor(cursor)
            found = self._target.find(needle, flags)
        self.count_label.setText("" if found else "no matches")
