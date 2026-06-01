"""
Proxy / History tab.

Shows a live-updating table of captured transactions, with a filter bar and
in-scope toggle. Selecting a row populates request + response viewers in a
vertical splitter below.

The table is backed by a custom QAbstractTableModel so we can append rows
cheaply when the proxy captures new traffic.
"""
from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ... import bodydecode
from ... import reflect
from ...curl import to_curl
from ...scope import SCOPE
from ...storage import STORE
from ..signals import StoreBridge
from ..widgets.combobox import MiniBurpComboBox
from ..widgets.http_viewer import HttpViewer


COLUMNS = ["#", "Method", "Host", "Path", "Status", "Length", "Time"]


class _HistoryModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        # newest first
        for tx in reversed(STORE.all()):
            self._rows.append(tx.summary())

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return COLUMNS[section]
        return section + 1

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return row.get("id")
            if col == 1:
                return row.get("method") or ""
            if col == 2:
                return row.get("host") or ""
            if col == 3:
                return row.get("path") or ""
            if col == 4:
                s = row.get("status")
                return str(s) if s is not None else "-"
            if col == 5:
                return row.get("length", 0)
            if col == 6:
                d = row.get("duration_ms")
                return f"{d} ms" if d is not None else "-"
        if role == Qt.ForegroundRole:
            if col == 1:
                m = (row.get("method") or "").upper()
                return QBrush(QColor({
                    "GET": "#5ecb7b",
                    "POST": "#e7b648",
                    "PUT": "#7aa2ff",
                    "PATCH": "#7aa2ff",
                    "DELETE": "#ef6767",
                }.get(m, "#e8e8ec")))
            if col == 4:
                s = row.get("status") or 0
                color = "#9a9aa8"
                if 200 <= s < 300:
                    color = "#5ecb7b"
                elif 300 <= s < 400:
                    color = "#7aa2ff"
                elif 400 <= s < 500:
                    color = "#e7b648"
                elif s >= 500:
                    color = "#ef6767"
                return QBrush(QColor(color))
        if role == Qt.UserRole:
            return row
        return None

    # ---- external mutation ---------------------------------------------
    def row_for_id(self, tx_id: int) -> int:
        for i, row in enumerate(self._rows):
            if row.get("id") == tx_id:
                return i
        return -1

    def add_tx(self, summary: dict) -> None:
        self.beginInsertRows(QModelIndex(), 0, 0)
        self._rows.insert(0, summary)
        self.endInsertRows()

    def update_tx(self, summary: dict) -> None:
        tx_id = summary.get("id")
        if tx_id is None:
            return
        i = self.row_for_id(tx_id)
        if i < 0:
            return
        self._rows[i] = summary
        top = self.index(i, 0)
        bot = self.index(i, self.columnCount() - 1)
        self.dataChanged.emit(top, bot)

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()


class _FilterModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.method_filter = ""   # exact method, "" = any
        self.host_filter = ""     # substring, case-insensitive
        self.path_filter = ""     # substring
        self.status_filter = ""   # "", "2xx", "3xx", "4xx", "5xx"
        self.scope_only = True

        # Full-text "search" — matches URL parts, raw request/response, and
        # the decoded response body. Separate from the metadata filters above
        # so the user can stack them ("4xx requests containing 'csrf'").
        self.search_text = ""
        self.search_regex = False
        self._search_re: Optional[re.Pattern] = None
        self._search_needle_bytes: bytes = b""

        self.setFilterKeyColumn(-1)

    def set_search(self, text: str, use_regex: bool) -> None:
        """Update the search criterion and reapply.

        Empty text disables the search entirely. An invalid regex disables
        the regex part (caller can show a hint) but the table is still usable.
        """
        self.search_text = text
        self.search_regex = use_regex
        self._search_re = None
        self._search_needle_bytes = text.lower().encode("utf-8", errors="ignore")
        if text and use_regex:
            try:
                self._search_re = re.compile(text, re.IGNORECASE)
            except re.error:
                self._search_re = None
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, parent: QModelIndex) -> bool:  # noqa: N802
        src = self.sourceModel()
        idx = src.index(source_row, 0, parent)
        row = src.data(idx, Qt.UserRole)
        if not isinstance(row, dict):
            return True

        if self.method_filter and (row.get("method") or "").upper() != self.method_filter.upper():
            return False
        if self.host_filter and self.host_filter.lower() not in (row.get("host") or "").lower():
            return False
        if self.path_filter and self.path_filter.lower() not in (row.get("path") or "").lower():
            return False
        if self.status_filter:
            s = row.get("status") or 0
            bucket = f"{s // 100}xx"
            if bucket != self.status_filter:
                return False
        if self.scope_only:
            host = row.get("host") or ""
            if host and not SCOPE.in_scope(host):
                return False

        if self.search_text:
            tx_id = row.get("id")
            tx = STORE.get(tx_id) if tx_id is not None else None
            if tx is None:
                return False
            if not self._row_matches_search(tx):
                return False
        return True

    def _row_matches_search(self, tx) -> bool:
        """Test a Transaction against the current search criterion."""
        if self.search_regex:
            if self._search_re is None:
                # Regex mode with an invalid pattern: hide everything so the
                # user notices their typo rather than getting silent results.
                return False
            haystacks_text = [
                tx.host or "",
                tx.path or "",
                (tx.raw_request or b"").decode("iso-8859-1", errors="replace"),
                (tx.raw_response or b"").decode("iso-8859-1", errors="replace"),
                tx.decoded_resp_body().decode("iso-8859-1", errors="replace"),
            ]
            return any(self._search_re.search(s) for s in haystacks_text)

        needle = self._search_needle_bytes
        if not needle:
            return True
        if needle in (tx.host or "").lower().encode("utf-8", errors="ignore"):
            return True
        if needle in (tx.path or "").lower().encode("utf-8", errors="ignore"):
            return True
        if needle in (tx.raw_request or b"").lower():
            return True
        if needle in (tx.raw_response or b"").lower():
            return True
        if needle in tx.decoded_resp_body().lower():
            return True
        return False


class ProxyTab(QWidget):
    """History + request/response viewers."""

    send_to_repeater = Signal(int)   # tx_id
    send_to_intruder = Signal(int)   # tx_id

    def __init__(self, bridge: StoreBridge, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._bridge = bridge

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ---- filter bar ----
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)

        self._method_combo = MiniBurpComboBox(self)
        self._method_combo.addItems(["Any method", "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        self._host_edit = QLineEdit(self)
        self._host_edit.setPlaceholderText("Host contains…")
        self._path_edit = QLineEdit(self)
        self._path_edit.setPlaceholderText("Path contains…")
        self._status_combo = MiniBurpComboBox(self)
        self._status_combo.addItems(["Any status", "2xx", "3xx", "4xx", "5xx"])
        self._scope_check = QCheckBox("In-scope only", self)
        self._scope_check.setChecked(True)
        self._clear_btn = QPushButton("Clear history", self)

        filter_row.addWidget(QLabel("Filter:", self))
        filter_row.addWidget(self._method_combo)
        filter_row.addWidget(self._host_edit, 1)
        filter_row.addWidget(self._path_edit, 1)
        filter_row.addWidget(self._status_combo)
        filter_row.addWidget(self._scope_check)
        filter_row.addStretch(1)
        filter_row.addWidget(self._clear_btn)

        # ---- search row ----
        # Sits below the filter row. Searches raw request bytes, raw response
        # bytes, and the decoded response body — so a 'csrf-token' query hits
        # gzipped responses too.
        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        search_row.addWidget(QLabel("Search:", self))
        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText(
            "Substring across URL, request, response, decoded body…"
        )
        self._search_edit.setClearButtonEnabled(True)
        # Cap the edit so Regex / status never slide off the right edge on
        # narrow windows; keep a stretch factor so it still grows up to the
        # max on wide ones.
        self._search_edit.setMaximumWidth(480)
        self._search_edit.setMinimumWidth(220)
        self._regex_check = QCheckBox("Regex", self)
        self._search_status = QLabel("", self)
        self._search_status.setProperty("role", "muted")
        search_row.addWidget(self._search_edit, 1)
        search_row.addWidget(self._regex_check)
        search_row.addWidget(self._search_status)
        # Soak up the slack on wide windows so the row reads left-aligned
        # instead of stretching the edit + status across the whole pane.
        search_row.addStretch(1)

        # ---- table ----
        self._source = _HistoryModel(self)
        self._filter = _FilterModel(self)
        self._filter.setSourceModel(self._source)
        self._table = QTableView(self)
        self._table.setModel(self._filter)
        self._table.setSelectionBehavior(QTableView.SelectRows)
        self._table.setSelectionMode(QTableView.SingleSelection)
        self._table.setEditTriggers(QTableView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        # All columns user-resizable. Path is the one that benefits from
        # stretching since URLs vary wildly; the rest get explicit widths
        # sized for the new uppercase-spaced header glyphs.
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(3, QHeaderView.Stretch)  # Path
        self._table.setColumnWidth(0, 50)   # #
        self._table.setColumnWidth(1, 90)   # Method
        self._table.setColumnWidth(2, 260)  # Host
        # col 3 (Path) is stretched
        self._table.setColumnWidth(4, 85)   # Status
        self._table.setColumnWidth(5, 95)   # Length
        self._table.setColumnWidth(6, 100)  # Time

        # ---- detail splitter ----
        self._req_viewer = HttpViewer("Request", editable=False, parent=self)
        self._res_viewer = HttpViewer("Response", editable=False, parent=self)

        self._detail_splitter = QSplitter(Qt.Horizontal, self)
        self._detail_splitter.addWidget(self._req_viewer)
        self._detail_splitter.addWidget(self._res_viewer)
        self._detail_splitter.setStretchFactor(0, 1)
        self._detail_splitter.setStretchFactor(1, 1)

        # ---- action row below table ----
        action_row = QHBoxLayout()
        self._repeater_btn = QPushButton("Send to Repeater", self)
        self._intruder_btn = QPushButton("Send to Intruder", self)
        self._repeater_btn.setEnabled(False)
        self._intruder_btn.setEnabled(False)
        action_row.addWidget(self._repeater_btn)
        action_row.addWidget(self._intruder_btn)
        action_row.addStretch(1)

        # ---- main vertical splitter ----
        self._main_splitter = QSplitter(Qt.Vertical, self)
        top_widget = QWidget(self)
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addLayout(filter_row)
        top_layout.addLayout(search_row)
        top_layout.addWidget(self._table, 1)
        top_layout.addLayout(action_row)
        self._main_splitter.addWidget(top_widget)
        self._main_splitter.addWidget(self._detail_splitter)
        self._main_splitter.setStretchFactor(0, 1)
        self._main_splitter.setStretchFactor(1, 1)

        root.addWidget(self._main_splitter, 1)

        # ---- wire signals ----
        bridge.added.connect(self._source.add_tx)
        bridge.updated.connect(self._on_updated)
        bridge.cleared.connect(self._source.clear)

        self._method_combo.currentIndexChanged.connect(self._apply_filters)
        self._status_combo.currentIndexChanged.connect(self._apply_filters)
        self._host_edit.textChanged.connect(self._apply_filters)
        self._path_edit.textChanged.connect(self._apply_filters)
        self._scope_check.toggled.connect(self._apply_filters)
        self._search_edit.textChanged.connect(self._apply_search)
        self._regex_check.toggled.connect(self._apply_search)
        self._clear_btn.clicked.connect(STORE.clear)

        self._table.selectionModel().currentRowChanged.connect(self._on_row_changed)
        self._repeater_btn.clicked.connect(lambda: self._emit_selected(self.send_to_repeater))
        self._intruder_btn.clicked.connect(lambda: self._emit_selected(self.send_to_intruder))

        # Right-click actions on a history row (README documents these) plus
        # double-click → Repeater for the common "grab this and iterate" move.
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.doubleClicked.connect(self._on_double_click)

    # ---- slots ---------------------------------------------------------
    def _apply_filters(self) -> None:
        mi = self._method_combo.currentIndex()
        self._filter.method_filter = "" if mi == 0 else self._method_combo.currentText()
        si = self._status_combo.currentIndex()
        self._filter.status_filter = "" if si == 0 else self._status_combo.currentText()
        self._filter.host_filter = self._host_edit.text()
        self._filter.path_filter = self._path_edit.text()
        self._filter.scope_only = self._scope_check.isChecked()
        self._filter.invalidateFilter()

    def _apply_search(self) -> None:
        text = self._search_edit.text()
        use_regex = self._regex_check.isChecked()
        self._filter.set_search(text, use_regex)
        # Surface a hint when the user types an invalid regex; otherwise quiet.
        if text and use_regex:
            try:
                re.compile(text)
                self._search_status.setText("")
            except re.error as e:
                self._search_status.setText(f"bad regex: {e}")
        else:
            self._search_status.setText("")

    def _on_updated(self, summary: dict) -> None:
        self._source.update_tx(summary)
        # if the updated row is currently selected, refresh the viewers.
        idx = self._table.currentIndex()
        if idx.isValid():
            row = idx.data(Qt.UserRole)
            if isinstance(row, dict) and row.get("id") == summary.get("id"):
                self._load_detail(summary.get("id"))

    def _on_row_changed(self, current: QModelIndex, _prev: QModelIndex) -> None:
        if not current.isValid():
            self._req_viewer.clear()
            self._res_viewer.clear()
            self._repeater_btn.setEnabled(False)
            self._intruder_btn.setEnabled(False)
            return
        row = current.data(Qt.UserRole)
        tx_id = row.get("id") if isinstance(row, dict) else None
        if tx_id is None:
            return
        self._repeater_btn.setEnabled(True)
        self._intruder_btn.setEnabled(True)
        self._load_detail(tx_id)

    def _load_detail(self, tx_id: int) -> None:
        tx = STORE.get(tx_id)
        if not tx:
            return
        self._req_viewer.set_bytes(tx.raw_request, title=f"Request #{tx.id}")

        # Decode body for display if possible.
        decoded_body, note = bodydecode.decode_body(tx.resp_body, tx.resp_headers)
        if tx.raw_response:
            split = tx.raw_response.find(b"\r\n\r\n")
            if note == "" and split != -1 and decoded_body is not tx.resp_body:
                display = tx.raw_response[: split + 4] + decoded_body
            else:
                display = tx.raw_response
        else:
            display = b""
        title = f"Response #{tx.id}"
        if note:
            title += f"  [body decode: {note}]"
        elif tx.resp_headers.get("Content-Encoding"):
            title += "  [decoded]"
        # Highlight where this request's input echoes in the response body.
        needles = reflect.request_tokens(tx.raw_request or b"")
        hits = reflect.reflected(needles, decoded_body.decode("iso-8859-1", errors="replace"))
        if hits:
            title += f"  ·  {len(hits)} reflected"
        self._res_viewer.set_bytes(display, title=title)
        self._res_viewer.set_reflection_needles(hits)

    def _emit_selected(self, sig: Signal) -> None:
        idx = self._table.currentIndex()
        if not idx.isValid():
            return
        row = idx.data(Qt.UserRole)
        if isinstance(row, dict) and row.get("id") is not None:
            sig.emit(row["id"])

    # ---- context menu / double-click -----------------------------------
    def _tx_id_at(self, index: QModelIndex) -> Optional[int]:
        if not index.isValid():
            return None
        row = index.data(Qt.UserRole)
        return row.get("id") if isinstance(row, dict) else None

    def _on_double_click(self, index: QModelIndex) -> None:
        tx_id = self._tx_id_at(index)
        if tx_id is not None:
            self.send_to_repeater.emit(tx_id)

    def _show_context_menu(self, pos) -> None:
        index = self._table.indexAt(pos)
        if index.isValid():
            # Select the row under the cursor so the action targets it even if
            # the user right-clicked a different row than the selected one.
            self._table.selectRow(index.row())
        tx_id = self._tx_id_at(self._table.currentIndex())
        tx = STORE.get(tx_id) if tx_id is not None else None
        if tx is None:
            return

        menu = QMenu(self)
        act_rep = menu.addAction("Send to Repeater")
        act_intr = menu.addAction("Send to Intruder")
        menu.addSeparator()
        act_url = menu.addAction("Copy URL")
        act_req = menu.addAction("Copy request")
        act_curl = menu.addAction("Copy as curl")

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        clip = QApplication.clipboard()
        if chosen is act_rep:
            self.send_to_repeater.emit(tx.id)
        elif chosen is act_intr:
            self.send_to_intruder.emit(tx.id)
        elif chosen is act_url:
            clip.setText(tx.url)
        elif chosen is act_req:
            clip.setText((tx.raw_request or b"").decode("iso-8859-1", errors="replace"))
        elif chosen is act_curl:
            clip.setText(to_curl(tx.method, tx.url, tx.req_headers, tx.req_body))
