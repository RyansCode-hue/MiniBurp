"""
Decoder tab — multi-op pipeline equivalent to Burp's Decoder.

Users enter text, pick an op, and click Apply. The result becomes the new
input so ops can be chained. A reset button restores the current input.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from ... import decoder as decoder_module


class DecoderTab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("Operation:", self))
        self._op = QComboBox(self)
        for key, label in decoder_module.OPS:
            self._op.addItem(label, key)
        self._op.setCurrentIndex(1)  # base64-decode by default
        op_row.addWidget(self._op)

        self._apply_btn = QPushButton("Apply →", self)
        self._chain_btn = QPushButton("Apply to output", self)
        self._clear_btn = QPushButton("Clear", self)

        op_row.addWidget(self._apply_btn)
        op_row.addWidget(self._chain_btn)
        op_row.addStretch(1)
        op_row.addWidget(self._clear_btn)

        self._error = QLabel("", self)
        self._error.setProperty("role", "err")

        self._in = QPlainTextEdit(self)
        self._in.setPlaceholderText("Input text…")
        self._out = QPlainTextEdit(self)
        self._out.setPlaceholderText("Output appears here")

        split = QSplitter(Qt.Horizontal, self)

        left = QWidget(self)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Input", self))
        lv.addWidget(self._in, 1)

        right = QWidget(self)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("Output", self))
        rv.addWidget(self._out, 1)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        root.addLayout(op_row)
        root.addWidget(self._error)
        root.addWidget(split, 1)

        self._apply_btn.clicked.connect(self._apply)
        self._chain_btn.clicked.connect(self._chain)
        self._clear_btn.clicked.connect(self._clear)

    def _apply(self) -> None:
        op = self._op.currentData()
        text = self._in.toPlainText()
        result, error = decoder_module.run(op, text)
        self._out.setPlainText(result)
        self._error.setText(error or "")

    def _chain(self) -> None:
        # Copy current output back to the input and apply again.
        self._in.setPlainText(self._out.toPlainText())
        self._apply()

    def _clear(self) -> None:
        self._in.clear()
        self._out.clear()
        self._error.clear()
