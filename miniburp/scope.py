"""
Scope config: which hosts count as "in scope" for interception and UI display.

Patterns are simple shell-style globs matched case-insensitively against
the hostname. Examples:

    *.web-security-academy.net
    lab.example.com
    *

When the pattern list is empty, EVERYTHING is treated as in-scope.
"""
from __future__ import annotations

import fnmatch
import threading


class Scope:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._patterns: list[str] = []

    def patterns(self) -> list[str]:
        with self._lock:
            return list(self._patterns)

    def set_patterns(self, patterns: list[str]) -> None:
        cleaned = [p.strip() for p in patterns if p.strip()]
        with self._lock:
            self._patterns = cleaned

    def in_scope(self, host: str) -> bool:
        with self._lock:
            if not self._patterns:
                return True
            host_l = host.lower()
            for p in self._patterns:
                if fnmatch.fnmatchcase(host_l, p.lower()):
                    return True
            return False


SCOPE = Scope()
