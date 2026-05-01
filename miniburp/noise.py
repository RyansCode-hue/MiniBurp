"""
Chatter filter — keeps Chrome's background noise out of the proxy history.

MiniBurp is a lab-only tool. The proxy history must show only traffic to the
website(s) being tested, never Chrome's automatic chatter (Safe Browsing,
sync, component updates, telemetry). The Chrome flags in browser.py suppress
most of this at the source; this module is the safety net for anything that
leaks through (new chatter sources Chrome adds in future versions, non-Chrome
clients pointed at the proxy, etc.).

Set MINIBURP_NOISE_FILTER=off to disable — useful when intentionally testing
a Google or Mozilla service.
"""
from __future__ import annotations

import os


# Domain suffixes whose traffic should never enter the proxy history.
# A host matches if it equals the suffix or ends in "." + suffix.
_NOISE_SUFFIXES = frozenset({
    # Google service umbrellas
    "google.com",
    "googleapis.com",
    "gvt1.com",
    "gstatic.com",
    "googleusercontent.com",
    "googletagmanager.com",
    "google-analytics.com",
    "doubleclick.net",
    "googlesyndication.com",
    # Mozilla services (in case someone runs Firefox through the proxy)
    "services.mozilla.com",
    "telemetry.mozilla.org",
    "mozilla.net",
    # Apple services (in case Safari or system processes route through)
    "apple.com",
    "icloud.com",
    "mzstatic.com",
})


def _filter_enabled() -> bool:
    return os.environ.get("MINIBURP_NOISE_FILTER", "on").lower() != "off"


def is_noise(host: str) -> bool:
    """True if the host matches a known background-chatter pattern."""
    if not _filter_enabled():
        return False
    h = host.lower().strip()
    if not h:
        return False
    if h in _NOISE_SUFFIXES:
        return True
    return any(h.endswith("." + suf) for suf in _NOISE_SUFFIXES)
