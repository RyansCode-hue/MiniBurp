"""
Launch a Chromium-based browser pre-wired to use our proxy.

This is the same trick Burp Suite's embedded browser uses:

* `--proxy-server=http://host:port` sends all traffic through us.
* `--user-data-dir=<tmp>` uses a disposable profile so the user's real
  Chrome isn't touched and no extensions / saved passwords leak in.

For HTTPS interception to work without warnings, MiniBurp's root CA must
be imported into the OS trust store (Keychain on macOS, etc.). The CLI
prints the path to ca.crt at startup.

We search the usual install paths for Chrome, Chromium, Brave, or Edge.
The env var MINIBURP_CHROME overrides auto-detection.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


CHROME_CANDIDATES = {
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
    "Linux": [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/brave-browser",
        "/usr/bin/microsoft-edge",
        "/snap/bin/chromium",
    ],
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    ],
}


def find_chrome() -> Optional[str]:
    """Return the first runnable Chromium-family browser we can find."""
    env = os.environ.get("MINIBURP_CHROME")
    if env and Path(env).exists():
        return env

    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "chrome",
        "brave-browser",
        "microsoft-edge",
    ):
        p = shutil.which(name)
        if p:
            return p

    for candidate in CHROME_CANDIDATES.get(platform.system(), []):
        if Path(candidate).exists():
            return candidate
    return None


def build_command(
    exe: str,
    proxy_host: str,
    proxy_port: int,
    user_data_dir: str,
    start_url: Optional[str],
) -> list[str]:
    """Assemble the argv for the browser. Kept separate so we can unit-test it."""
    # MiniBurp is a lab-only tool. Every flag below either configures the proxy
    # path or silences Chrome background chatter (Safe Browsing list refresh,
    # Omaha update polling, component CRX downloads, sync, telemetry, etc.) so
    # the proxy history shows only the website under test. If you remove a
    # flag, expect Google-services traffic to start appearing in history.
    cmd = [
        exe,
        f"--user-data-dir={user_data_dir}",
        f"--proxy-server=http://{proxy_host}:{proxy_port}",
        # Don't try to proxy 127.0.0.1 (would loop through ourselves).
        "--proxy-bypass-list=<-loopback>",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--allow-insecure-localhost",
        # Chatter suppression — kills Safe Browsing refresh, Omaha update
        # polling, Chrome Variations, field trials, network time, etc.
        "--disable-background-networking",
        "--disable-sync",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-domain-reliability",
        "--disable-client-side-phishing-detection",
        "--metrics-recording-only",
        "--no-pings",
        "--safebrowsing-disable-auto-update",
        "--disable-features=Translate,OptimizationHints,"
        "OptimizationGuideModelDownloading,MediaRouter,"
        "InterestFeedContentSuggestions,CalculateNativeWinOcclusion",
    ]
    if start_url:
        cmd.append(start_url)
    return cmd


# keep a reference so subprocess objects aren't GC'd mid-run
_active_procs: list[subprocess.Popen] = []


def launch(
    proxy_host: str = "127.0.0.1",
    proxy_port: int = 8080,
    user_data_dir: Optional[str] = None,
    start_url: Optional[str] = "http://127.0.0.1:8888/",
) -> subprocess.Popen:
    exe = find_chrome()
    if not exe:
        raise RuntimeError(
            "No Chromium-based browser found. Install Google Chrome, Chromium, "
            "Brave, or Microsoft Edge, or set the MINIBURP_CHROME environment "
            "variable to the browser binary path."
        )

    if user_data_dir is None:
        user_data_dir = tempfile.mkdtemp(prefix="miniburp-chrome-")

    cmd = build_command(exe, proxy_host, proxy_port, user_data_dir, start_url)

    # start_new_session detaches so the browser survives if our main thread dies
    # during testing; stdout/stderr go to /dev/null so Chrome's chatter doesn't
    # pollute our proxy log.
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    _active_procs.append(proc)
    print(f"[browser] launched {Path(exe).name} (pid {proc.pid}) "
          f"through {proxy_host}:{proxy_port}, profile at {user_data_dir}")
    return proc
