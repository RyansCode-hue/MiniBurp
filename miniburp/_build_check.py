"""
Bundle-staleness detection.

A PyInstaller bundle has no idea what the source tree looks like — by design.
But in this repo's dev workflow the bundle lives at `<repo>/dist/MiniBurp.app`
right next to the source, and forgetting to rebuild after a code change leaks
old behavior (stale Chrome flags, missing chatter filter, etc.). This module
lets the bundle detect that scenario and prompt the user.

How it works
------------
At build time, `build.sh` writes `miniburp/_build_info.py` containing the git
SHA at build time and a hash of every source `.py` file. At app startup,
``check_for_staleness()`` recomputes the same hash against the current source
tree (located via heuristic or ``MINIBURP_SOURCE_DIR``) and reports any
divergence.

Running from source? `_build_info` won't exist and the check no-ops.
Bundle copied to `/Applications` or another machine? Source path isn't
locatable and the check no-ops. The check is precise: false positives mean
nagging the user; the heuristic is intentionally conservative.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Files in the source tree that contribute to the hash. We intentionally keep
# this small and explicit so adding e.g. a docs folder doesn't trigger the
# prompt — only files that actually get bundled count.
_SOURCE_GLOBS: tuple[str, ...] = (
    "run.py",
    "miniburp/**/*.py",
)

# `_build_info.py` is itself generated and varies per build; including it
# would make the hash non-deterministic across rebuilds-with-no-changes.
_EXCLUDED_RELATIVE_PATHS: frozenset[str] = frozenset({
    "miniburp/_build_info.py",
})


@dataclass(frozen=True)
class StalenessReport:
    """Returned by check_for_staleness when bundle and source diverge."""
    build_sha: str
    build_time: str
    build_source_hash: str
    current_source_hash: str
    head_sha: Optional[str]
    source_root: Path

    @property
    def reason(self) -> str:
        """Human-readable summary of why the bundle is considered stale."""
        if self.head_sha and self.head_sha != self.build_sha:
            return (
                f"Source tree is now at {self.head_sha[:7]} (bundle built from "
                f"{self.build_sha[:7]})."
            )
        return (
            "Source tree has uncommitted changes that aren't in this bundle."
        )


def compute_source_hash(repo_root: Path) -> str:
    """
    Deterministic SHA-256 of every file matched by `_SOURCE_GLOBS`.

    Hashes the relative path and contents of each file in sorted order so the
    same source tree always produces the same digest regardless of filesystem
    enumeration order.
    """
    files: list[Path] = []
    for pattern in _SOURCE_GLOBS:
        files.extend(repo_root.glob(pattern))

    seen: set[str] = set()
    unique: list[tuple[str, Path]] = []
    for f in files:
        try:
            rel = f.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            continue  # outside the repo; skip
        if rel in _EXCLUDED_RELATIVE_PATHS or rel in seen:
            continue
        if not f.is_file():
            continue
        seen.add(rel)
        unique.append((rel, f))

    unique.sort(key=lambda pair: pair[0])

    h = hashlib.sha256()
    for rel, path in unique:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(path.read_bytes())
        except OSError:
            # Unreadable file — fold its name in but not contents, so the hash
            # still changes if it later becomes readable.
            pass
        h.update(b"\0")
    return h.hexdigest()


def find_source_repo() -> Optional[Path]:
    """
    Locate the source repo that corresponds to the currently-running bundle.

    Strategy:
      1. ``$MINIBURP_SOURCE_DIR`` — explicit override.
      2. If we're a PyInstaller bundle and the binary lives at
         ``<repo>/dist/MiniBurp.app/Contents/MacOS/MiniBurp``, the repo root
         is four levels up. Verify by checking for ``.git`` and
         ``miniburp/__init__.py``.

    Returns ``None`` when running from source (no bundle to compare) or when
    the bundle has been moved away from its source repo.
    """
    env = os.environ.get("MINIBURP_SOURCE_DIR")
    if env:
        p = Path(env).expanduser().resolve()
        if (p / ".git").exists() and (p / "miniburp" / "__init__.py").is_file():
            return p
        return None

    # PyInstaller sets `sys.frozen = True` on the bundled bootloader.
    if not getattr(sys, "frozen", False):
        return None

    try:
        exe = Path(sys.executable).resolve()
    except OSError:
        return None

    parts = exe.parts
    try:
        idx = parts.index("MiniBurp.app")
    except ValueError:
        return None
    # Expected layout: .../<repo>/dist/MiniBurp.app/Contents/MacOS/MiniBurp
    if idx < 2 or parts[idx - 1] != "dist":
        return None

    repo = Path(*parts[: idx - 1])
    if not (repo / ".git").exists():
        return None
    if not (repo / "miniburp" / "__init__.py").is_file():
        return None
    return repo


def _git_head(repo: Path) -> Optional[str]:
    """Best-effort `git rev-parse HEAD`. None on any failure."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    sha = out.stdout.decode("ascii", errors="replace").strip()
    return sha or None


def check_for_staleness() -> Optional[StalenessReport]:
    """
    Return a StalenessReport if this bundle is out of date relative to the
    live source tree. Returns None when:

    * `MINIBURP_BUNDLE_CHECK=off` — escape hatch
    * `_build_info` isn't present (running from source, not a bundle)
    * the source repo can't be located (bundle moved away from repo)
    * the bundle and source agree

    Designed to never raise — the caller is the Qt startup path and any
    exception here would block app launch.
    """
    if os.environ.get("MINIBURP_BUNDLE_CHECK", "on").lower() == "off":
        return None

    try:
        from . import _build_info  # type: ignore
    except ImportError:
        return None  # running from source

    repo = find_source_repo()
    if repo is None:
        return None

    try:
        current_hash = compute_source_hash(repo)
    except Exception:
        return None

    build_hash = getattr(_build_info, "BUILD_SOURCE_HASH", "")
    if not build_hash or current_hash == build_hash:
        return None

    return StalenessReport(
        build_sha=getattr(_build_info, "BUILD_SHA", "unknown"),
        build_time=getattr(_build_info, "BUILD_TIME", "unknown"),
        build_source_hash=build_hash,
        current_source_hash=current_hash,
        head_sha=_git_head(repo),
        source_root=repo,
    )
