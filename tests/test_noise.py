"""
Tests for the chatter / noise filter (noise.py).

The noise filter is a hard barrier between Chrome's background traffic and
the proxy history. Lab targets must never be classified as noise; known
chatter sources (Google services, Mozilla telemetry, Apple system services)
always must be.
"""
from __future__ import annotations

import importlib

import pytest

import miniburp.noise as noise_mod


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default to filter-on for every test; individual tests can override."""
    monkeypatch.delenv("MINIBURP_NOISE_FILTER", raising=False)
    importlib.reload(noise_mod)


# ---- chatter classification ------------------------------------------------


@pytest.mark.parametrize("host", [
    "safebrowsing.googleapis.com",
    "update.googleapis.com",
    "edgedl.me.gvt1.com",
    "redirector.gvt1.com",
    "accounts.google.com",
    "mtalk.google.com",
    "clients2.google.com",
    "www.gstatic.com",
    "ssl.gstatic.com",
    "play.google.com",
    "mail.google.com",
    "chromewebstore.googleapis.com",
    "content-autofill.googleapis.com",
    "ogads-pa.clients6.google.com",
    "android.clients.google.com",
])
def test_known_chatter_is_noise(host: str) -> None:
    assert noise_mod.is_noise(host) is True


@pytest.mark.parametrize("host", [
    "0a5a00cd0450b6b980bd8f0900900087.web-security-academy.net",
    "web-security-academy.net",
    "portswigger.net",
    "go.portswigger.net",
    "lab.example.com",
    "example.com",
    "127.0.0.1",
    "localhost",
])
def test_lab_targets_are_not_noise(host: str) -> None:
    assert noise_mod.is_noise(host) is False


def test_bare_suffix_matches() -> None:
    """`google.com` itself counts; anchored suffix match."""
    assert noise_mod.is_noise("google.com") is True


def test_lookalike_does_not_match() -> None:
    """`mygoogle.com` must not match `google.com` (suffix is dot-anchored)."""
    assert noise_mod.is_noise("mygoogle.com") is False
    assert noise_mod.is_noise("evilgooglesyndication.com") is False


def test_case_insensitive() -> None:
    assert noise_mod.is_noise("Accounts.Google.COM") is True


def test_empty_host() -> None:
    assert noise_mod.is_noise("") is False


# ---- env-var override ------------------------------------------------------


def test_filter_off_disables_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIBURP_NOISE_FILTER", "off")
    importlib.reload(noise_mod)
    assert noise_mod.is_noise("safebrowsing.googleapis.com") is False
    assert noise_mod.is_noise("accounts.google.com") is False


def test_filter_on_is_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIBURP_NOISE_FILTER", "on")
    importlib.reload(noise_mod)
    assert noise_mod.is_noise("accounts.google.com") is True
