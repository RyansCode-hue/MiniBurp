"""
Tests for Transaction.decoded_resp_body memoization + cache invalidation.

The history-search feature relies on this so 'csrf-token' matches gzipped
responses, not just identity bodies.
"""
from __future__ import annotations

import gzip

from miniburp.storage import Store


def _payload(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"))


def test_decoded_resp_body_decodes_gzip_and_memoizes():
    store = Store()
    body = _payload("hello carlos")
    tx = store.add(
        method="GET", scheme="https", host="h.test", port=443, path="/",
        req_headers={}, req_body=b"",
        raw_request=b"", raw_response=b"",
    )
    store.update(
        tx.id,
        resp_body=body,
        resp_headers={"Content-Encoding": "gzip"},
    )
    # First call decodes...
    assert b"hello carlos" in tx.decoded_resp_body()
    cached = tx._decoded_resp_body
    assert cached is not None
    # ...and second call returns the same object (memoized).
    assert tx.decoded_resp_body() is cached


def test_update_invalidates_decoded_cache_when_body_changes():
    store = Store()
    tx = store.add(
        method="GET", scheme="https", host="h.test", port=443, path="/",
        req_headers={}, req_body=b"",
        raw_request=b"", raw_response=b"",
    )
    store.update(
        tx.id,
        resp_body=_payload("first"),
        resp_headers={"Content-Encoding": "gzip"},
    )
    assert tx.decoded_resp_body() == b"first"

    store.update(tx.id, resp_body=_payload("second"))
    # Cache should have been dropped, so we re-decode and get the new body.
    assert tx.decoded_resp_body() == b"second"


def test_decoded_resp_body_falls_back_to_raw_on_identity():
    store = Store()
    tx = store.add(
        method="GET", scheme="http", host="h.test", port=80, path="/",
        req_headers={}, req_body=b"",
        raw_request=b"", raw_response=b"",
    )
    store.update(tx.id, resp_body=b"plaintext body", resp_headers={})
    assert tx.decoded_resp_body() == b"plaintext body"


def test_unrelated_update_does_not_invalidate_cache():
    store = Store()
    tx = store.add(
        method="GET", scheme="https", host="h.test", port=443, path="/",
        req_headers={}, req_body=b"",
        raw_request=b"", raw_response=b"",
    )
    store.update(
        tx.id,
        resp_body=_payload("cached"),
        resp_headers={"Content-Encoding": "gzip"},
    )
    _ = tx.decoded_resp_body()
    cached = tx._decoded_resp_body
    # Touching an unrelated field like 'status' must not drop the cache.
    store.update(tx.id, status=200)
    assert tx._decoded_resp_body is cached
