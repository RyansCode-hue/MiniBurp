"""
Tests for the host allowlist (scope.py).
"""
from __future__ import annotations

from miniburp.scope import Scope


def test_empty_scope_matches_everything() -> None:
    s = Scope()
    assert s.in_scope("anything.example.com") is True
    assert s.in_scope("127.0.0.1") is True


def test_exact_host_match() -> None:
    s = Scope()
    s.set_patterns(["lab.example.com"])
    assert s.in_scope("lab.example.com") is True
    assert s.in_scope("other.example.com") is False


def test_glob_pattern() -> None:
    s = Scope()
    s.set_patterns(["*.web-security-academy.net"])
    assert s.in_scope("0a1b.web-security-academy.net") is True
    assert s.in_scope("foo.bar.web-security-academy.net") is True
    assert s.in_scope("portswigger.net") is False


def test_case_insensitive() -> None:
    s = Scope()
    s.set_patterns(["LAB.example.com"])
    assert s.in_scope("lab.example.com") is True
    assert s.in_scope("LAB.EXAMPLE.COM") is True


def test_set_patterns_strips_blank_lines() -> None:
    s = Scope()
    s.set_patterns(["  ", "lab.example.com", "", "  *.foo.com  "])
    patterns = s.patterns()
    assert patterns == ["lab.example.com", "*.foo.com"]


def test_multiple_patterns_any_match() -> None:
    s = Scope()
    s.set_patterns(["lab.a.com", "*.b.com"])
    assert s.in_scope("lab.a.com") is True
    assert s.in_scope("anything.b.com") is True
    assert s.in_scope("c.com") is False
