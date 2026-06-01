"""
Match & Replace rules — standing transforms applied to every in-scope request
before it's forwarded, the equivalent of Burp's Proxy > Match and Replace.

Lets you inject a header on every request (e.g. ``X-Forwarded-For`` for IP-based
access labs), rewrite a cookie, or swap a path/parameter without hand-editing
each request through Intercept.

Scope is intentionally REQUEST-only. Response bodies are frequently gzipped, so
a raw find/replace would silently miss — rather than ship something that fails
quietly, response rules are left for a future pass that decodes first.

Kept headless (no Qt) so the engine is unit-tested directly and the proxy can
import it without dragging in the UI.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass

# (key, human label). The key selects which segment of the request a rule edits.
TARGETS = [
    ("req-line", "Request line"),
    ("req-header", "Request headers"),
    ("req-body", "Request body"),
]
_TARGET_KEYS = {k for k, _ in TARGETS}


@dataclass
class Rule:
    target: str          # one of _TARGET_KEYS
    match: str           # literal substring, or regex when is_regex
    replacement: str
    is_regex: bool = False
    enabled: bool = True


def _apply_one(segment: bytes, rule: Rule) -> bytes:
    """Apply a single rule to one request segment (bytes in, bytes out)."""
    if rule.is_regex:
        try:
            pattern = rule.match.encode("iso-8859-1")
            repl = rule.replacement.encode("iso-8859-1")
            return re.sub(pattern, repl, segment)
        except re.error:
            return segment  # invalid pattern is a no-op; UI surfaces the error
    needle = rule.match.encode("iso-8859-1")
    if not needle:
        return segment  # empty literal match would be meaningless
    return segment.replace(needle, rule.replacement.encode("iso-8859-1"))


def _split_request(raw: bytes) -> tuple[bytes, bytes, bytes]:
    """Return (request_line, header_lines, body). header_lines excludes the
    request line and the trailing blank line; body is everything after the
    blank line (empty if none)."""
    split = raw.find(b"\r\n\r\n")
    if split == -1:
        head, body = raw, b""
    else:
        head, body = raw[:split], raw[split + 4:]
    nl = head.find(b"\r\n")
    if nl == -1:
        return head, b"", body
    return head[:nl], head[nl + 2:], body


def _join_request(line: bytes, headers: bytes, body: bytes, had_blank: bool) -> bytes:
    head = line if not headers else line + b"\r\n" + headers
    if had_blank:
        return head + b"\r\n\r\n" + body
    return head


class Ruleset:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rules: list[Rule] = []

    def rules(self) -> list[Rule]:
        with self._lock:
            return list(self._rules)

    def set_rules(self, rules: list[Rule]) -> None:
        with self._lock:
            self._rules = list(rules)

    def has_active_request_rules(self) -> bool:
        with self._lock:
            return any(r.enabled and r.target in _TARGET_KEYS for r in self._rules)

    def apply_request(self, raw: bytes) -> bytes:
        """Run every enabled rule against ``raw`` in order; return the result.

        Rules compose: each sees the output of the previous one. A rule whose
        target segment doesn't contain the match is a no-op.
        """
        with self._lock:
            active = [r for r in self._rules if r.enabled and r.target in _TARGET_KEYS]
        if not active:
            return raw

        line, headers, body = _split_request(raw)
        had_blank = b"\r\n\r\n" in raw
        for rule in active:
            if rule.target == "req-line":
                line = _apply_one(line, rule)
            elif rule.target == "req-header":
                headers = _apply_one(headers, rule)
            elif rule.target == "req-body":
                body = _apply_one(body, rule)
        return _join_request(line, headers, body, had_blank)


# Single global ruleset shared by the proxy and the UI.
RULES = Ruleset()
