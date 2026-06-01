"""
Match & Replace tab — standing request transforms (Burp's Proxy > Match/Replace).

Add rules with a target segment (request line / headers / body), a match (literal
or regex), and a replacement. Enabled rules apply to every in-scope request
before it's forwarded. The table is the source of truth; every edit pushes the
full list into the shared RULES engine.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...rules import RULES, TARGETS, Rule
from ..widgets.combobox import MiniBurpComboBox

_TARGET_LABELS = dict(TARGETS)
_COLUMNS = ["On", "Target", "Match", "Replace", "Regex"]


class RulesTab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rules: list[Rule] = list(RULES.rules())
        self._refreshing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        help_text = QLabel(
            "Standing find/replace applied to every <b>in-scope</b> request "
            "before it's forwarded — inject a header (<code>X-Forwarded-For: "
            "127.0.0.1</code>), rewrite a cookie, or swap a path segment without "
            "editing each request by hand. Content-Length is recomputed when a "
            "body rule changes the body length.",
            self,
        )
        help_text.setTextFormat(Qt.TextFormat.RichText)
        help_text.setWordWrap(True)
        help_text.setProperty("role", "muted")

        # ---- add-rule form ----
        form = QHBoxLayout()
        form.setSpacing(6)
        self._target = MiniBurpComboBox(self)
        for key, label in TARGETS:
            self._target.addItem(label, key)
        self._match = QLineEdit(self)
        self._match.setPlaceholderText("Match (literal, or regex if checked)")
        self._replace = QLineEdit(self)
        self._replace.setPlaceholderText("Replace with…")
        self._regex = QCheckBox("Regex", self)
        self._add_btn = QPushButton("Add rule", self)
        self._add_btn.setProperty("role", "primary")
        form.addWidget(self._target)
        form.addWidget(self._match, 1)
        form.addWidget(self._replace, 1)
        form.addWidget(self._regex)
        form.addWidget(self._add_btn)

        # ---- table ----
        self._table = QTableWidget(0, len(_COLUMNS), self)
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(2, QHeaderView.Stretch)  # Match
        header.setSectionResizeMode(3, QHeaderView.Stretch)  # Replace
        self._table.setColumnWidth(0, 50)
        self._table.setColumnWidth(1, 130)
        self._table.setColumnWidth(4, 70)

        # ---- bottom buttons ----
        btn_row = QHBoxLayout()
        self._remove_btn = QPushButton("Remove selected", self)
        self._clear_btn = QPushButton("Clear all", self)
        self._clear_btn.setProperty("role", "danger")
        btn_row.addWidget(self._remove_btn)
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch(1)

        root.addWidget(help_text)
        root.addLayout(form)
        root.addWidget(self._table, 1)
        root.addLayout(btn_row)

        self._add_btn.clicked.connect(self._add_rule)
        self._match.returnPressed.connect(self._add_rule)
        self._replace.returnPressed.connect(self._add_rule)
        self._remove_btn.clicked.connect(self._remove_selected)
        self._clear_btn.clicked.connect(self._clear_all)
        self._table.itemChanged.connect(self._on_item_changed)

        self._refresh_table()

    # ---- mutation ------------------------------------------------------
    def _add_rule(self) -> None:
        match = self._match.text()
        if not match:
            return
        self._rules.append(Rule(
            target=self._target.currentData(),
            match=match,
            replacement=self._replace.text(),
            is_regex=self._regex.isChecked(),
            enabled=True,
        ))
        self._match.clear()
        self._replace.clear()
        self._push()
        self._refresh_table()

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True)
        for r in rows:
            if 0 <= r < len(self._rules):
                del self._rules[r]
        self._push()
        self._refresh_table()

    def _clear_all(self) -> None:
        self._rules.clear()
        self._push()
        self._refresh_table()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._refreshing or item.column() != 0:
            return
        row = item.row()
        if 0 <= row < len(self._rules):
            self._rules[row].enabled = item.checkState() == Qt.Checked
            self._push()

    # ---- render --------------------------------------------------------
    def _push(self) -> None:
        RULES.set_rules(self._rules)

    def _refresh_table(self) -> None:
        self._refreshing = True
        self._table.setRowCount(0)
        self._table.setRowCount(len(self._rules))
        for r, rule in enumerate(self._rules):
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            on.setCheckState(Qt.Checked if rule.enabled else Qt.Unchecked)
            self._table.setItem(r, 0, on)
            self._table.setItem(r, 1, QTableWidgetItem(_TARGET_LABELS.get(rule.target, rule.target)))
            self._table.setItem(r, 2, QTableWidgetItem(rule.match))
            self._table.setItem(r, 3, QTableWidgetItem(rule.replacement))
            self._table.setItem(r, 4, QTableWidgetItem("regex" if rule.is_regex else ""))
        self._refreshing = False
