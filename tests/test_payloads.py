"""
Tests for the bundled payload library (payloads.py).
"""
from __future__ import annotations

from miniburp import payloads


def test_lists_have_unique_keys_and_content():
    keys = [pl.key for pl in payloads.LISTS]
    assert len(keys) == len(set(keys))  # no dupes
    for pl in payloads.LISTS:
        assert pl.name
        assert pl.description
        assert pl.payloads, f"{pl.key} has no payloads"


def test_path_traversal_list_includes_the_lab_payload():
    pl = payloads.get("path-traversal")
    assert pl is not None
    assert "../../../etc/passwd" in pl.payloads


def test_names_and_payloads_for_round_trip():
    names = payloads.names()
    assert ("path-traversal", "Path traversal") in names
    for key, _name in names:
        assert payloads.payloads_for(key) == payloads.get(key).payloads


def test_unknown_key_is_empty_not_error():
    assert payloads.get("nope") is None
    assert payloads.payloads_for("nope") == []
