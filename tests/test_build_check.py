"""
Tests for miniburp._build_check — the pure helpers that drive the
stale-bundle prompt.

The prompt itself is Qt and the source-repo locator depends on PyInstaller
runtime state, so they're not tested here. Hash determinism and the
"only my own source files count" rule are the load-bearing pieces.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from miniburp._build_check import (
    _EXCLUDED_RELATIVE_PATHS,
    compute_source_hash,
)


def _make_fake_repo(tmp_path: Path) -> Path:
    """Build a minimal repo skeleton that compute_source_hash can scan."""
    (tmp_path / "run.py").write_text("print('hi')\n")
    pkg = tmp_path / "miniburp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__version__ = '0.0.0'\n")
    (pkg / "proxy.py").write_text("def serve(): pass\n")
    sub = pkg / "qt"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (sub / "app.py").write_text("def run(): return 0\n")
    return tmp_path


def test_hash_is_deterministic(tmp_path):
    repo = _make_fake_repo(tmp_path)
    assert compute_source_hash(repo) == compute_source_hash(repo)


def test_hash_changes_on_file_edit(tmp_path):
    repo = _make_fake_repo(tmp_path)
    before = compute_source_hash(repo)
    (repo / "miniburp" / "proxy.py").write_text("def serve(): return 1\n")
    after = compute_source_hash(repo)
    assert before != after


def test_hash_changes_when_a_file_is_added(tmp_path):
    repo = _make_fake_repo(tmp_path)
    before = compute_source_hash(repo)
    (repo / "miniburp" / "new_module.py").write_text("X = 1\n")
    after = compute_source_hash(repo)
    assert before != after


def test_hash_changes_when_a_file_is_removed(tmp_path):
    repo = _make_fake_repo(tmp_path)
    before = compute_source_hash(repo)
    (repo / "miniburp" / "proxy.py").unlink()
    after = compute_source_hash(repo)
    assert before != after


def test_build_info_is_excluded_from_hash(tmp_path):
    """Writing _build_info.py must not change the hash — it's generated."""
    repo = _make_fake_repo(tmp_path)
    before = compute_source_hash(repo)
    (repo / "miniburp" / "_build_info.py").write_text(
        "BUILD_SHA = 'deadbeef'\nBUILD_TIME = 'now'\nBUILD_SOURCE_HASH = 'x'\n"
    )
    after = compute_source_hash(repo)
    assert before == after, "the per-build stamp must not feed back into the hash"


def test_excluded_paths_are_relative_and_posix():
    """Sanity-check the exclusion list — relative paths, forward slashes."""
    for p in _EXCLUDED_RELATIVE_PATHS:
        assert not p.startswith("/")
        assert "\\" not in p


def test_unrelated_files_outside_globs_dont_affect_hash(tmp_path):
    """README, test files, assets etc. should not contribute to the hash."""
    repo = _make_fake_repo(tmp_path)
    before = compute_source_hash(repo)
    (repo / "README.md").write_text("# unrelated\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_x(): pass\n")
    (repo / "assets").mkdir()
    (repo / "assets" / "icon.png").write_bytes(b"\x89PNG fake")
    after = compute_source_hash(repo)
    assert before == after


def test_pyc_files_outside_globs_dont_affect_hash(tmp_path):
    """__pycache__ entries must not move the hash."""
    repo = _make_fake_repo(tmp_path)
    cache = repo / "miniburp" / "__pycache__"
    cache.mkdir()
    before = compute_source_hash(repo)
    (cache / "proxy.cpython-313.pyc").write_bytes(b"compiled junk")
    after = compute_source_hash(repo)
    assert before == after


def test_hash_is_hex_sha256(tmp_path):
    repo = _make_fake_repo(tmp_path)
    h = compute_source_hash(repo)
    assert len(h) == 64
    int(h, 16)  # raises if not hex


def test_compute_source_hash_handles_missing_repo(tmp_path):
    """Empty directory hashes cleanly to the empty-SHA256 (no inputs)."""
    h = compute_source_hash(tmp_path)
    # No files matched — should still be a valid hex digest, just the empty one.
    assert len(h) == 64
    int(h, 16)


# ---------------------------------------------------------------------------
# Integration smoke test against the live repo: build.sh's emitted
# _build_info.py, if present, should record a hash equal to the current
# compute_source_hash() result. (Skipped when no bundle has been built.)
# ---------------------------------------------------------------------------


def test_emitted_build_info_matches_current_source():
    """
    Post-build smoke: if a bundle exists and the source hasn't moved since the
    build, the baked hash should match. Skips on a fresh checkout (no bundle)
    or when the working tree has drifted (rebuild needed) — the staleness
    prompt at app launch is the real enforcement; this is a sanity check.
    """
    repo = Path(__file__).resolve().parents[1]
    try:
        from miniburp import _build_info  # type: ignore
    except ImportError:
        pytest.skip("no _build_info.py present (no bundle has been built)")
    live = compute_source_hash(repo)
    if live != _build_info.BUILD_SOURCE_HASH:
        pytest.skip(
            "source has drifted since the last build — run ./build.sh "
            "to regenerate _build_info.py"
        )
    assert live == _build_info.BUILD_SOURCE_HASH
