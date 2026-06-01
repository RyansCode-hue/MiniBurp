"""
Render a captured request as a copy-pasteable ``curl`` command.

Used by the Proxy history "Copy as curl" action. Kept headless and pure so it
can be unit-tested without Qt and reused by the legacy Flask UI if wanted.
"""
from __future__ import annotations

import shlex


def to_curl(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> str:
    """Build a multi-line ``curl`` invocation reproducing the request.

    * ``-X`` is emitted only when the method isn't a plain bodyless GET.
    * Every header is forwarded as ``-H`` except ``Content-Length`` — curl
      recomputes that from ``--data-raw``, and forwarding a stale value would
      desync the request the same way a hand-edit does.
    * A body is emitted with ``--data-raw`` (single-quoted, so ``curl`` doesn't
      interpret ``@`` / globs the way ``--data`` would).
    """
    headers = headers or {}
    parts: list[str] = ["curl"]

    m = (method or "GET").upper()
    if m != "GET" or body:
        parts.append(f"-X {m}")

    parts.append(shlex.quote(url))

    for name, value in headers.items():
        if name.lower() == "content-length":
            continue
        parts.append("-H " + shlex.quote(f"{name}: {value}"))

    if body:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("iso-8859-1")
        parts.append("--data-raw " + shlex.quote(text))

    return " \\\n  ".join(parts)
