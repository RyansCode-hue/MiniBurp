"""
Tests for browser.build_command — the Chromium argv assembler.

The flag set is load-bearing: every chatter-suppression flag is required to
keep MiniBurp's history showing only lab traffic. Removing one silently
brings back Google background traffic, so we lock the flag list down here.
"""
from __future__ import annotations

import pytest

from miniburp.browser import build_command


@pytest.fixture
def cmd() -> list[str]:
    return build_command(
        exe="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        proxy_host="127.0.0.1",
        proxy_port=8080,
        user_data_dir="/tmp/miniburp-chrome-xyz",
        start_url=None,
    )


def test_first_arg_is_executable(cmd: list[str]) -> None:
    assert cmd[0].endswith("Google Chrome")


def test_proxy_flag_present(cmd: list[str]) -> None:
    assert "--proxy-server=http://127.0.0.1:8080" in cmd


def test_user_data_dir_flag_present(cmd: list[str]) -> None:
    assert "--user-data-dir=/tmp/miniburp-chrome-xyz" in cmd


def test_loopback_bypass_present(cmd: list[str]) -> None:
    assert "--proxy-bypass-list=<-loopback>" in cmd


def test_spki_flag_is_gone(cmd: list[str]) -> None:
    """The SPKI-list flag was replaced by OS-trust install. Must not return."""
    assert not any("ignore-certificate-errors" in a for a in cmd)
    assert not any("spki" in a.lower() for a in cmd)


@pytest.mark.parametrize("flag", [
    "--disable-background-networking",
    "--disable-sync",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-domain-reliability",
    "--disable-client-side-phishing-detection",
    "--metrics-recording-only",
    "--no-pings",
    "--safebrowsing-disable-auto-update",
])
def test_chatter_suppression_flag_present(cmd: list[str], flag: str) -> None:
    assert flag in cmd


@pytest.mark.parametrize("feature", [
    "Translate",
    "OptimizationHints",
    "OptimizationGuideModelDownloading",
    "MediaRouter",
    "InterestFeedContentSuggestions",
])
def test_disable_features_includes(cmd: list[str], feature: str) -> None:
    df = next((a for a in cmd if a.startswith("--disable-features=")), None)
    assert df is not None, "no --disable-features= flag found"
    features = df.split("=", 1)[1].split(",")
    assert feature in features


def test_start_url_appended_when_provided() -> None:
    cmd = build_command(
        exe="chrome",
        proxy_host="127.0.0.1",
        proxy_port=8080,
        user_data_dir="/tmp/x",
        start_url="https://portswigger.net/web-security",
    )
    assert cmd[-1] == "https://portswigger.net/web-security"


def test_start_url_not_appended_when_none(cmd: list[str]) -> None:
    assert not any(a.startswith("http") and "://" in a for a in cmd if not a.startswith("--"))
