"""
MiniBurp desktop main window.

One QMainWindow with a QTabWidget (Proxy / Intercept / Repeater / Intruder /
Decoder / Scope) plus a toolbar that owns global actions: Launch tracked
browser, Intercept toggle, Clear history, Save CA, and Theme toggle.

Themes are driven by two QSS strings (THEMES dict). Widgets that have a
semantic color (muted text, ok/warn/err indicators, section labels) carry
a dynamic ``role`` property and are styled by attribute selectors in the
QSS, e.g. ``QLabel[role="muted"]``. That means the whole UI recolors on
theme change without per-widget plumbing.
"""
from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings, QSize, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QPalette
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QWidget,
)

from .. import browser as browser_module
from .. import ca as ca_module
from ..intercept import INTERCEPT
from ..storage import STORE
from .signals import StoreBridge
from .tabs.decoder_tab import DecoderTab
from .tabs.intercept_tab import InterceptTab
from .tabs.intruder_tab import IntruderTab
from .tabs.proxy_tab import ProxyTab
from .tabs.repeater_tab import RepeaterTab
from .tabs.scope_tab import ScopeTab


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------
#
# Both themes share the orange accent so MiniBurp stays recognisable. Anywhere
# a widget needs a semantic color, set ``widget.setProperty("role", "<name>")``
# and add a matching rule below.

_QSS_TEMPLATE = """
/* ---- base typography & surface --------------------------------------- */
QMainWindow, QWidget {{
    background: {bg};
    color: {fg};
    font-family: -apple-system, "SF Pro Text", "Helvetica Neue", "Segoe UI", sans-serif;
    font-size: 12px;
}}

/* ---- tabs -------------------------------------------------------------
 * Flat strip, accent underline on the active tab. macOS's QTabBar native
 * painter ignores `QTabBar` background set from QSS, so we set it on the
 * tabs themselves and let the selected one switch to the content bg —
 * that's what makes the selected tab visually "connect" to the pane below.
 */
QTabWidget::pane {{
    border: 0;
    /* Pane bg is chrome_bg, not the window bg, so the strip right of the
     * last tab paints the same color as the bar instead of leaking the
     * content bg (which renders as a white line in light mode, black in
     * dark mode). The actual tab content widgets are QWidget instances
     * carrying `background: bg` from the global rule above, so the visible
     * content area is still bg — only the otherwise-empty sliver matches
     * the chrome strip.
     */
    background: {chrome_bg};
}}
/* The empty strip to the right of the last tab falls through to the
 * QTabWidget's own bg. Without this rule that strip leaks the window bg
 * instead of the chrome bg — visible as an unstyled patch in light mode.
 * Applies to nested QTabWidgets too (Repeater / Intruder session bars)
 * which have exactly the same painter quirk on macOS. */
QTabWidget {{ background: {chrome_bg}; border: 0; }}
QTabBar {{ background: {chrome_bg}; }}
QTabBar::tab {{
    background: {chrome_bg};
    color: {fg_muted};
    padding: 9px 20px;
    border: 0;
    border-bottom: 2px solid transparent;
    font-weight: 500;
    margin: 0;
}}
QTabBar::tab:hover {{
    color: {fg};
    background: {chrome_bg};
}}
QTabBar::tab:selected {{
    color: {fg};
    background: {bg};
    border-bottom: 2px solid {accent};
}}

/* ---- buttons: three-tier hierarchy ----------------------------------
 * Default is a ghost button. role="primary" gets the accent fill; reserve
 * for Send / Start attack / Forward. role="danger" stays restrained — a
 * tinted bg with red text — and saturates only on hover so destructive
 * intent is clearly signalled at click time.
 */
QPushButton {{
    background: {surface};
    color: {fg};
    border: 1px solid {border};
    padding: 6px 14px;
    border-radius: 5px;
    font-weight: 500;
}}
QPushButton:hover {{
    background: {chrome_bg};
    border-color: {border_strong};
}}
QPushButton:pressed {{
    background: {border};
}}
QPushButton:focus {{
    border-color: {accent};
}}
QPushButton:disabled {{
    color: {fg_subtle};
    background: {surface};
    border-color: {border};
}}

QPushButton[role="primary"] {{
    background: {accent};
    color: {accent_fg};
    border: 1px solid {accent};
    font-weight: 600;
}}
QPushButton[role="primary"]:hover {{
    background: {accent_hover};
    border-color: {accent_hover};
}}
QPushButton[role="primary"]:pressed {{
    background: {accent_pressed};
    border-color: {accent_pressed};
}}
QPushButton[role="primary"]:disabled {{
    background: {accent_disabled};
    border-color: {accent_disabled};
    color: {accent_fg};
}}

QPushButton[role="danger"] {{
    background: {err_subtle};
    color: {err};
    border: 1px solid {err_border};
}}
QPushButton[role="danger"]:hover {{
    background: {err};
    color: #ffffff;
    border-color: {err};
}}

/* ---- checkable buttons (Repeater Follow redirects, etc.) ------------- */
QPushButton:checked {{
    background: {accent_subtle};
    color: {accent};
    border-color: {accent_border};
}}

/* ---- session-tab close button: plain × that matches the tab text ----
 * Used by Repeater/Intruder session tabs in place of the default macOS
 * close glyph. Borderless on the chrome strip, shifts from fg_muted →
 * fg on hover so the affordance is obvious without being shouty.
 */
QPushButton[role="tabClose"] {{
    background: transparent;
    color: {fg_muted};
    border: 0;
    padding: 0;
    margin: 0 4px 0 2px;
    font-size: 16px;
    font-weight: 400;
    qproperty-flat: true;
}}
QPushButton[role="tabClose"]:hover {{
    color: {fg};
    background: transparent;
    border: 0;
}}
QPushButton[role="tabClose"]:pressed {{
    color: {fg};
    background: transparent;
}}
QPushButton[role="tabClose"]:focus {{
    border: 0;
}}

/* ---- toolbar: chrome-strip styling, no slab buttons ------------------ */
QToolBar {{
    background: {chrome_bg};
    border-bottom: 1px solid {border};
    padding: 6px 10px;
    spacing: 4px;
}}
QToolBar QToolButton {{
    background: transparent;
    color: {fg};
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 5px 11px;
    font-weight: 500;
}}
QToolBar QToolButton:hover {{
    background: {surface};
    border-color: {border};
}}
QToolBar QToolButton:pressed {{
    background: {border};
}}
QToolBar QToolButton:checked {{
    background: {accent_subtle};
    color: {accent};
    border-color: {accent_border};
}}
QToolBar::separator {{
    background: {border};
    width: 1px;
    margin: 6px 6px;
}}

/* ---- inputs ---------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {surface};
    color: {fg};
    border: 1px solid {border};
    border-radius: 5px;
    padding: 5px 10px;
    selection-background-color: {accent};
    selection-color: {accent_fg};
}}
QLineEdit {{ placeholder-text-color: {fg_subtle}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {accent};
}}
QPlainTextEdit, QTextEdit {{ padding: 8px; }}

QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {surface};
    color: {fg};
    border: 1px solid {border_strong};
    border-radius: 5px;
    selection-background-color: {accent_subtle};
    selection-color: {accent};
    outline: 0;
    padding: 4px;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 12px;
    min-height: 22px;
    border-radius: 3px;
    color: {fg};
}}
QComboBox QAbstractItemView::item:hover {{
    background: {chrome_bg};
}}
QComboBox QAbstractItemView::item:selected {{
    background: {accent_subtle};
    color: {accent};
}}

QSpinBox::up-button, QSpinBox::down-button {{
    width: 16px;
    border: 0;
    background: transparent;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {chrome_bg};
}}

/* ---- tables & lists -------------------------------------------------- */
QTableView, QTableWidget, QListWidget {{
    background: {bg};
    color: {fg};
    alternate-background-color: {row_alt};
    gridline-color: {border};
    border: 1px solid {border};
    border-radius: 5px;
    selection-background-color: {accent_subtle};
    selection-color: {accent};
}}
QTableView::item, QTableWidget::item, QListWidget::item {{
    padding: 4px 6px;
}}
QHeaderView::section {{
    background: {chrome_bg};
    color: {fg_muted};
    padding: 8px 12px;
    border: 0;
    border-right: 1px solid {border};
    border-bottom: 1px solid {border};
    font-weight: 500;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1.2px;
}}
/* Drop the rightmost divider so it doesn't double up with the table's
 * own right border. The visible per-column divider is the grab cue
 * for resizing — every header in MiniBurp uses Interactive mode. */
QHeaderView::section:last {{
    border-right: 0;
}}

/* ---- status bar ------------------------------------------------------ */
QStatusBar {{
    background: {chrome_bg};
    border-top: 1px solid {border};
    color: {fg_muted};
    padding: 4px 12px;
}}
QStatusBar::item {{ border: 0; }}

/* ---- splitters: thin lines, accent on hover for grab affordance ----- */
QSplitter::handle {{ background: {border}; }}
QSplitter::handle:horizontal {{ width: 1px; margin: 0 3px; }}
QSplitter::handle:vertical   {{ height: 1px; margin: 3px 0; }}
QSplitter::handle:hover {{ background: {accent}; }}

/* ---- checkboxes ------------------------------------------------------ */
QCheckBox {{ color: {fg}; spacing: 6px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {border_strong};
    border-radius: 3px;
    background: {surface};
}}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent};
}}

/* ---- labels & semantic roles ---------------------------------------- */
QLabel {{ color: {fg}; }}
QLabel[role="muted"]   {{ color: {fg_muted}; }}
QLabel[role="ok"]      {{ color: {ok}; }}
QLabel[role="warn"]    {{ color: {warn}; }}
QLabel[role="err"]     {{ color: {err}; }}
QLabel[role="section"] {{
    color: {fg_muted};
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 1.2px;
    text-transform: uppercase;
}}
QLabel[role="title"]   {{ font-weight: 600; font-size: 13px; }}

/* ---- status pills in the toolbar ----------------------------------- */
QLabel#proxyIndicator {{
    color: {ok};
    background: {ok_subtle};
    border: 1px solid {ok_border};
    border-radius: 10px;
    padding: 3px 12px;
    font-size: 11px;
    font-weight: 500;
}}
QLabel#chromeIndicator {{
    border-radius: 10px;
    padding: 3px 12px;
    font-size: 11px;
    font-weight: 500;
    border: 1px solid {border};
    background: {surface};
}}
QLabel#chromeIndicator[state="off"] {{
    color: {fg_muted};
    background: {surface};
    border-color: {border};
}}
QLabel#chromeIndicator[state="on"] {{
    color: {ok};
    background: {ok_subtle};
    border-color: {ok_border};
}}
QLabel#chromeIndicator[state="exited"] {{
    color: {warn};
    background: {warn_subtle};
    border-color: {warn_border};
}}

/* ---- scrollbars: thin, transparent track, accent-grab on hover ----- */
QScrollBar:vertical, QScrollBar:horizontal {{
    background: transparent;
    border: 0;
}}
QScrollBar:vertical {{ width: 10px; }}
QScrollBar:horizontal {{ height: 10px; }}
QScrollBar::handle {{
    background: {border_strong};
    border-radius: 4px;
    margin: 2px;
    min-height: 30px;
    min-width: 30px;
}}
QScrollBar::handle:hover {{ background: {scroll_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{
    background: none; border: 0; width: 0; height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- tooltips ------------------------------------------------------- */
QToolTip {{
    background: {chrome_bg};
    color: {fg};
    border: 1px solid {border_strong};
    padding: 5px 9px;
    border-radius: 4px;
}}

/* ---- group boxes / message boxes ----------------------------------- */
QMessageBox {{ background: {surface}; }}
"""

# Dark palette: deep, slightly warm (away from pure-blue cast). The orange
# accent reads cleaner on a warm bg than on the previous cool-tinted gray.
_DARK = {
    "bg":               "#16161a",
    "surface":          "#1c1c20",
    "chrome_bg":        "#1f1f24",
    "row_alt":          "#1a1a1e",
    "border":           "#2a2a30",
    "border_strong":    "#3a3a44",
    "fg":               "#ebebed",
    "fg_muted":         "#8b8b93",
    "fg_subtle":        "#5a5a62",
    "accent":           "#ff7d3d",
    "accent_hover":     "#ff9258",
    "accent_pressed":   "#e86a2e",
    "accent_fg":        "#1a1208",
    "accent_subtle":    "#2a1d12",
    "accent_border":    "#4a2f1c",
    "accent_disabled":  "#4a3527",
    "ok":               "#5dcc7c",
    "ok_subtle":        "#1b2a1f",
    "ok_border":        "#2e4a37",
    "warn":             "#e8b94a",
    "warn_subtle":      "#2a2114",
    "warn_border":      "#4a3b1f",
    "err":              "#ef6868",
    "err_subtle":       "#2a1818",
    "err_border":       "#4a2828",
    "scroll_hover":     "#5a5a64",
}

# Light palette: warm off-white instead of bluish white. Less contrast-y
# than pure black-on-white; the slight warmth pairs with the deepened
# orange so neither feels stranded.
_LIGHT = {
    "bg":               "#fbfbf7",
    "surface":          "#ffffff",
    "chrome_bg":        "#f1f0eb",
    "row_alt":          "#f6f5ef",
    "border":           "#e4e2dc",
    "border_strong":    "#ccc9c0",
    "fg":               "#1a1a1d",
    "fg_muted":         "#6a6a72",
    "fg_subtle":        "#9c9ca4",
    "accent":           "#e85a1f",
    "accent_hover":     "#cd4d15",
    "accent_pressed":   "#b04210",
    "accent_fg":        "#ffffff",
    "accent_subtle":    "#fff0e6",
    "accent_border":    "#f5cab0",
    "accent_disabled":  "#f0b89a",
    "ok":               "#1c9c4d",
    "ok_subtle":        "#e9f6ed",
    "ok_border":        "#bbe0c5",
    "warn":             "#9a6b14",
    "warn_subtle":      "#fbf2db",
    "warn_border":      "#e6d4a0",
    "err":              "#c93030",
    "err_subtle":       "#fbe9e9",
    "err_border":       "#e8c0c0",
    "scroll_hover":     "#a8a8b0",
}

THEMES = {
    "dark":  _QSS_TEMPLATE.format(**_DARK),
    "light": _QSS_TEMPLATE.format(**_LIGHT),
}
DEFAULT_THEME = "dark"


def _repolish(widget: QWidget) -> None:
    """Force Qt to re-evaluate property-based QSS selectors after a change."""
    style = widget.style()
    if style is None:
        return
    style.unpolish(widget)
    style.polish(widget)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(
        self,
        proxy_host: str,
        proxy_port: int,
        start_url: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("MiniBurp")
        self.resize(1400, 900)

        self._proxy_host = proxy_host
        self._proxy_port = proxy_port
        self._start_url = start_url
        self._chrome_proc: Optional[subprocess.Popen] = None

        # Persisted UI prefs (theme).
        self._settings = QSettings("MiniBurp", "MiniBurp")
        self._theme: str = self._settings.value("theme", DEFAULT_THEME, type=str)
        if self._theme not in THEMES:
            self._theme = DEFAULT_THEME

        self._bridge = StoreBridge(self)

        # ---- toolbar ----
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)

        self._launch_action = QAction("Launch tracked browser", self)
        self._intercept_action = QAction("Intercept: OFF", self)
        self._intercept_action.setCheckable(True)
        self._clear_action = QAction("Clear history", self)
        self._save_session_action = QAction("Save session…", self)
        self._load_session_action = QAction("Load session…", self)
        self._save_ca_action = QAction("Save CA…", self)
        self._theme_action = QAction(self._theme_action_label(), self)
        # Qt auto-derives a tooltip from QAction.text() and shows it on
        # hover in the toolbar. With the text already visible on the button
        # the tooltip just duplicates it — kill them.
        for act in (self._launch_action, self._intercept_action,
                    self._clear_action, self._save_session_action,
                    self._load_session_action, self._save_ca_action,
                    self._theme_action):
            act.setToolTip("")

        tb.addAction(self._launch_action)
        tb.addAction(self._intercept_action)
        tb.addSeparator()
        tb.addAction(self._clear_action)
        tb.addAction(self._save_session_action)
        tb.addAction(self._load_session_action)
        tb.addAction(self._save_ca_action)

        # Expanding spacer pushes the status indicators to the right edge.
        tb.addWidget(self._make_toolbar_spacer())

        self._proxy_indicator = QLabel(f"Proxy: {proxy_host}:{proxy_port}", self)
        self._proxy_indicator.setObjectName("proxyIndicator")
        tb.addWidget(self._proxy_indicator)

        self._chrome_indicator = QLabel("Chrome: not launched", self)
        self._chrome_indicator.setObjectName("chromeIndicator")
        self._chrome_indicator.setProperty("state", "off")
        tb.addWidget(self._chrome_indicator)

        tb.addSeparator()
        tb.addAction(self._theme_action)

        # ---- tabs ----
        self._tabs = QTabWidget(self)
        self._tabs.setDocumentMode(True)
        # macOS's native QTabBar painter ignores QSS background; this attr
        # makes it honor the chrome_bg rule so the strip right of the last
        # tab matches the theme instead of inheriting the window bg.
        self._tabs.tabBar().setAttribute(Qt.WA_StyledBackground, True)

        self.proxy_tab = ProxyTab(self._bridge, self)
        self.intercept_tab = InterceptTab(self._bridge, self)
        self.repeater_tab = RepeaterTab(self)
        self.intruder_tab = IntruderTab(self)
        self.decoder_tab = DecoderTab(self)
        self.scope_tab = ScopeTab(self)

        self._tabs.addTab(self.proxy_tab, "Proxy")
        self._tabs.addTab(self.intercept_tab, "Intercept")
        self._tabs.addTab(self.repeater_tab, "Repeater")
        self._tabs.addTab(self.intruder_tab, "Intruder")
        self._tabs.addTab(self.decoder_tab, "Decoder")
        self._tabs.addTab(self.scope_tab, "Scope")

        self.setCentralWidget(self._tabs)

        # ---- status bar ----
        sb = QStatusBar(self)
        self.setStatusBar(sb)
        self._sb_label = QLabel("Ready", self)
        sb.addWidget(self._sb_label)

        # ---- wiring ----
        self._launch_action.triggered.connect(self._launch_browser)
        self._intercept_action.toggled.connect(self._toggle_intercept)
        self._clear_action.triggered.connect(STORE.clear)
        self._save_session_action.triggered.connect(self._save_session)
        self._load_session_action.triggered.connect(self._load_session)
        self._save_ca_action.triggered.connect(self._save_ca)
        self._theme_action.triggered.connect(self._toggle_theme)

        self.proxy_tab.send_to_repeater.connect(self._send_to_repeater)
        self.proxy_tab.send_to_intruder.connect(self._send_to_intruder)
        self.intercept_tab.send_to_repeater.connect(self._intercept_to_repeater)
        self.intercept_tab.send_to_intruder.connect(self._intercept_to_intruder)

        self._bridge.added.connect(self._on_added)

        # Apply the saved theme once everything is wired up.
        # Fusion-app-wide is set in qt/app.py::run so QSS is honored across
        # the board — this widget just inherits that.
        self._apply_theme(self._theme)

        # Poll Chrome process every 1.5s and update intercept badge.
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_state)
        self._poll.start(1500)

    # ---- helpers -------------------------------------------------------
    def _make_toolbar_spacer(self) -> QWidget:
        """Expanding spacer for the toolbar — pushes whatever follows to the right."""
        w = QWidget(self)
        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Without this the global `QWidget { background: bg; }` rule paints
        # the spacer in content-bg, which shows as a coloured band cutting
        # through the toolbar between Save CA and the status indicators.
        w.setStyleSheet("background: transparent;")
        return w

    def _theme_action_label(self) -> str:
        return "Theme: Light" if self._theme == "dark" else "Theme: Dark"

    def _send_to_repeater(self, tx_id: int) -> None:
        self.repeater_tab.new_session_from_tx(tx_id)
        self._tabs.setCurrentWidget(self.repeater_tab)

    def _send_to_intruder(self, tx_id: int) -> None:
        self.intruder_tab.new_session_from_tx(tx_id)
        self._tabs.setCurrentWidget(self.intruder_tab)

    def _intercept_to_repeater(self, tx_id: int, raw) -> None:
        tx = STORE.get(tx_id)
        if tx is None:
            return
        self.repeater_tab.new_session_from_bytes(
            tx.scheme, tx.host, tx.port, bytes(raw), label=f"#{tx_id} {tx.host}"
        )
        self._tabs.setCurrentWidget(self.repeater_tab)

    def _intercept_to_intruder(self, tx_id: int, raw) -> None:
        tx = STORE.get(tx_id)
        if tx is None:
            return
        self.intruder_tab.new_session_from_bytes(
            tx.scheme, tx.host, tx.port, bytes(raw), label=f"#{tx_id} {tx.host}"
        )
        self._tabs.setCurrentWidget(self.intruder_tab)

    # ---- theme ---------------------------------------------------------
    def _apply_theme(self, name: str) -> None:
        if name not in THEMES:
            name = DEFAULT_THEME
        self._theme = name
        self.setStyleSheet(THEMES[name])
        # Push our colors into the QApplication palette so Fusion's native
        # painters (tab-strip separator line, sunken QFrame borders, the
        # splitter shadow, etc.) use them instead of Fusion's default dark
        # grays. Without this you see thin "random black lines" wherever
        # Fusion draws on top of the QSS background.
        self._apply_palette(_DARK if name == "dark" else _LIGHT)
        # The HTTP syntax highlighter caches its colors on construction —
        # walk every HttpViewer/HttpEditor and re-stain it for the new
        # theme. Without this the request body text stays at the dark-mode
        # near-white colour and reads as invisible on the light surface.
        from .widgets.http_viewer import HttpViewer
        from PySide6.QtWidgets import QApplication
        qapp = QApplication.instance()
        if qapp is not None:
            for w in qapp.allWidgets():
                if isinstance(w, HttpViewer):
                    w.refresh_highlight_theme()
            # Force every widget to re-polish and repaint with the new
            # palette + stylesheet. Qt's PaletteChange event reaches all
            # widgets, but Fusion's PE_FrameTabWidget primitive caches the
            # frame it painted using the previous palette. Without this
            # re-polish the cached `border`-coloured frame reappears as a
            # stripe on every theme toggle, even though the palette change
            # itself looks correct.
            for w in qapp.allWidgets():
                style = w.style()
                if style is not None:
                    style.unpolish(w)
                    style.polish(w)
                w.update()
        self._theme_action.setText(self._theme_action_label())
        self._settings.setValue("theme", name)

    def _apply_palette(self, palette_dict: dict[str, str]) -> None:
        from PySide6.QtWidgets import QApplication
        qapp = QApplication.instance()
        if qapp is None:
            return
        c = lambda key: QColor(palette_dict[key])
        p = QPalette()
        # Window / surface
        p.setColor(QPalette.Window, c("bg"))
        p.setColor(QPalette.WindowText, c("fg"))
        p.setColor(QPalette.Base, c("surface"))
        p.setColor(QPalette.AlternateBase, c("row_alt"))
        p.setColor(QPalette.Text, c("fg"))
        p.setColor(QPalette.BrightText, c("fg"))
        p.setColor(QPalette.PlaceholderText, c("fg_subtle"))
        # Buttons (Fusion uses these for button gradients underneath QSS)
        p.setColor(QPalette.Button, c("surface"))
        p.setColor(QPalette.ButtonText, c("fg"))
        # Tooltips
        p.setColor(QPalette.ToolTipBase, c("chrome_bg"))
        p.setColor(QPalette.ToolTipText, c("fg"))
        # Frame / border roles. Fusion's PE_FrameTabWidget primitive uses
        # QPalette.Mid + QPalette.Shadow to paint a 1px frame around the tab
        # pane — that's the stripe the user sees right at the bar→pane
        # boundary (tabs cover it where they sit, it's visible in the gap
        # right of the last tab and across the session-tab strips). Setting
        # Mid + Shadow to chrome_bg means Fusion still paints, but the line
        # colour matches the surface beneath and is invisible.
        # QSS-driven borders use literal `{border}` from the format string,
        # not the palette, so splitter handles / table outlines / toolbar
        # bottom etc. stay sharp.
        p.setColor(QPalette.Light, c("chrome_bg"))
        p.setColor(QPalette.Midlight, c("chrome_bg"))
        p.setColor(QPalette.Mid, c("chrome_bg"))
        p.setColor(QPalette.Dark, c("border_strong"))
        p.setColor(QPalette.Shadow, c("chrome_bg"))
        # Selection / accent
        p.setColor(QPalette.Highlight, c("accent"))
        p.setColor(QPalette.HighlightedText, c("accent_fg"))
        p.setColor(QPalette.Link, c("accent"))
        p.setColor(QPalette.LinkVisited, c("accent_pressed"))
        # Disabled overrides — dimmer everything when widgets are disabled
        p.setColor(QPalette.Disabled, QPalette.WindowText, c("fg_subtle"))
        p.setColor(QPalette.Disabled, QPalette.Text, c("fg_subtle"))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, c("fg_subtle"))
        qapp.setPalette(p)

    def _toggle_theme(self) -> None:
        self._apply_theme("light" if self._theme == "dark" else "dark")

    # ---- toolbar actions ----------------------------------------------
    def _proxy_is_listening(self) -> bool:
        """Quick TCP probe so we don't launch Chrome at a dead proxy."""
        try:
            with socket.create_connection(
                (self._proxy_host, self._proxy_port), timeout=0.3
            ):
                return True
        except OSError:
            return False

    def _launch_browser(self) -> None:
        if not self._proxy_is_listening():
            QMessageBox.warning(
                self,
                "Proxy not running",
                f"The proxy at {self._proxy_host}:{self._proxy_port} isn't "
                f"accepting connections, so Chrome would show "
                f"ERR_PROXY_CONNECTION_FAILED.\n\n"
                f"Quit MiniBurp and relaunch. If the issue persists, another "
                f"process is using the port.",
            )
            return
        try:
            proc = browser_module.launch(
                proxy_host=self._proxy_host,
                proxy_port=self._proxy_port,
                start_url=self._start_url,
            )
            self._chrome_proc = proc
            self._sb_label.setText(f"Chrome launched (pid {proc.pid})")
        except RuntimeError as e:
            QMessageBox.warning(self, "No browser found", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Launch failed", str(e))

    def _toggle_intercept(self, on: bool) -> None:
        INTERCEPT.set_enabled(on)
        self._intercept_action.setText(f"Intercept: {'ON' if on else 'OFF'}")
        self._sb_label.setText(f"Intercept {'enabled' if on else 'disabled'}")

    def _save_session(self) -> None:
        from ..session_io import dump_transactions
        target, _ = QFileDialog.getSaveFileName(
            self, "Save session", "miniburp-session.json",
            "MiniBurp session (*.json)",
        )
        if not target:
            return
        try:
            txs = STORE.all()
            Path(target).write_bytes(dump_transactions(txs))
            self._sb_label.setText(f"Session saved ({len(txs)} request(s)) → {target}")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def _load_session(self) -> None:
        from ..session_io import load_transactions
        target, _ = QFileDialog.getOpenFileName(
            self, "Load session", "", "MiniBurp session (*.json)"
        )
        if not target:
            return
        try:
            items = load_transactions(Path(target).read_bytes())
        except Exception as e:
            QMessageBox.critical(self, "Load failed", str(e))
            return
        STORE.load(items)
        self._sb_label.setText(f"Session loaded ({len(items)} request(s))")

    def _save_ca(self) -> None:
        target, _ = QFileDialog.getSaveFileName(
            self, "Save MiniBurp CA", "miniburp-ca.crt", "PEM Certificates (*.crt)"
        )
        if not target:
            return
        try:
            data = ca_module.CA_CERT_PATH.read_bytes()
            Path(target).write_bytes(data)
            self._sb_label.setText(f"CA saved to {target}")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    # ---- background polling -------------------------------------------
    def _set_chrome_state(self, state: str, text: str) -> None:
        self._chrome_indicator.setText(text)
        if self._chrome_indicator.property("state") != state:
            self._chrome_indicator.setProperty("state", state)
            _repolish(self._chrome_indicator)

    def _poll_state(self) -> None:
        # Chrome status
        if self._chrome_proc is None:
            self._set_chrome_state("off", "Chrome: not launched")
        else:
            rc = self._chrome_proc.poll()
            if rc is None:
                self._set_chrome_state(
                    "on", f"Chrome: running (pid {self._chrome_proc.pid})"
                )
            else:
                self._set_chrome_state("exited", f"Chrome: exited (rc {rc})")
                self._chrome_proc = None

        # Intercept toolbar text stays in sync with the core toggle.
        on = INTERCEPT.is_enabled()
        if on != self._intercept_action.isChecked():
            self._intercept_action.blockSignals(True)
            self._intercept_action.setChecked(on)
            self._intercept_action.blockSignals(False)
            self._intercept_action.setText(f"Intercept: {'ON' if on else 'OFF'}")

    def _on_added(self, _summary: dict) -> None:
        self._sb_label.setText(f"{len(STORE.all())} request(s) captured")

    # ---- shutdown ------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Kill tracked Chrome so it doesn't outlive the app.
        if self._chrome_proc is not None and self._chrome_proc.poll() is None:
            try:
                self._chrome_proc.terminate()
            except Exception:
                pass
        # Release any held intercepts.
        INTERCEPT.set_enabled(False)
        # Shut down the bridge thread.
        self._bridge.shutdown()
        super().closeEvent(event)
