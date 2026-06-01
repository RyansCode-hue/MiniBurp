"""
Headless smoke test for the Qt desktop UI.

Instantiates the whole MainWindow (every tab) offscreen so a construction-time
error in any tab — bad signal wiring, a missing import, a QSS format key —
fails CI instead of only surfacing when a human launches the app. It does not
drive interactions; it just proves the widget tree builds under both themes.

Skips cleanly where PySide6 can't start (no display libraries on the runner).
"""
from __future__ import annotations

import os

import pytest

# Must be set before the first QApplication is created.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    try:
        inst = QApplication.instance() or QApplication([])
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"Qt cannot start headless here: {e}")
    return inst


def test_mainwindow_builds_all_tabs(app):
    from miniburp.qt.main_window import MainWindow, THEMES

    w = MainWindow("127.0.0.1", 8080, start_url=None)
    try:
        tab_titles = [w._tabs.tabText(i) for i in range(w._tabs.count())]
        assert tab_titles == [
            "Proxy", "Intercept", "Repeater", "Intruder",
            "Decoder", "Match/Replace", "Scope",
        ]
        # Both stylesheets apply without a QSS format KeyError.
        for name in THEMES:
            w._apply_theme(name)
    finally:
        w.close()


def test_proxy_context_menu_copy_helpers(app):
    """The history copy-as-curl path renders against a real Transaction."""
    from miniburp.storage import STORE
    from miniburp.curl import to_curl

    STORE.clear()
    tx = STORE.add(
        method="GET", scheme="https", host="lab.net", port=443,
        path="/image?filename=10.jpg", req_headers={"Host": "lab.net"},
        req_body=b"",
        raw_request=b"GET /image?filename=10.jpg HTTP/1.1\r\nHost: lab.net\r\n\r\n",
    )
    cmd = to_curl(tx.method, tx.url, tx.req_headers, tx.req_body)
    assert "lab.net/image?filename=10.jpg" in cmd
    STORE.clear()


def test_intercept_send_to_repeater_carries_edited_bytes(app):
    from miniburp.qt.main_window import MainWindow
    from miniburp.storage import STORE

    STORE.clear()
    tx = STORE.add(
        method="GET", scheme="https", host="lab.net", port=443,
        path="/image?filename=10.jpg", req_headers={"Host": "lab.net"},
        req_body=b"",
        raw_request=b"GET /image?filename=10.jpg HTTP/1.1\r\nHost: lab.net\r\n\r\n",
    )
    w = MainWindow("127.0.0.1", 8080)
    try:
        edited = (
            b"GET /image?filename=../../../etc/passwd HTTP/1.1\r\n"
            b"Host: lab.net\r\n\r\n"
        )
        before = w.repeater_tab._tabs.count()
        w._intercept_to_repeater(tx.id, edited)
        assert w.repeater_tab._tabs.count() == before + 1
        # The user's edited held bytes seed the session, not the captured request.
        sess = w.repeater_tab._tabs.currentWidget()
        assert b"etc/passwd" in sess._req_editor.bytes_value()

        w._intercept_to_intruder(tx.id, edited)
        templ = w.intruder_tab._tabs.currentWidget()._template.bytes_value()
        assert b"etc/passwd" in templ
    finally:
        w.close()
        STORE.clear()
