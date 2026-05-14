"""
PySide6 application entry point.

Responsibilities:
    * Start the intercepting proxy on a background thread.
    * Create the QApplication + MainWindow.
    * (Optionally) auto-launch the tracked browser once the proxy is listening.
    * Block in app.exec() until the user quits.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .. import proxy as proxy_module
from .._build_check import StalenessReport, check_for_staleness


def _icon_path() -> Optional[Path]:
    """
    Best-effort lookup for the bundled PNG icon. Works both when running from
    source (assets/icon.png next to run.py) and from a PyInstaller bundle
    (PyInstaller stages it under sys._MEIPASS/assets/icon.png).
    """
    # PyInstaller stash dir
    meipass = getattr(sys, "_MEIPASS", None)
    candidates: list[Path] = []
    if meipass:
        candidates.append(Path(meipass) / "assets" / "icon.png")
    # Source layout: project root / assets / icon.png
    candidates.append(Path(__file__).resolve().parents[2] / "assets" / "icon.png")
    for p in candidates:
        if p.is_file():
            return p
    return None


def _wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    """Busy-wait until the proxy accepts connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _prompt_for_rebuild(staleness: StalenessReport) -> bool:
    """
    Show the stale-bundle dialog. Returns True if the user picked "Rebuild".

    A QApplication must already exist when this is called.
    """
    from PySide6.QtWidgets import QMessageBox

    msg = QMessageBox()
    msg.setIcon(QMessageBox.Warning)
    msg.setWindowTitle("MiniBurp bundle is out of date")
    msg.setText(
        f"This MiniBurp.app was built from {staleness.build_sha[:7]} "
        f"on {staleness.build_time}."
    )
    msg.setInformativeText(
        staleness.reason
        + "\n\nRebuild now so the bundle picks up the latest source?"
    )
    # `&&` so Qt doesn't eat the '&' as a mnemonic.
    rebuild_btn = msg.addButton("Rebuild && quit", QMessageBox.AcceptRole)
    msg.addButton("Continue anyway", QMessageBox.RejectRole)
    msg.setDefaultButton(rebuild_btn)
    msg.exec()
    return msg.clickedButton() is rebuild_btn


def _spawn_rebuild(source_root: Path) -> None:
    """Kick off `./build.sh --run` in the background and exit this process."""
    script = source_root / "build.sh"
    if not script.is_file():
        # Shouldn't normally happen — find_source_repo verified the layout —
        # but fall back gracefully so the user isn't stranded.
        print(f"[bundle-check] {script} not found; rebuild manually.", file=sys.stderr)
        return
    try:
        subprocess.Popen(
            [str(script), "--run"],
            cwd=str(source_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        print(f"[bundle-check] failed to start {script}: {e}", file=sys.stderr)


def run(
    host: str = "127.0.0.1",
    proxy_port: int = 8080,
    launch_browser: bool = True,
    start_url: Optional[str] = None,
) -> int:
    """Start everything and block until the Qt event loop exits."""
    # Allow Qt to run in the sandbox / headless CI.
    os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM", ""))

    # PySide6 imports are deferred so --web mode can still run without Qt installed.
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox, QStyleFactory

    from .main_window import MainWindow

    app = QApplication(sys.argv if sys.argv else ["miniburp"])
    app.setApplicationName("MiniBurp")
    app.setOrganizationName("MiniBurp")
    # Switch to Fusion app-wide so every widget honors our QSS. macOS's native
    # painters ignore stylesheet rules on QTabBar, QComboBox popups, and parts
    # of QToolBar — visible as the "dark patch beside Scope", the "black band
    # in the dropdown", and the popup-above-button placement quirk. Fusion is
    # the cross-platform style that respects QSS fully; MiniBurp is already
    # so QSS-heavy that switching loses nothing native we were keeping.
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        app.setStyle(fusion)

    # Detect a stale bundle: this .app was built before the source it lives
    # next to. Quietly returns None when running from source, when the bundle
    # has been copied away from its repo, or when MINIBURP_BUNDLE_CHECK=off.
    staleness = check_for_staleness()
    if staleness is not None:
        if _prompt_for_rebuild(staleness):
            # User chose to rebuild; spawn build.sh and exit so the new
            # bundle can replace this one cleanly.
            _spawn_rebuild(staleness.source_root)
            return 0

    # Bind the proxy in the foreground so a port-in-use error surfaces to the
    # user instead of dying silently in a daemon thread (where stderr from a
    # Finder-launched .app is invisible).
    try:
        srv = proxy_module.bind_with_fallback(host=host, preferred_port=proxy_port)
    except OSError as e:
        QMessageBox.critical(
            None,
            "MiniBurp — cannot start proxy",
            f"Failed to bind {host}:{proxy_port} (or any of the next 9 ports).\n\n"
            f"{e}\n\n"
            f"Another process is probably using these ports. Quit it and relaunch, "
            f"or run from source with --proxy-port to pick a different port.",
        )
        return 1

    actual_port = srv.getsockname()[1]
    if actual_port != proxy_port:
        print(f"[proxy] port {proxy_port} was busy, fell back to {actual_port}")

    threading.Thread(
        target=proxy_module.accept_loop,
        args=(srv,),
        daemon=True,
        name="proxy",
    ).start()

    # App-wide icon. The bundle's BUNDLE step sets the .icns for Finder/Dock,
    # but setting QIcon here also covers source-runs and the running-process
    # representation in some macOS versions.
    icon_path = _icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow(proxy_host=host, proxy_port=actual_port, start_url=start_url)
    if icon_path is not None:
        window.setWindowIcon(QIcon(str(icon_path)))
    window.show()

    # Make sure the bridge's pump thread is stopped before Qt tears down the
    # application. ``closeEvent`` covers the "X" button; ``aboutToQuit`` covers
    # Cmd-Q and other exit paths where ``closeEvent`` may not fire in time.
    # ``shutdown()`` is idempotent so it's safe to call from both.
    def _on_about_to_quit() -> None:
        try:
            window._bridge.shutdown()
        except Exception:
            pass
    app.aboutToQuit.connect(_on_about_to_quit)

    # Auto-launch browser once the proxy is listening.
    if launch_browser:
        def _launch_when_ready() -> None:
            if _wait_for_port(host, actual_port, timeout=5.0):
                # Use a Qt timer to hop back onto the main thread.
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, window._launch_browser)
        threading.Thread(target=_launch_when_ready, daemon=True, name="browser-launcher").start()

    return app.exec()
