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


def test_repeater_session_history_and_diff(app):
    from miniburp.qt.tabs.repeater_tab import RepeaterSession

    s = RepeaterSession(
        scheme="https", host="lab.net", port=443,
        raw=b"GET / HTTP/1.1\r\nHost: lab.net\r\n\r\n",
    )
    try:
        s._pending_raw = b"GET /a HTTP/1.1\r\nHost: lab.net\r\n\r\n"
        s._on_response({
            "error": None, "status": 200, "headers": {}, "body": b"line1\nline2",
            "raw": b"HTTP/1.1 200 OK\r\n\r\nline1\nline2", "duration_ms": 5,
        })
        assert s._hist_label.text() == "1/1"

        s._pending_raw = b"GET /b HTTP/1.1\r\nHost: lab.net\r\n\r\n"
        s._on_response({
            "error": None, "status": 200, "headers": {}, "body": b"line1\nCHANGED",
            "raw": b"HTTP/1.1 200 OK\r\n\r\nline1\nCHANGED", "duration_ms": 6,
        })
        assert s._hist_label.text() == "2/2"

        # Stepping back restores the request that produced response #1.
        s._step_history(-1)
        assert s._hist_label.text() == "1/2"
        assert b"/a" in s._req_editor.bytes_value()

        # Diff on the second send shows the body delta.
        s._step_history(1)
        s._diff_check.setChecked(True)
        assert b"CHANGED" in s._res_viewer._raw_bytes
        assert b"-line2" in s._res_viewer._raw_bytes
    finally:
        s.deleteLater()


def test_render_mode_only_for_html_readonly(app):
    from miniburp.qt.widgets.http_viewer import HttpEditor, HttpViewer

    v = HttpViewer("Response", editable=False)
    v.set_bytes(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{}")
    assert v._render_btn.isEnabled() is False  # not HTML

    v.set_bytes(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"
        b"<html><body>root:x:0:0</body></html>"
    )
    assert v._render_btn.isEnabled() is True
    v._render_btn.setChecked(True)
    assert v._mode == "render"
    # Hex and Render are mutually exclusive.
    v._hex_btn.setChecked(True)
    assert v._mode == "hex" and v._render_btn.isChecked() is False

    # Editable editors don't get a render affordance at all.
    e = HttpEditor("Request")
    assert e._render_btn is None and e._render_view is None


def test_param_grid_edits_write_back_to_raw(app):
    from miniburp.qt.widgets.http_viewer import HttpEditor, HttpViewer

    e = HttpEditor("Request")
    e.set_bytes(b"GET /image?filename=10.jpg HTTP/1.1\r\nHost: lab.net\r\n\r\n")
    e._params_btn.setChecked(True)  # reveal the Params table
    t = e._params_table
    assert t.rowCount() == 1
    assert t.item(0, 1).text() == "filename"
    # Editing the value cell rewrites the raw request; slashes stay literal.
    t.item(0, 2).setText("../../../etc/passwd")
    assert b"GET /image?filename=../../../etc/passwd HTTP/1.1" in e.bytes_value()

    # Read-only viewers have no params panel.
    v = HttpViewer("Response", editable=False)
    assert v._params_btn is None and v._params_table is None
