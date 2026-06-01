"""
Tests for session_io (save/restore) and Store.load.
"""
from __future__ import annotations

import pytest

from miniburp.session_io import dump_transactions, load_transactions
from miniburp.storage import Store, Transaction


def _sample_txs() -> list[Transaction]:
    a = Transaction(
        id=1, method="GET", scheme="https", host="lab.net", port=443,
        path="/image?filename=10.jpg", status=200,
        req_headers={"Host": "lab.net"}, req_body=b"",
        resp_headers={"Content-Type": "image/jpeg"}, resp_body=b"\xff\xd8\xff binary",
        raw_request=b"GET /image?filename=10.jpg HTTP/1.1\r\nHost: lab.net\r\n\r\n",
        raw_response=b"HTTP/1.1 200 OK\r\n\r\n\xff\xd8\xff binary",
        finished_at=123.0, error=None,
    )
    b = Transaction(
        id=2, method="POST", scheme="http", host="lab.net", port=80,
        path="/login", status=None,
        req_headers={"Host": "lab.net", "Content-Length": "5"}, req_body=b"abcde",
        raw_request=b"POST /login HTTP/1.1\r\nHost: lab.net\r\n\r\nabcde",
        error="connect failed",
    )
    return [a, b]


def test_round_trip_preserves_fields():
    txs = _sample_txs()
    items = load_transactions(dump_transactions(txs))
    assert len(items) == 2

    first = items[0]
    assert first["method"] == "GET"
    assert first["scheme"] == "https"
    assert first["host"] == "lab.net"
    assert first["port"] == 443
    assert first["status"] == 200
    assert first["req_headers"] == {"Host": "lab.net"}
    # Binary bytes survive base64 round-trip exactly.
    assert first["resp_body"] == b"\xff\xd8\xff binary"
    assert first["raw_request"].startswith(b"GET /image")

    second = items[1]
    assert second["status"] is None
    assert second["error"] == "connect failed"
    assert second["req_body"] == b"abcde"


def test_loaded_items_reconstruct_transactions():
    items = load_transactions(dump_transactions(_sample_txs()))
    # Each item is valid kwargs for Transaction (id is assigned by the Store).
    tx = Transaction(id=99, **items[0])
    assert tx.url == "https://lab.net/image?filename=10.jpg"


def test_bad_data_raises_valueerror():
    with pytest.raises(ValueError):
        load_transactions(b"not json")
    with pytest.raises(ValueError):
        load_transactions(b'{"something": "else"}')


def test_store_load_repopulates_and_publishes():
    store = Store()
    store.add(method="GET", scheme="http", host="old.example", port=80, path="/")
    q = store.subscribe()

    items = load_transactions(dump_transactions(_sample_txs()))
    store.load(items)

    # History is exactly the restored set, with fresh sequential ids.
    all_txs = store.all()
    assert [t.host for t in all_txs] == ["lab.net", "lab.net"]
    assert [t.id for t in all_txs] == [1, 2]
    assert all_txs[0].status == 200

    # Subscriber saw a clear then one add per restored tx.
    kinds = []
    while not q.empty():
        kinds.append(q.get_nowait()["kind"])
    assert kinds[0] == "clear"
    assert kinds.count("add") == 2


def test_store_load_survives_null_started_at():
    """A hand-edited/corrupt session file with a null started_at must not crash
    the load (summary() -> duration_ms used to raise on None - None)."""
    store = Store()
    items = load_transactions(dump_transactions(_sample_txs()))
    items[0]["started_at"] = None  # simulate a malformed/older file
    items[0]["finished_at"] = 5.0
    store.load(items)  # would raise TypeError before the duration_ms guard
    assert len(store.all()) == 2
    assert store.all()[0].duration_ms is None
