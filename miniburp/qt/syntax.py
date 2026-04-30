"""
Lightweight HTTP / JSON syntax highlighter for QPlainTextEdit.

The highlighter is intentionally heuristic — it recognizes the HTTP request
line, response status line, header-name: value pairs, JSON primitives, and
simple HTML tags. It is good enough to make bodies readable without pulling in
a full tree-sitter grammar.
"""
from __future__ import annotations

import re

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Bold)
    if italic:
        f.setFontItalic(True)
    return f


class HttpHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)

        # Palette tuned for the dark theme used elsewhere.
        self.fmt_method = _fmt("#5ecb7b", bold=True)
        self.fmt_path = _fmt("#e8e8ec")
        self.fmt_proto = _fmt("#7aa2ff")
        self.fmt_status = _fmt("#ff7030", bold=True)
        self.fmt_header_name = _fmt("#e7b648")
        self.fmt_header_value = _fmt("#e8e8ec")
        self.fmt_string = _fmt("#5ecb7b")
        self.fmt_number = _fmt("#7aa2ff")
        self.fmt_constant = _fmt("#ff7030", bold=True)
        self.fmt_tag = _fmt("#7aa2ff")
        self.fmt_comment = _fmt("#9a9aa8", italic=True)

        self._request_line = QRegularExpression(
            r"^([A-Z]+)\s+(\S+)\s+(HTTP/\d(?:\.\d)?)\s*$"
        )
        self._status_line = QRegularExpression(
            r"^(HTTP/\d(?:\.\d)?)\s+(\d{3})\s+(.*)$"
        )
        self._header = QRegularExpression(
            r"^([A-Za-z0-9!#$%&'*+.^_`|~-]+)\s*:\s*(.*)$"
        )
        self._json_string = QRegularExpression(r'"(?:[^"\\]|\\.)*"')
        self._json_number = QRegularExpression(r"-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b")
        self._json_const = QRegularExpression(r"\b(true|false|null)\b")
        self._html_tag = QRegularExpression(r"<\s*/?\s*[A-Za-z][^>]*>")

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        if not text:
            return

        # Request / status line only make sense on line 0 of a block stream,
        # but QSyntaxHighlighter feeds us one logical line at a time; match
        # liberally.
        m = self._request_line.match(text)
        if m.hasMatch():
            self.setFormat(m.capturedStart(1), m.capturedLength(1), self.fmt_method)
            self.setFormat(m.capturedStart(2), m.capturedLength(2), self.fmt_path)
            self.setFormat(m.capturedStart(3), m.capturedLength(3), self.fmt_proto)
            return

        m = self._status_line.match(text)
        if m.hasMatch():
            self.setFormat(m.capturedStart(1), m.capturedLength(1), self.fmt_proto)
            self.setFormat(m.capturedStart(2), m.capturedLength(2), self.fmt_status)
            self.setFormat(m.capturedStart(3), m.capturedLength(3), self.fmt_header_value)
            return

        m = self._header.match(text)
        if m.hasMatch() and ":" in text:
            # Only treat as header if it's the start of the line and plausible.
            name_len = m.capturedLength(1)
            if name_len < 64:  # sanity guard against matching e.g. URL query
                self.setFormat(0, name_len, self.fmt_header_name)
                self.setFormat(
                    m.capturedStart(2),
                    m.capturedLength(2),
                    self.fmt_header_value,
                )
                return

        # Fallback: JSON/HTML fragments inside body.
        self._apply(self._json_string, text, self.fmt_string)
        self._apply(self._json_number, text, self.fmt_number)
        self._apply(self._json_const, text, self.fmt_constant)
        self._apply(self._html_tag, text, self.fmt_tag)

    def _apply(self, rx: QRegularExpression, text: str, fmt: QTextCharFormat) -> None:
        it = rx.globalMatch(text)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(), m.capturedLength(), fmt)
