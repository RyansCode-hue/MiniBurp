"""
Reflection helpers — extract the input values from a request so the response
viewer can highlight where they echo back.

Seeing your input reflected is the core "aha" of XSS / SSRF / open-redirect /
traversal labs. These pure functions pull candidate tokens out of a request
(path segments, query values, urlencoded body values) and test which of them
appear in a response, so the UI can tint them and show an "N reflected" badge.

Kept headless and tested directly; the Qt layer only does the highlighting.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

# Below this length tokens are too noisy to be meaningful reflections.
_MIN_LEN = 3


def request_tokens(raw: bytes) -> list[str]:
    """Candidate reflected inputs from a raw request, de-duped and de-noised.

    Pulls path segments, query-string values, and urlencoded body values.
    Drops tokens shorter than 3 chars and pure numbers (status codes, ids,
    counts) which would highlight everywhere.
    """
    split = raw.find(b"\r\n\r\n")
    head = raw[:split] if split != -1 else raw
    body = raw[split + 4:] if split != -1 else b""

    tokens: set[str] = set()
    lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
    if lines:
        parts = lines[0].split(" ")
        if len(parts) >= 2:
            sp = urlsplit(parts[1])
            for seg in sp.path.split("/"):
                if seg:
                    tokens.add(seg)
            for _k, v in parse_qsl(sp.query):
                if v:
                    tokens.add(v)

    body_text = body.decode("iso-8859-1", errors="replace")
    # Only treat a single-line key=value blob as a urlencoded form.
    if "=" in body_text and "\n" not in body_text.strip():
        for _k, v in parse_qsl(body_text):
            if v:
                tokens.add(v)

    out = []
    for t in tokens:
        t = t.strip()
        if len(t) < _MIN_LEN or t.isdigit():
            continue
        out.append(t)
    # Longest first so a value isn't masked by a shorter substring of itself.
    out.sort(key=len, reverse=True)
    return out


def reflected(needles: list[str], haystack: str) -> list[str]:
    """Subset of ``needles`` that appear in ``haystack``."""
    return [n for n in needles if n and n in haystack]
