"""
MiniBurp launcher.

Default (desktop) mode:
    python run.py                       -> proxy + PySide6 desktop app
    python run.py --url LAB_URL         -> open Chrome straight to LAB_URL
    python run.py --no-browser          -> don't auto-launch Chrome

Legacy web mode (kept for development / headless environments):
    python run.py --web                 -> proxy + Flask UI at http://127.0.0.1:8888/
    python run.py --web --no-browser
"""
from __future__ import annotations

import argparse
import sys
import threading
import time


def _run_web(args: argparse.Namespace) -> int:
    """Legacy Flask UI path."""
    from miniburp import browser, proxy, webui

    threading.Thread(
        target=proxy.serve,
        kwargs={"host": args.host, "port": args.proxy_port},
        daemon=True,
        name="proxy",
    ).start()

    if args.browser:
        def _launch_soon() -> None:
            time.sleep(0.8)
            try:
                start_url = args.url or f"http://{args.host}:{args.ui_port}/"
                browser.launch(
                    proxy_host=args.host,
                    proxy_port=args.proxy_port,
                    start_url=start_url,
                )
            except RuntimeError as e:
                print(f"[browser] {e}")
            except Exception as e:
                print(f"[browser] launch failed: {e}")

        threading.Thread(target=_launch_soon, daemon=True, name="browser-launcher").start()

    webui.serve(
        host=args.host,
        port=args.ui_port,
        proxy_host=args.host,
        proxy_port=args.proxy_port,
    )
    return 0


def _run_desktop(args: argparse.Namespace) -> int:
    """PySide6 desktop path."""
    try:
        from miniburp.qt import app as qt_app
    except ImportError as e:
        print(
            f"[desktop] PySide6 isn't installed: {e}\n"
            "[desktop] run `pip install pyside6` or use `python run.py --web` for the legacy Flask UI.",
            file=sys.stderr,
        )
        return 2

    return qt_app.run(
        host=args.host,
        proxy_port=args.proxy_port,
        launch_browser=args.browser,
        start_url=args.url,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="MiniBurp - an educational intercepting proxy")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--proxy-port", type=int, default=8080)
    ap.add_argument("--ui-port", type=int, default=8888,
                    help="Flask UI port (only used with --web).")
    ap.add_argument("--web", action="store_true",
                    help="Use the legacy Flask web UI instead of the native desktop app.")
    ap.add_argument("--no-browser", dest="browser", action="store_false", default=True,
                    help="Don't launch a tracked browser on startup.")
    ap.add_argument("--browser", dest="browser", action="store_true",
                    help="Launch a tracked browser (default).")
    ap.add_argument("--url", default=None,
                    help="URL to open in the tracked browser on launch.")
    args = ap.parse_args()

    if args.web:
        return _run_web(args) or 0
    return _run_desktop(args)


if __name__ == "__main__":
    raise SystemExit(main())
