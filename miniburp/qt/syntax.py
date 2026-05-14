"""
Lightweight HTTP / JSON syntax highlighter for QPlainTextEdit.

The highlighter is intentionally heuristic — it recognizes the HTTP request
line, response status line, header-name: value pairs, JSON primitives, and
simple HTML tags. It is good enough to make bodies readable without pulling in
a full tree-sitter grammar.

The colour palette swaps with the theme. On dark we use bright, low-saturation
tints; on light we use deeper variants that pass legibility checks against the
warm-off-white surface. ``refresh_theme()`` is called by ``MainWindow`` after
the global QSS + palette swap so every active highlighter re-stains itself.
"""
from __future__ import annotations

import re

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QPalette, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QApplication


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Bold)
    if italic:
        f.setFontItalic(True)
    return f


# Per-theme palettes. Keys map onto the eight `fmt_*` attributes the
# highlighter assigns to tokens. Light-mode values are deeper / more saturated
# so they read cleanly on the #fbfbf7 surface; the dark-mode values match the
# theme accents.
_DARK_PALETTE = {
    "method":       "#5ecb7b",  # green, bold
    "path":         "#ebebed",  # ~ fg
    "proto":        "#7aa2ff",
    "status":       "#ff7d3d",  # ~ accent, bold
    "header_name":  "#e7b648",
    "header_value": "#ebebed",
    "string":       "#5ecb7b",
    "number":       "#7aa2ff",
    "constant":     "#ff7d3d",
    "tag":          "#7aa2ff",
    "comment":      "#8b8b93",
}
_LIGHT_PALETTE = {
    "method":       "#1c9c4d",  # forest green
    "path":         "#1a1a1d",  # ~ fg (near black for legibility)
    "proto":        "#2858a8",
    "status":       "#cd4d15",  # deep orange, matches the light accent
    "header_name":  "#9a6b14",  # amber
    "header_value": "#1a1a1d",
    "string":       "#1c9c4d",
    "number":       "#2858a8",
    "constant":     "#cd4d15",
    "tag":          "#2858a8",
    "comment":      "#6a6a72",
}


def _current_theme() -> str:
    """Detect whether we're in light or dark mode from the app palette.

    The Window role is the most reliable indicator since `_apply_palette` in
    MainWindow sets it directly from the active theme's bg colour. We treat
    anything brighter than 50% lightness as light mode.
    """
    qapp = QApplication.instance()
    if qapp is None:
        return "dark"
    bg = qapp.palette().color(QPalette.Window)
    return "light" if bg.lightnessF() > 0.5 else "dark"


class HttpHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        self._apply_palette(_current_theme())

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

    def _apply_palette(self, theme: str) -> None:
        """Pick a per-theme palette and rebuild the format objects."""
        pal = _LIGHT_PALETTE if theme == "light" else _DARK_PALETTE
        self.fmt_method = _fmt(pal["method"], bold=True)
        self.fmt_path = _fmt(pal["path"])
        self.fmt_proto = _fmt(pal["proto"])
        self.fmt_status = _fmt(pal["status"], bold=True)
        self.fmt_header_name = _fmt(pal["header_name"])
        self.fmt_header_value = _fmt(pal["header_value"])
        self.fmt_string = _fmt(pal["string"])
        self.fmt_number = _fmt(pal["number"])
        self.fmt_constant = _fmt(pal["constant"], bold=True)
        self.fmt_tag = _fmt(pal["tag"])
        self.fmt_comment = _fmt(pal["comment"], italic=True)

    def refresh_theme(self) -> None:
        """Re-pick the palette from the current app state and repaint."""
        self._apply_palette(_current_theme())
        self.rehighlight()

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
