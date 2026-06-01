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
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ... import decoder as decoder_module
from ... import params as params_module
from ... import payloads as payloads_module
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
        payload_actions: dict = {}
        if not self.isReadOnly():
            if cursor.hasSelection():
                menu.addSeparator()
                sub = menu.addMenu("Encode / Decode selection")
                for key, label in decoder_module.OPS:
                    ops[sub.addAction(label)] = key
            menu.addSeparator()
            pmenu = menu.addMenu("Insert payload")
            for key, name in payloads_module.names():
                cat = pmenu.addMenu(name)
                for p in payloads_module.payloads_for(key):
                    # '&' is a Qt menu mnemonic — escape it in the label only.
                    payload_actions[cat.addAction(p.replace("&", "&&"))] = p
        chosen = menu.exec(event.globalPos())
        if chosen in ops:
            result, error = decoder_module.run(ops[chosen], cursor.selectedText())
            if not error:
                cursor.insertText(result)
        elif chosen in payload_actions:
            cursor.insertText(payload_actions[chosen])


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
        self._mode = "text"  # "text" | "hex" | "render"

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
        self._hex_btn.toggled.connect(self._on_hex_toggled)

        header.addWidget(self._title_label, 1)
        # Render is a read-only-only affordance (it shows the HTML body as a
        # page). It's enabled only when the body looks like HTML.
        self._render_btn: Optional[QPushButton] = None
        if not editable:
            self._render_btn = QPushButton("Render", self)
            self._render_btn.setCheckable(True)
            self._render_btn.setMinimumWidth(64)
            self._render_btn.setEnabled(False)
            self._render_btn.setToolTip("Render the HTML body as a page (no network)")
            self._render_btn.toggled.connect(self._on_render_toggled)
            header.addWidget(self._render_btn)
        # Params is an editable-only affordance: a key/value table for the
        # query string, cookies, and urlencoded body.
        self._params_btn: Optional[QPushButton] = None
        if editable:
            self._params_btn = QPushButton("Params", self)
            self._params_btn.setCheckable(True)
            self._params_btn.setMinimumWidth(64)
            self._params_btn.setToolTip(
                "Edit query / cookie / body parameters as a table"
            )
            self._params_btn.toggled.connect(self._toggle_params)
            header.addWidget(self._params_btn)
        header.addWidget(self._hex_btn)

        # --- editor ---
        self.edit = _CodeEdit(self)
        self.edit.setReadOnly(not editable)

        self.highlighter = HttpHighlighter(self.edit.document())

        # --- render view (read-only viewers only) ---
        # QTextBrowser does not fetch remote resources on its own, so rendering
        # a captured page can't phone home past the proxy.
        self._render_view: Optional[QTextBrowser] = None
        if not editable:
            self._render_view = QTextBrowser(self)
            self._render_view.setOpenExternalLinks(False)
            self._render_view.setOpenLinks(False)
            self._render_view.hide()

        # --- params table (editable viewers only) ---
        self._params_table: Optional[QTableWidget] = None
        self._params: list = []
        self._params_syncing = False
        if editable:
            self._params_table = QTableWidget(0, 3, self)
            self._params_table.setHorizontalHeaderLabels(["Where", "Name", "Value"])
            self._params_table.verticalHeader().setVisible(False)
            ph = self._params_table.horizontalHeader()
            ph.setSectionResizeMode(QHeaderView.Interactive)
            ph.setStretchLastSection(True)
            self._params_table.setColumnWidth(0, 70)
            self._params_table.setColumnWidth(1, 170)
            self._params_table.setMaximumHeight(180)
            self._params_table.hide()
            self._params_table.itemChanged.connect(self._on_param_item_changed)

        # --- find bar (hidden until Cmd-F) ---
        self.find_bar = FindBar(self)
        self.find_bar.attach(self.edit)

        root.addLayout(header)
        root.addWidget(self.edit, 1)
        if self._render_view is not None:
            root.addWidget(self._render_view, 1)
        if self._params_table is not None:
            root.addWidget(self._params_table)
        root.addWidget(self.find_bar)

    # ---- public API ----------------------------------------------------
    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def set_bytes(self, raw: bytes, title: Optional[str] = None) -> None:
        self._raw_bytes = bytes(raw or b"")
        if title is not None:
            self.set_title(title)
        if self._render_btn is not None:
            self._render_btn.setEnabled(self._looks_like_html())
        self._render()

    def set_text(self, text: str, title: Optional[str] = None) -> None:
        """Convenience for places that only have text (e.g. Intruder template)."""
        self.set_bytes(text.encode("utf-8", errors="replace"), title=title)

    def bytes_value(self) -> bytes:
        """Return current editor content as bytes (text mode only).

        In hex mode, returns the original bytes it was loaded with, since
        the hex representation is display-only.
        """
        if self._mode in ("hex", "render"):
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

    def set_reflection_needles(self, needles: list[str]) -> None:
        """Wash-highlight occurrences of request input in this response view."""
        if self.highlighter is not None:
            self.highlighter.set_needles(needles)

    # ---- params panel --------------------------------------------------
    def _toggle_params(self, on: bool) -> None:
        if self._params_table is None:
            return
        if on:
            self._rebuild_params_table()
        self._params_table.setVisible(on)

    def _rebuild_params_table(self) -> None:
        self._params = params_module.parse_params(self.bytes_value())
        self._params_syncing = True
        t = self._params_table
        t.setRowCount(0)
        t.setRowCount(len(self._params))
        for r, p in enumerate(self._params):
            where = QTableWidgetItem(p.location)
            where.setFlags(Qt.ItemIsEnabled)
            name = QTableWidgetItem(p.name)
            name.setFlags(Qt.ItemIsEnabled)
            value = QTableWidgetItem(p.value)  # the only editable column
            t.setItem(r, 0, where)
            t.setItem(r, 1, name)
            t.setItem(r, 2, value)
        self._params_syncing = False

    def _on_param_item_changed(self, item: QTableWidgetItem) -> None:
        if self._params_syncing or item.column() != 2:
            return
        row = item.row()
        if not (0 <= row < len(self._params)):
            return
        self._params[row].value = item.text()
        # Write the edit back into the raw request, then re-sync the table.
        new_raw = params_module.apply_params(self.bytes_value(), self._params)
        self.set_bytes(new_raw)
        self._rebuild_params_table()

    # ---- internals -----------------------------------------------------
    def _looks_like_html(self) -> bool:
        sp = self._raw_bytes.find(b"\r\n\r\n")
        head = self._raw_bytes[:sp] if sp != -1 else b""
        if b"text/html" in head.lower():
            return True
        low = self._raw_bytes[:2000].lower()
        return b"<html" in low or b"<!doctype html" in low

    def _on_hex_toggled(self, on: bool) -> None:
        if on and self._render_btn is not None and self._render_btn.isChecked():
            self._render_btn.blockSignals(True)
            self._render_btn.setChecked(False)
            self._render_btn.blockSignals(False)
        self._set_mode("hex" if on else "text")

    def _on_render_toggled(self, on: bool) -> None:
        if on and self._hex_btn.isChecked():
            self._hex_btn.blockSignals(True)
            self._hex_btn.setChecked(False)
            self._hex_btn.blockSignals(False)
        self._set_mode("render" if on else "text")

    def _set_mode(self, mode: str) -> None:
        # Preserve edits in text mode before switching away.
        if self._mode == "text" and self._editable:
            self._raw_bytes = self.bytes_value()
        self._mode = mode
        # Only text mode is ever editable; hex/render are display-only.
        self.edit.setReadOnly(True if mode != "text" else (not self._editable))
        self.highlighter.setDocument(self.edit.document() if mode == "text" else None)
        if self._render_view is not None:
            self._render_view.setVisible(mode == "render")
        self.edit.setVisible(mode != "render")
        self._render()

    def _render(self) -> None:
        if self._mode == "render":
            if self._render_view is not None:
                sp = self._raw_bytes.find(b"\r\n\r\n")
                body = self._raw_bytes[sp + 4:] if sp != -1 else self._raw_bytes
                self._render_view.setHtml(body.decode("utf-8", errors="replace"))
            return
        if self._mode == "hex":
            self.edit.setPlainText(_hex_dump(self._raw_bytes))
        else:
            self.edit.setPlainText(_decode(self._raw_bytes))
        # Re-enable the highlighter when leaving hex/render.
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
