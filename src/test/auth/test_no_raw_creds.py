"""
Iron law #12 enforcement: NOTHING outside ``sso/authorizer.py`` may read
``credentials.txt``, hardcode ``Path.home()``, or touch a ``session.json``
file directly. This test greps the source tree for violations.

Exemptions are per file **and** per pattern (see ``EXEMPTIONS``): an exempt
file is only excused for the named pattern(s), and an exemption that is no
longer load-bearing fails ``test_exemptions_are_live``. A stale exemption is
worse than none — it whitelists the whole file, so a real regression in it
would pass CI silently.

History (2026-09-21): ``webui/loader.py`` and ``bb/download.py`` were removed
from the list for exactly that reason. ``loader.py`` had been excused for
``Path.home() / ".config" / "sustech_survival" / "webui" / "skins"``, a path
deleted in 2026.8.25 (user skins now live at ``config_root() / "skins"``, i.e.
``$SUSTECH_HOME/.sustech_survival/skins``); ``bb/download.py`` now defaults to
``config_root() / "downloads" / <kind>``. Keeping either exemption would have
hidden the very rule this test exists to protect: user data belongs under the
SUSTech home dot-directory, never under an XDG ``~/.config``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "sustech_survival"

# Patterns that violate iron law #12 when found OUTSIDE exempted files.
# Each is (stable_key, regex, description).
VIOLATIONS: list[tuple[str, str, str]] = [
    # Raw credential file reads — open("*credentials*")
    ("creds-open", r'open\s*\([^)]*credentials',
     "Direct open() of credentials file — use Authorizer._read_creds()"),
    # Path.home() followed by / — breaks on non-default HOME and escapes the
    # SUSTech home dot-directory. The regex requires the trailing / to
    # distinguish real code from docstring mentions.
    ("home-path", r'Path\.home\s*\(\s*\)\s*/',
     "Path.home()/ — breaks on non-default HOME. Use sustech_survival._cache.config_root()."),
    # session.json literal in code (not comments) — disk-persisted sessions are
    # the anti-pattern
    ("session-json", r'["\']session\.json["\']',
     "session.json disk persistence — use Authorizer in-memory TTL (iron law #12)"),
]

# Files ALLOWED to contain credential/session patterns, scoped to the named
# pattern keys (``None`` = every pattern; reserved for the canonical accessor).
# Each entry is (relative_path): (allowed_pattern_keys, reason).
EXEMPTIONS: dict[str, tuple[frozenset[str] | None, str]] = {
    "sso/authorizer.py": (
        None,
        "The one accessor — _read_creds, _resolve_skill_dir, _creds_file",
    ),
    "sso/authlib/rsc_inject.py": (
        frozenset({"session-json"}),
        "Legacy Playwright cookie bridge — prefers Authorizer in-memory, falls back to file",
    ),
    "lib/booking/auth.py": (
        frozenset({"session-json"}),
        "_save_session/refresh_from_disk are no-op stubs for backward compat",
    ),
    "bb/session.py": (
        frozenset({"session-json"}),
        "SESSION_FILE is vestigial, marked # legacy; auth goes through BBAuth",
    ),
}

# Exemptions kept as documentation rather than to excuse live code: the file is
# the canonical implementation and is expected to stay clean.
DOCUMENTARY_EXEMPTIONS = frozenset({"sso/authorizer.py"})


def _rel(path: Path) -> str:
    """Repo-relative posix-style path, so exemption keys match on every OS."""
    return str(path.relative_to(SRC_DIR)).replace("\\", "/")


def _scan_file(path: Path, *, ignore_exemptions: bool = False) -> list[tuple[str, int, str, str]]:
    """Scan one file for violations.

    Returns [(pattern_key, line_num, line_text, rel_path)]. An exempt file is
    scanned only for the patterns it is **not** excused for; with
    ``ignore_exemptions=True`` every pattern is applied even to exempt files,
    which is how the exemption tests check their own honesty.
    """
    rel = _rel(path)
    allowed = EXEMPTIONS.get(rel, (frozenset(), ""))[0]
    if rel in EXEMPTIONS and not ignore_exemptions and allowed is None:
        return []

    hits: list[tuple[str, int, str, str]] = []
    for line_num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        # Skip comment-only lines (heuristic — # or // at start after whitespace)
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        for key, pattern, _desc in VIOLATIONS:
            if not ignore_exemptions and allowed is not None and key in allowed:
                continue
            if re.search(pattern, line):
                hits.append((key, line_num, line.rstrip(), rel))
    return hits


_DESCRIPTION = {key: desc for key, _pattern, desc in VIOLATIONS}


def test_no_raw_credential_reads():
    """No file outside sso/authorizer.py may open credentials.txt directly."""
    all_hits: list[tuple[str, int, str, str]] = []
    for f in sorted(SRC_DIR.rglob("*.py")):
        all_hits.extend(_scan_file(f))

    if all_hits:
        lines = [f"  {h[3]}:{h[1]} — {_DESCRIPTION[h[0]]}" for h in all_hits]
        msg = (
            f"\n❌ Iron law #12 violations found ({len(all_hits)}):\n"
            + "\n".join(lines)
            + "\n\nUse Authorizer._read_creds() / TISAuth().ensure() instead."
        )
        pytest.fail(msg)


def test_exemptions_are_live():
    """Every exemption must point at a real file and still excuse something.

    Remove an exemption together with the code it excused; a left-behind entry
    silently whitelists a whole file.
    """
    for rel, (patterns, reason) in EXEMPTIONS.items():
        path = SRC_DIR / rel
        assert path.exists(), f"exemption points at a missing file: {rel}"
        assert reason.strip(), f"exemption without a reason: {rel}"
        if rel in DOCUMENTARY_EXEMPTIONS:
            continue
        assert _scan_file(path, ignore_exemptions=True), (
            f"stale exemption: {rel} no longer contains any of the "
            f"{sorted(k for k, _p, _d in VIOLATIONS)} patterns — remove it from "
            f"EXEMPTIONS (reason on file: {reason!r})"
        )
        assert patterns, f"exemption without scoped patterns: {rel}"


def test_exemptions_do_not_hide_other_violations():
    """A scoped exemption must not cover patterns it was not written for.

    e.g. ``bb/session.py`` is excused for its vestigial ``session.json``
    constant only — a ``Path.home()/`` there is still a violation.
    """
    for rel, (patterns, reason) in EXEMPTIONS.items():
        if patterns is None:
            continue
        found = {key for key, _n, _l, _r in _scan_file(SRC_DIR / rel, ignore_exemptions=True)}
        stray = found - set(patterns)
        assert not stray, (
            f"{rel} is exempted for {sorted(patterns)} but also hits "
            f"{sorted(stray)} (reason on file: {reason!r})"
        )
