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
from PySide6.QtGui import QAction
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
QMainWindow, QWidget {{ background: {bg}; color: {fg}; }}

QTabWidget::pane {{ border-top: 1px solid {border}; }}
QTabBar::tab {{
    background: {chrome_bg}; color: {fg_muted}; padding: 6px 14px; border: 0;
    border-right: 1px solid {border}; font-weight: 600;
}}
QTabBar::tab:selected {{
    color: {accent}; background: {bg}; border-bottom: 2px solid {accent};
}}
QTabBar::tab:hover {{ color: {fg}; }}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {input_bg}; color: {fg}; border: 1px solid {border};
    border-radius: 4px; padding: 4px 6px;
    selection-background-color: {accent}; selection-color: {accent_fg};
}}
QPlainTextEdit, QTextEdit {{ padding: 6px; }}

QPushButton {{
    background: {accent}; color: {accent_fg}; border: 0; padding: 6px 14px;
    border-radius: 4px; font-weight: 600;
}}
QPushButton:hover {{ background: {accent_hover}; }}
QPushButton:disabled {{ background: {disabled_bg}; color: {fg_muted}; }}

/* "Drop" / destructive button uses a red accent. */
QPushButton[role="danger"] {{ background: {err}; color: #ffffff; }}
QPushButton[role="danger"]:hover {{ background: {err_hover}; }}

QTableView, QTableWidget, QListWidget {{
    background: {bg}; color: {fg}; alternate-background-color: {row_alt};
    gridline-color: {border}; border: 1px solid {border};
    selection-background-color: {accent}; selection-color: {accent_fg};
}}
QHeaderView::section {{
    background: {chrome_bg}; color: {fg_muted}; padding: 4px 8px; border: 0;
    border-right: 1px solid {border}; font-weight: 600; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.5px;
}}

QToolBar {{
    background: {chrome_bg}; border-bottom: 1px solid {border};
    spacing: 6px; padding: 4px 8px;
}}
QStatusBar {{
    background: {chrome_bg}; border-top: 1px solid {border}; color: {fg_muted};
}}

QSplitter::handle {{ background: {border}; }}
QSplitter::handle:horizontal {{ width: 2px; }}
QSplitter::handle:vertical {{ height: 2px; }}

QCheckBox {{ color: {fg}; }}
QLabel {{ color: {fg}; }}

/* Semantic label styles -- driven by `setProperty("role", ...)`. */
QLabel[role="muted"]   {{ color: {fg_muted}; }}
QLabel[role="ok"]      {{ color: {ok}; }}
QLabel[role="warn"]    {{ color: {warn}; }}
QLabel[role="err"]     {{ color: {err}; }}
QLabel[role="section"] {{
    color: {fg_muted}; font-size: 11px; font-weight: 600;
    letter-spacing: 0.5px;
}}
QLabel[role="title"]   {{ font-weight: 600; }}

/* The proxy/chrome status pills in the toolbar. State drives the color. */
QLabel#proxyIndicator                    {{ color: {ok};        padding: 0 10px; }}
QLabel#chromeIndicator[state="off"]      {{ color: {fg_muted};  padding: 0 10px; }}
QLabel#chromeIndicator[state="on"]       {{ color: {ok};        padding: 0 10px; }}
QLabel#chromeIndicator[state="exited"]   {{ color: {warn};      padding: 0 10px; }}

QScrollBar:vertical {{ background: {bg}; width: 12px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {scroll_hover}; }}
QScrollBar:horizontal {{ background: {bg}; height: 12px; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{
    background: none; border: none; width: 0; height: 0;
}}
"""

_DARK = {
    "bg":            "#1b1b1f",
    "chrome_bg":     "#24242b",
    "input_bg":      "#24242b",
    "row_alt":       "#20202a",
    "border":        "#353540",
    "fg":            "#e8e8ec",
    "fg_muted":      "#9a9aa8",
    "accent":        "#ff7030",
    "accent_hover":  "#ff8450",
    "accent_fg":     "#1b1b1f",
    "disabled_bg":   "#3a3a44",
    "ok":            "#5ecb7b",
    "warn":          "#e7b648",
    "err":           "#ef6767",
    "err_hover":     "#f48989",
    "scroll_hover":  "#4a4a58",
}

_LIGHT = {
    "bg":            "#fafafc",
    "chrome_bg":     "#ececf0",
    "input_bg":      "#ffffff",
    "row_alt":       "#f3f3f7",
    "border":        "#d3d3da",
    "fg":            "#1b1b1f",
    "fg_muted":      "#6c6c78",
    "accent":        "#ff7030",
    "accent_hover":  "#ff8450",
    "accent_fg":     "#ffffff",
    "disabled_bg":   "#dcdce2",
    "ok":            "#1f9d4f",
    "warn":          "#a8761a",
    "err":           "#c93030",
    "err_hover":     "#dd4848",
    "scroll_hover":  "#bcbcc4",
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
        self._save_ca_action = QAction("Save CA…", self)
        self._theme_action = QAction(self._theme_action_label(), self)

        tb.addAction(self._launch_action)
        tb.addAction(self._intercept_action)
        tb.addSeparator()
        tb.addAction(self._clear_action)
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
        self._save_ca_action.triggered.connect(self._save_ca)
        self._theme_action.triggered.connect(self._toggle_theme)

        self.proxy_tab.send_to_repeater.connect(self._send_to_repeater)
        self.proxy_tab.send_to_intruder.connect(self._send_to_intruder)

        self._bridge.added.connect(self._on_added)

        # Apply the saved theme once everything is wired up.
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
        return w

    def _theme_action_label(self) -> str:
        return "Theme: Light" if self._theme == "dark" else "Theme: Dark"

    def _send_to_repeater(self, tx_id: int) -> None:
        self.repeater_tab.new_session_from_tx(tx_id)
        self._tabs.setCurrentWidget(self.repeater_tab)

    def _send_to_intruder(self, tx_id: int) -> None:
        self.intruder_tab.new_session_from_tx(tx_id)
        self._tabs.setCurrentWidget(self.intruder_tab)

    # ---- theme ---------------------------------------------------------
    def _apply_theme(self, name: str) -> None:
        if name not in THEMES:
            name = DEFAULT_THEME
        self._theme = name
        self.setStyleSheet(THEMES[name])
        self._theme_action.setText(self._theme_action_label())
        self._settings.setValue("theme", name)

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
