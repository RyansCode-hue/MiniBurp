"""
HttpViewer / HttpEditor — shared widgets for showing HTTP request or response
bytes with syntax highlighting, an optional hex view, and an in-place find bar.

Use HttpViewer for read-only panels (Proxy detail, Repeater response) and
HttpEditor for editable panels (Repeater request, Intercept hold, Intruder
template).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import decoder as decoder_module
from ...http_io import encode_http_text
from ..syntax import HttpHighlighter
from .find_bar import FindBar


def _decode(raw: bytes) -> str:
    if not isinstance(raw, (bytes, bytearray)):
        return str(raw)
    try:
        return bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        return bytes(raw).decode("iso-8859-1", errors="replace")


def _hex_dump(raw: bytes, width: int = 16) -> str:
    """Classic offset / hex / ascii dump."""
    if not raw:
        return ""
    raw = bytes(raw)
    lines = []
    for offset in range(0, len(raw), width):
        chunk = raw[offset : offset + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk).ljust(width * 3 - 1)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:08x}  {hex_part}  |{ascii_part}|")
    return "\n".join(lines)


class _CodeEdit(QPlainTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFont("SF Mono", 11)
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)
        self.setTabChangesFocus(False)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Standard menu plus, for editable panes with a selection, an
        Encode/Decode submenu that transforms the selection in place — so you
        can URL-encode a payload or Base64 a cookie without leaving for the
        Decoder tab."""
        menu = self.createStandardContextMenu()
        cursor = self.textCursor()
        ops: dict = {}
        if not self.isReadOnly() and cursor.hasSelection():
            menu.addSeparator()
            sub = menu.addMenu("Encode / Decode selection")
            for key, label in decoder_module.OPS:
                ops[sub.addAction(label)] = key
        chosen = menu.exec(event.globalPos())
        if chosen in ops:
            result, error = decoder_module.run(ops[chosen], cursor.selectedText())
            if not error:
                cursor.insertText(result)


class HttpViewer(QWidget):
    """Read-only pretty viewer with text/hex toggle and find-in-view."""

    def __init__(
        self,
        title: str = "",
        editable: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self._raw_bytes: bytes = b""
        self._editable = editable
        self._mode = "text"  # "text" | "hex"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # --- title / toolbar row ---
        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 4, 0)
        self._title_label = QLabel(title, self)
        self._title_label.setProperty("role", "section")
        self._hex_btn = QPushButton("Hex", self)
        self._hex_btn.setCheckable(True)
        # Don't fix the width — at the previous 48px the text clipped on macOS
        # because button padding (14px each side) + "Hex" glyph width exceeded
        # the budget. Letting QSS padding determine the size keeps it honest
        # across themes / fonts / DPI.
        self._hex_btn.setMinimumWidth(56)
        self._hex_btn.toggled.connect(self._set_hex_mode)

        header.addWidget(self._title_label, 1)
        header.addWidget(self._hex_btn)

        # --- editor ---
        self.edit = _CodeEdit(self)
        self.edit.setReadOnly(not editable)

        self.highlighter = HttpHighlighter(self.edit.document())

        # --- find bar (hidden until Cmd-F) ---
        self.find_bar = FindBar(self)
        self.find_bar.attach(self.edit)

        root.addLayout(header)
        root.addWidget(self.edit, 1)
        root.addWidget(self.find_bar)

    # ---- public API ----------------------------------------------------
    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def set_bytes(self, raw: bytes, title: Optional[str] = None) -> None:
        self._raw_bytes = bytes(raw or b"")
        if title is not None:
            self.set_title(title)
        self._render()

    def set_text(self, text: str, title: Optional[str] = None) -> None:
        """Convenience for places that only have text (e.g. Intruder template)."""
        self.set_bytes(text.encode("utf-8", errors="replace"), title=title)

    def bytes_value(self) -> bytes:
        """Return current editor content as bytes (text mode only).

        In hex mode, returns the original bytes it was loaded with, since
        the hex representation is display-only.
        """
        if self._mode == "hex":
            return self._raw_bytes
        # encode_http_text normalizes newlines to CRLF and falls back to UTF-8
        # when the text isn't Latin-1-representable, so non-ASCII payloads
        # aren't silently turned into '?'.
        return encode_http_text(self.edit.toPlainText())

    def text_value(self) -> str:
        return self.edit.toPlainText()

    def clear(self) -> None:
        self._raw_bytes = b""
        self.edit.clear()

    def refresh_highlight_theme(self) -> None:
        """Called by MainWindow after a theme swap so the highlighter
        re-stains the document with the colors of the new palette."""
        if self.highlighter is not None and self._mode == "text":
            self.highlighter.refresh_theme()

    # ---- internals -----------------------------------------------------
    def _set_hex_mode(self, hex_on: bool) -> None:
        # Preserve edits in text mode before switching.
        if self._mode == "text" and self._editable:
            self._raw_bytes = self.bytes_value()
        self._mode = "hex" if hex_on else "text"
        # Hex is always read-only.
        self.edit.setReadOnly(True if hex_on else (not self._editable))
        self.highlighter.setDocument(None if hex_on else self.edit.document())
        self._render()

    def _render(self) -> None:
        if self._mode == "hex":
            self.edit.setPlainText(_hex_dump(self._raw_bytes))
        else:
            self.edit.setPlainText(_decode(self._raw_bytes))
        # Re-enable the highlighter when leaving hex.
        if self._mode == "text" and self.highlighter.document() is None:
            self.highlighter.setDocument(self.edit.document())
        # Move cursor to top so the viewer starts at the start line.
        cursor = self.edit.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.edit.setTextCursor(cursor)


class HttpEditor(HttpViewer):
    """Editable flavor of HttpViewer."""

    def __init__(self, title: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(title=title, editable=True, parent=parent)
