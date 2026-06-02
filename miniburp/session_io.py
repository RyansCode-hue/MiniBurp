"""
Serialize / deserialize captured transactions to a portable JSON file.

History lives only in memory and vanishes on restart, which is friction for
lab work spanning a session and makes sharing a capture impossible. This module
round-trips the STORE to a JSON document (raw byte fields base64-encoded so they
survive cleanly) so the user can save and reload, or hand a capture to someone
reproducing a finding.

Kept headless (no Qt, no socket) so it's unit-testable and the Flask UI could
reuse it. ``Store.load`` consumes the dicts that ``load_transactions`` returns.

Note: saved files contain full request/response bodies, which may include
credentials or session tokens — they are not meant to be committed to a repo.
"""
from __future__ import annotations

import base64
import json

FORMAT_VERSION = 1

# Transaction fields stored verbatim as JSON scalars.
_SCALAR_FIELDS = (
    "method", "scheme", "host", "port", "path", "status",
    "started_at", "finished_at", "error",
)
# Byte fields base64-encoded for a clean round-trip.
_BYTE_FIELDS = ("req_body", "resp_body", "raw_request", "raw_response")


def _tx_to_dict(tx) -> dict:
    d = {f: getattr(tx, f) for f in _SCALAR_FIELDS}
    d["req_headers"] = dict(tx.req_headers or {})
    d["resp_headers"] = dict(tx.resp_headers or {})
    for f in _BYTE_FIELDS:
        d[f] = base64.b64encode(getattr(tx, f) or b"").decode("ascii")
    return d


def dump_transactions(txs) -> bytes:
    """Serialize an iterable of Transaction objects to JSON bytes."""
    payload = {
        "version": FORMAT_VERSION,
        "transactions": [_tx_to_dict(t) for t in txs],
    }
    return json.dumps(payload, indent=2).encode("utf-8")


def load_transactions(data: bytes) -> list[dict]:
    """Parse a saved session into a list of ``Store.add``-compatible kwargs.

    Raises ValueError on a document that isn't a recognizable session file.
    """
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"not a valid session file: {e}") from e
    if not isinstance(payload, dict) or "transactions" not in payload:
        raise ValueError("not a MiniBurp session file (missing 'transactions')")

    out: list[dict] = []
    for d in payload["transactions"]:
        item = {f: d.get(f) for f in _SCALAR_FIELDS}
        item["req_headers"] = d.get("req_headers") or {}
        item["resp_headers"] = d.get("resp_headers") or {}
        for f in _BYTE_FIELDS:
            item[f] = base64.b64decode(d.get(f) or "")
        out.append(item)
    return out
