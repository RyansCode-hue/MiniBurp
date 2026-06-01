"""
Intruder tab.

Attack-mode combo + target row + marker template + N payload lists + a
background worker that runs the attack and streams results into a table.
"""
from __future__ import annotations

import itertools
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import bodydecode
from ... import payloads as payloads_module
from ...http_io import encode_http_text
from ...sender import recompute_content_length, send_raw
from ...storage import STORE
from ..widgets.combobox import MiniBurpComboBox
from ..widgets.hint_strip import HintStrip
from ..widgets.http_viewer import HttpEditor


MARK_RE = re.compile(r"§[^§]*§")


class _NumericItem(QTableWidgetItem):
    """Table cell that sorts on a numeric key while displaying formatted text.

    QTableWidget's default sort compares `data(Qt.DisplayRole)` as strings,
    so columns like Status / Length / Time would sort '10' before '2'. We
    keep an explicit `_sort_value` and override `__lt__` so clicking the
    column header gives proper numeric ascending / descending order.
    """

    def __init__(self, sort_value, display: Optional[str] = None) -> None:
        super().__init__(display if display is not None else str(sort_value))
        self._sort_value = sort_value

    def __lt__(self, other: object) -> bool:  # noqa: D401 - Qt comparison hook
        if isinstance(other, _NumericItem):
            # None sorts to the end regardless of direction so partial /
            # in-flight rows don't jump above completed ones.
            if self._sort_value is None and other._sort_value is None:
                return False
            if self._sort_value is None:
                return False
            if other._sort_value is None:
                return True
            return self._sort_value < other._sort_value
        return super().__lt__(other)


def _replace_markers(template: str, replacements: list[str]) -> str:
    it = iter(replacements)
    return MARK_RE.sub(lambda _m: next(it, ""), template)


def _build_attempts(
    mode: str,
    n_positions: int,
    payload_lists: list[list[str]],
    template: str,
) -> list[tuple[str, str]]:
    """Return list of (label, fully-substituted-text)."""
    if n_positions == 0:
        return []
    if mode == "sniper":
        payloads = payload_lists[0] if payload_lists else []
        out = []
        for pos in range(n_positions):
            for p in payloads:
                replacements = ["" for _ in range(n_positions)]
                replacements[pos] = p
                out.append((f"pos {pos+1}: {p}", _replace_markers(template, replacements)))
        return out
    if mode == "battering-ram":
        payloads = payload_lists[0] if payload_lists else []
        return [(p, _replace_markers(template, [p] * n_positions)) for p in payloads]
    if mode == "pitchfork":
        if len(payload_lists) < n_positions or any(not pl for pl in payload_lists[:n_positions]):
            return []
        return [
            (" | ".join(row), _replace_markers(template, list(row)))
            for row in zip(*payload_lists[:n_positions])
        ]
    if mode == "clusterbomb":
        if len(payload_lists) < n_positions or any(not pl for pl in payload_lists[:n_positions]):
            return []
        return [
            (" | ".join(row), _replace_markers(template, list(row)))
            for row in itertools.product(*payload_lists[:n_positions])
        ]
    return []


class _AttackWorker(QThread):
    result_ready = Signal(int, dict)  # index, row-dict
    all_done = Signal()

    def __init__(
        self,
        host: str,
        port: int,
        scheme: str,
        attempts: list[tuple[str, str]],
        match_pattern: str = "",
        match_regex: bool = False,
        concurrency: int = 4,
    ) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.scheme = scheme
        self.attempts = attempts
        self.match_pattern = match_pattern
        self.match_regex = match_regex
        # Clamp to avoid both pointless parallelism and abusive thread counts.
        # 1 = the old serial behaviour; sensible cap is the size of the attack.
        self.concurrency = max(1, min(concurrency, max(1, len(attempts))))
        self._abort = False

        # Compile once. Invalid regex → no rows ever match; the UI will surface
        # the syntax error next to the input so the user knows.
        self._match_re: Optional[re.Pattern] = None
        if match_pattern and match_regex:
            try:
                self._match_re = re.compile(match_pattern, re.IGNORECASE)
            except re.error:
                self._match_re = None
        self._match_needle = match_pattern.lower()

    def abort(self) -> None:
        self._abort = True

    def _scan_body(self, body: bytes, headers: dict) -> bool:
        """True if `body` (after Content-Encoding decode) contains the pattern."""
        if not self.match_pattern:
            return False
        decoded, _note = bodydecode.decode_body(body, headers or {})
        if self.match_regex:
            if self._match_re is None:
                return False
            text = decoded.decode("iso-8859-1", errors="replace")
            return self._match_re.search(text) is not None
        return self._match_needle.encode("utf-8", errors="ignore") in decoded.lower()

    def _do_one(self, label: str, text: str) -> Optional[dict]:
        """Build the raw request for one attempt, send it, and return the row."""
        if self._abort:
            return None
        # encode_http_text normalizes CRLF and falls back to UTF-8 for non-Latin-1
        # payloads — the old bare .encode("iso-8859-1") raised and killed the
        # worker thread the moment a payload contained a multibyte character.
        raw = encode_http_text(text)
        # Body length changes per attempt — recompute Content-Length so we
        # don't ship truncated requests for every payload but the first.
        raw = recompute_content_length(raw)
        r = send_raw(self.host, self.port, self.scheme, raw)
        matched = self._scan_body(r.get("body", b""), r.get("headers") or {})
        return {
            "payload": label,
            "status": r.get("status"),
            "length": len(r.get("body", b"")),
            "duration_ms": r.get("duration_ms", 0),
            "error": r.get("error"),
            "matched": matched,
        }

    def run(self) -> None:
        """Run the attack with up to `self.concurrency` requests in flight.

        Workers stream results back via ``result_ready.emit(index, row)``.
        Order is determined by which request finishes first — a 10s outlier
        no longer blocks the rest of the queue. The row index is preserved
        so the UI table updates the correct row regardless of finish order.
        """
        if not self.attempts:
            self.all_done.emit()
            return

        # Single-worker path keeps the cleaner stack trace + lower overhead
        # for the rare "concurrency: 1" case (e.g. labs that genuinely care
        # about request order).
        if self.concurrency <= 1:
            for i, (label, text) in enumerate(self.attempts):
                if self._abort:
                    break
                row = self._do_one(label, text)
                if row is not None:
                    self.result_ready.emit(i, row)
            self.all_done.emit()
            return

        pool = ThreadPoolExecutor(
            max_workers=self.concurrency,
            thread_name_prefix="intruder",
        )
        future_to_index: dict = {}
        try:
            for i, (label, text) in enumerate(self.attempts):
                fut = pool.submit(self._do_one, label, text)
                future_to_index[fut] = i

            for fut in as_completed(future_to_index):
                if self._abort:
                    # Cancel anything that hasn't started; in-flight requests
                    # will finish under us but their results get dropped.
                    for f in future_to_index:
                        f.cancel()
                    break
                try:
                    row = fut.result()
                except Exception as e:
                    row = {
                        "payload": "",
                        "status": None,
                        "length": 0,
                        "duration_ms": 0,
                        "error": str(e),
                        "matched": False,
                    }
                if row is not None:
                    self.result_ready.emit(future_to_index[fut], row)
        finally:
            # wait=False so a Stop click returns control promptly even if a
            # slow request is still mid-send. cancel_futures cleans the queue.
            pool.shutdown(wait=False, cancel_futures=True)
        self.all_done.emit()


class IntruderSession(QWidget):
    """A single Intruder attack configuration."""

    def __init__(
        self,
        scheme: str = "https",
        host: str = "",
        port: int = 443,
        raw: bytes = b"",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ---- top controls ----
        controls = QGridLayout()
        controls.addWidget(QLabel("Scheme:", self), 0, 0)
        self._scheme = MiniBurpComboBox(self)
        self._scheme.addItems(["http", "https"])
        self._scheme.setCurrentText(scheme)
        controls.addWidget(self._scheme, 0, 1)

        controls.addWidget(QLabel("Host:", self), 0, 2)
        self._host = QLineEdit(host, self)
        controls.addWidget(self._host, 0, 3)

        controls.addWidget(QLabel("Port:", self), 0, 4)
        self._port = QSpinBox(self)
        self._port.setRange(1, 65535)
        self._port.setValue(port)
        controls.addWidget(self._port, 0, 5)

        controls.addWidget(QLabel("Mode:", self), 0, 6)
        self._mode = MiniBurpComboBox(self)
        self._mode.addItem("Sniper (list 1, one marker at a time)", "sniper")
        self._mode.addItem("Battering ram (list 1, every marker)", "battering-ram")
        self._mode.addItem("Pitchfork (N lists, lockstep)", "pitchfork")
        self._mode.addItem("Cluster bomb (N lists, cartesian)", "clusterbomb")
        controls.addWidget(self._mode, 0, 7, 1, 2)

        # Threads spinbox stays in the top controls row — it's an attack-wide
        # setting, not a template manipulation. Position controls (Add §,
        # Clear §, positions count) live with the template down below.
        controls.addWidget(QLabel("Threads:", self), 1, 0)
        self._threads = QSpinBox(self)
        self._threads.setRange(1, 64)
        self._threads.setValue(20)
        self._threads.setToolTip(
            "Number of requests in flight at once.\n"
            "Default 20 matches Burp Pro. Lower to 1 for ordered attacks,\n"
            "or when the target starts returning 429 / 503."
        )
        controls.addWidget(self._threads, 1, 1)

        self._start_btn = QPushButton("Start attack", self)
        self._start_btn.setProperty("role", "primary")
        self._start_btn.setToolTip("Start the attack  (⌘↵ / Ctrl+Enter)")
        self._stop_btn = QPushButton("Stop", self)
        self._stop_btn.setEnabled(False)
        controls.addWidget(self._start_btn, 1, 7)
        controls.addWidget(self._stop_btn, 1, 8)

        # ---- template + payloads + results splitter ----
        # No title on the editor — the position-controls row above it makes
        # the purpose obvious.
        self._template = HttpEditor("", parent=self)
        self._template.set_bytes(raw)
        self._template.edit.textChanged.connect(self._rebuild_payload_panels)

        # Position controls live directly above the template, grouped in a
        # container so they ride the splitter with the editor.
        self._pos_label = QLabel("positions: 0", self)
        self._pos_label.setProperty("role", "muted")
        self._add_pos_btn = QPushButton("Add §", self)
        self._add_pos_btn.setToolTip(
            "Wrap the selected text in the template with § markers.\n"
            "If nothing is selected, inserts an empty §§ at the cursor.\n"
            "Shortcut: ⌘I"
        )
        self._clear_pos_btn = QPushButton("Clear §", self)
        self._clear_pos_btn.setToolTip("Remove every § marker from the template")

        pos_row = QHBoxLayout()
        pos_row.setContentsMargins(0, 0, 0, 0)
        pos_row.setSpacing(6)
        pos_row.addWidget(self._add_pos_btn)
        pos_row.addWidget(self._clear_pos_btn)
        pos_row.addSpacing(8)
        pos_row.addWidget(self._pos_label)
        pos_row.addStretch(1)

        template_wrap = QWidget(self)
        template_wrap_layout = QVBoxLayout(template_wrap)
        template_wrap_layout.setContentsMargins(0, 0, 0, 0)
        template_wrap_layout.setSpacing(4)
        template_wrap_layout.addLayout(pos_row)
        template_wrap_layout.addWidget(self._template, 1)

        self._payloads_wrap = QWidget(self)
        self._payloads_layout = QVBoxLayout(self._payloads_wrap)
        self._payloads_layout.setContentsMargins(0, 0, 0, 0)
        self._payloads_layout.setSpacing(6)
        self._payload_edits: list[QPlainTextEdit] = []

        left = QSplitter(Qt.Vertical, self)
        left.addWidget(template_wrap)
        left.addWidget(self._payloads_wrap)
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 2)

        # ---- match row + results, stacked on the right side ----
        match_row = QHBoxLayout()
        match_row.setSpacing(6)
        match_row.addWidget(QLabel("Match:", self))
        self._match_input = QLineEdit(self)
        self._match_input.setPlaceholderText(
            "Flag responses whose body contains this string (e.g. 'do not match')"
        )
        self._match_input.setClearButtonEnabled(True)
        self._match_regex = QCheckBox("Regex", self)
        self._match_status = QLabel("", self)
        self._match_status.setProperty("role", "muted")
        match_row.addWidget(self._match_input, 1)
        match_row.addWidget(self._match_regex)
        match_row.addWidget(self._match_status)

        self._results = QTableWidget(0, 6, self)
        self._results.setHorizontalHeaderLabels(["#", "Payload", "Status", "Length", "Time", "Match"])
        self._results.verticalHeader().setVisible(False)
        # Click a header to sort; click again to flip direction. _NumericItem
        # keeps numeric columns ordering correctly. Updates in _on_result
        # toggle sorting off then back on so streaming writes don't shuffle
        # rows mid-attack.
        self._results.setSortingEnabled(True)
        # Payload is the variable-width column; the rest get widths that fit
        # both the data and the new uppercase-spaced headers.
        header = self._results.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(1, QHeaderView.Stretch)  # Payload
        self._results.setColumnWidth(0, 50)   # #
        # col 1 (Payload) is stretched
        self._results.setColumnWidth(2, 85)   # Status
        self._results.setColumnWidth(3, 95)   # Length
        self._results.setColumnWidth(4, 100)  # Time
        self._results.setColumnWidth(5, 75)   # Match

        right_wrap = QWidget(self)
        right_layout = QVBoxLayout(right_wrap)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        right_layout.addLayout(match_row)
        right_layout.addWidget(self._results, 1)

        split = QSplitter(Qt.Horizontal, self)
        split.addWidget(left)
        split.addWidget(right_wrap)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)

        root.addLayout(controls)
        root.addWidget(split, 1)

        # initial payload panels based on current marker count
        self._rebuild_payload_panels()

        # wire actions
        self._start_btn.clicked.connect(self._start)
        self._stop_btn.clicked.connect(self._stop)
        self._mode.currentIndexChanged.connect(self._rebuild_payload_panels)
        self._match_input.textChanged.connect(self._update_match_status)
        self._match_regex.toggled.connect(self._update_match_status)
        self._add_pos_btn.clicked.connect(self._add_position)
        self._clear_pos_btn.clicked.connect(self._clear_positions)

        # Cmd+I / Ctrl+I to add a position — matches Burp's muscle memory.
        # Scoped to the template editor so it doesn't fire anywhere else.
        sc = QShortcut(QKeySequence("Ctrl+I"), self._template.edit)
        sc.setContext(Qt.WidgetWithChildrenShortcut)
        sc.activated.connect(self._add_position)

        # Cmd/Ctrl+Return (+ numpad Enter) starts the attack from anywhere in
        # the session, mirroring Repeater's Send shortcut.
        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            sc_start = QShortcut(QKeySequence(seq), self)
            sc_start.setContext(Qt.WidgetWithChildrenShortcut)
            sc_start.activated.connect(self._start)

        self._worker: Optional[_AttackWorker] = None

    # ---- position markers ----------------------------------------------
    def _add_position(self) -> None:
        """Wrap the current selection in §…§; if nothing is selected, insert
        an empty §§ at the cursor and park it between the markers."""
        editor = self._template.edit
        cursor = editor.textCursor()
        # Qt returns U+2029 for newlines inside selectedText(); normalize so a
        # multi-line selection doesn't smuggle a paragraph separator into the
        # template. (Single-line is the common case for payload positions.)
        selected = cursor.selectedText().replace("\u2029", "\n")
        if selected:
            cursor.insertText(f"§{selected}§")
        else:
            cursor.insertText("§§")
            cursor.movePosition(cursor.MoveOperation.Left, cursor.MoveMode.MoveAnchor, 1)
            editor.setTextCursor(cursor)
        editor.setFocus()

    def _clear_positions(self) -> None:
        """Strip every § from the template in one shot."""
        text = self._template.text_value()
        if "§" not in text:
            return
        cleaned = text.replace("§", "")
        # set_bytes routes through the editor's _render → setPlainText, which
        # fires textChanged so _rebuild_payload_panels picks the change up.
        self._template.set_bytes(encode_http_text(cleaned))

    def _update_match_status(self) -> None:
        text = self._match_input.text()
        if not text or not self._match_regex.isChecked():
            self._match_status.setText("")
            return
        try:
            re.compile(text)
            self._match_status.setText("")
        except re.error as e:
            self._match_status.setText(f"bad regex: {e}")

    # ---- payload panel reconcile ---------------------------------------
    def _rebuild_payload_panels(self) -> None:
        template_text = self._template.text_value()
        n = len(MARK_RE.findall(template_text))
        self._pos_label.setText(f"positions: {n}")

        # Sniper / battering-ram → always exactly 1 payload panel
        mode = self._mode.currentData()
        wanted = 1 if mode in ("sniper", "battering-ram") else max(1, n)

        # preserve existing payload text where we can
        existing_texts = [w.toPlainText() for w in self._payload_edits]

        # wipe layout
        while self._payloads_layout.count():
            item = self._payloads_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()
        self._payload_edits.clear()

        for i in range(wanted):
            wrap = QWidget(self)
            v = QVBoxLayout(wrap)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)

            header_row = QHBoxLayout()
            header_row.setContentsMargins(0, 0, 0, 0)
            label = QLabel(f"List {i+1}", self)
            label.setProperty("role", "section")
            loader = MiniBurpComboBox(self)
            loader.addItem("Load list…", None)
            for key, name in payloads_module.names():
                loader.addItem(name, key)
                pl = payloads_module.get(key)
                if pl is not None:
                    loader.setItemData(loader.count() - 1, pl.description, Qt.ToolTipRole)
            header_row.addWidget(label)
            header_row.addStretch(1)
            header_row.addWidget(loader)

            edit = QPlainTextEdit(self)
            edit.setPlaceholderText("One payload per line")
            if i < len(existing_texts):
                edit.setPlainText(existing_texts[i])

            def _load(_idx, e=edit, c=loader):
                key = c.currentData()
                if key:
                    e.setPlainText("\n".join(payloads_module.payloads_for(key)))
                    c.setCurrentIndex(0)  # reset picker to the "Load list…" prompt
            loader.currentIndexChanged.connect(_load)

            v.addLayout(header_row)
            v.addWidget(edit)
            self._payloads_layout.addWidget(wrap, 1)
            self._payload_edits.append(edit)

    # ---- attack lifecycle ----------------------------------------------
    def _start(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        template_text = self._template.text_value()
        n_positions = len(MARK_RE.findall(template_text))
        if n_positions == 0:
            self._set_results_error("mark at least one position with §…§")
            return

        mode = self._mode.currentData()
        lists = [
            [p for p in w.toPlainText().splitlines() if p.strip() != ""]
            for w in self._payload_edits
        ]
        # pad to n_positions so pitchfork/clusterbomb don't IndexError
        while len(lists) < n_positions:
            lists.append([])

        attempts = _build_attempts(mode, n_positions, lists, template_text)
        if not attempts:
            self._set_results_error(
                "supply payloads — Pitchfork/Cluster bomb need one non-empty list per position."
            )
            return

        # Pre-populate rows with blanks so results can stream in by index.
        # Sorting must be off while we write by row index — toggle it back on
        # afterwards so the user can click headers to sort live results.
        self._results.setSortingEnabled(False)
        self._results.setRowCount(0)
        self._results.setRowCount(len(attempts))
        for i, (label, _text) in enumerate(attempts):
            self._results.setItem(i, 0, _NumericItem(i + 1))
            self._results.setItem(i, 1, QTableWidgetItem(label))
            # Pending rows carry sort_value=None so they sort to the end
            # regardless of direction — keeps "…" placeholders out of the way.
            self._results.setItem(i, 2, _NumericItem(None, "…"))
            self._results.setItem(i, 3, _NumericItem(None, ""))
            self._results.setItem(i, 4, _NumericItem(None, ""))
            self._results.setItem(i, 5, _NumericItem(None, ""))
        self._results.setSortingEnabled(True)

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        worker = _AttackWorker(
            self._host.text().strip(),
            int(self._port.value()),
            self._scheme.currentText(),
            attempts,
            match_pattern=self._match_input.text(),
            match_regex=self._match_regex.isChecked(),
            concurrency=int(self._threads.value()),
        )
        worker.result_ready.connect(self._on_result)
        worker.all_done.connect(self._on_done)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.abort()

    def _find_row(self, attempt_index: int) -> int:
        """Map an attempt index (#) to its current visual row in the table.

        The user may have sorted the table while the attack runs, so the
        original `i` is no longer the row position. We look up the row whose
        # cell holds (attempt_index + 1).
        """
        target = attempt_index + 1
        for r in range(self._results.rowCount()):
            item = self._results.item(r, 0)
            if isinstance(item, _NumericItem) and item._sort_value == target:
                return r
        return attempt_index  # fallback to insertion order

    def _on_result(self, i: int, row: dict) -> None:
        # Disable sorting while we write or live re-sort will shuffle the
        # row we're targeting mid-update. Re-enable at the end so the user's
        # chosen sort takes effect on the new values immediately.
        self._results.setSortingEnabled(False)
        r = self._find_row(i)

        status = row.get("status")
        status_item = _NumericItem(
            status if isinstance(status, int) else None,
            str(status) if status is not None else "-",
        )
        color = "#9a9aa8"
        s = status or 0
        if 200 <= s < 300:
            color = "#5ecb7b"
        elif 300 <= s < 400:
            color = "#7aa2ff"
        elif 400 <= s < 500:
            color = "#e7b648"
        elif s >= 500:
            color = "#ef6767"
        status_item.setForeground(QBrush(QColor(color)))
        self._results.setItem(r, 2, status_item)

        length = int(row.get("length") or 0)
        self._results.setItem(r, 3, _NumericItem(length))
        dur = int(row.get("duration_ms") or 0)
        self._results.setItem(r, 4, _NumericItem(dur, f"{dur} ms"))

        matched = bool(row.get("matched"))
        # Match column sort value: 1 = matched, 0 = not. Click the header to
        # bring matches to the top (descending) or bottom (ascending).
        match_item = _NumericItem(1 if matched else 0, "✓" if matched else "")
        match_item.setTextAlignment(Qt.AlignCenter)
        if matched:
            # Use the accent-orange so the winning row jumps out against both
            # themes. Foreground only — Qt selection highlighting still works.
            accent = QBrush(QColor("#ff7030"))
            match_item.setForeground(accent)
            for col in range(self._results.columnCount()):
                cell = self._results.item(r, col)
                if cell is not None and col != 2:
                    # Leave the status-code color alone; tint everything else.
                    cell.setForeground(accent)
        self._results.setItem(r, 5, match_item)

        err = row.get("error")
        if err:
            self._results.item(r, 2).setText("ERR")
            self._results.item(r, 1).setToolTip(str(err))

        self._results.setSortingEnabled(True)

    def _on_done(self) -> None:
        # Drop the reference before the QThread's scheduled deleteLater fires;
        # otherwise the next Start click sees a deleted C++ object.
        self._worker = None
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    def _set_results_error(self, msg: str) -> None:
        self._results.setRowCount(1)
        self._results.setItem(0, 0, QTableWidgetItem("-"))
        self._results.setItem(0, 1, QTableWidgetItem(msg))
        self._results.setItem(0, 2, QTableWidgetItem("-"))
        self._results.setItem(0, 3, QTableWidgetItem("-"))
        self._results.setItem(0, 4, QTableWidgetItem("-"))
        self._results.setItem(0, 5, QTableWidgetItem("-"))


class IntruderTab(QWidget):
    """Container for multiple Intruder sessions as sub-tabs."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        top = QHBoxLayout()
        add_btn = QPushButton("+ New session", self)
        top.addWidget(add_btn)
        top.addStretch(1)

        self._tabs = QTabWidget(self)
        self._tabs.setTabsClosable(True)
        # Same recipe as Repeater — flat, left-aligned session strip,
        # plus the macOS WA_StyledBackground attr so the QTabBar's painter
        # honors chrome_bg from QSS instead of falling through to window bg.
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().setExpanding(False)
        self._tabs.tabBar().setAttribute(Qt.WA_StyledBackground, True)
        self._tabs.tabCloseRequested.connect(self._close)

        root.addWidget(HintStrip(
            "Wrap payload positions in <b>§…§</b> (⌘I), pick an attack mode, "
            "use <b>Load list…</b> for a built-in payload set, then "
            "<b>Start</b> (⌘↵). Click a result column header to sort."
        ))
        root.addLayout(top)
        root.addWidget(self._tabs, 1)

        add_btn.clicked.connect(self.new_session_blank)
        self._counter = 0
        self.new_session_blank()

    def _close(self, i: int) -> None:
        if self._tabs.count() <= 1:
            return
        w = self._tabs.widget(i)
        self._tabs.removeTab(i)
        if w is not None:
            w.deleteLater()

    def new_session_blank(self) -> IntruderSession:
        return self._add(IntruderSession(parent=self))

    def new_session_from_tx(self, tx_id: int) -> Optional[IntruderSession]:
        tx = STORE.get(tx_id)
        if not tx:
            return None
        return self.new_session_from_bytes(
            tx.scheme, tx.host, tx.port, tx.raw_request, label=f"#{tx.id} {tx.host}"
        )

    def new_session_from_bytes(
        self,
        scheme: str,
        host: str,
        port: int,
        raw: bytes,
        label: Optional[str] = None,
    ) -> IntruderSession:
        """Seed a session from explicit bytes — used by 'Send to Intruder' from
        the Intercept tab so the user's edited held bytes carry over."""
        session = IntruderSession(
            scheme=scheme, host=host, port=port, raw=bytes(raw), parent=self
        )
        return self._add(session, label=label)

    def _add(self, session: IntruderSession, label: Optional[str] = None) -> IntruderSession:
        self._counter += 1
        title = label or f"Session {self._counter}"
        i = self._tabs.addTab(session, title)
        # Same plain-× close button as Repeater; see that comment for why.
        close_btn = QPushButton("×", self)
        close_btn.setProperty("role", "tabClose")
        close_btn.setFlat(True)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFocusPolicy(Qt.NoFocus)
        close_btn.setFixedSize(18, 18)
        close_btn.clicked.connect(lambda _checked=False, w=session: self._close_widget(w))
        self._tabs.tabBar().setTabButton(i, QTabBar.RightSide, close_btn)
        self._tabs.setCurrentIndex(i)
        return session

    def _close_widget(self, w: QWidget) -> None:
        index = self._tabs.indexOf(w)
        if index >= 0:
            self._close(index)
