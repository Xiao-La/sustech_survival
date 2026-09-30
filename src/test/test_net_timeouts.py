"""Regression: one central timeout — 180 s — for every layer.

2026-09-10: ``tis/grades.py`` and ``tis/courses.py`` hardcoded ``timeout=15``,
bypassing the config tree. ``sustech tis grades`` therefore died whenever CAS
refresh + fetch exceeded 15 s (looked like a hang). Every request site now
resolves through :mod:`sustech_survival._net`, whose default is 180 s, and this
test keeps it that way.
"""
import pathlib
import re

from sustech_survival import _net

SRC = pathlib.Path(__file__).resolve().parents[1] / "sustech_survival"


def test_defaults_are_180_seconds():
    assert _net.HTTP_DEFAULT == 180.0
    assert _net.LOGIN_DEFAULT == 180.0
    assert _net.PAGE_DEFAULT == 180.0

    snapshot = _net.NetworkTimeouts.from_config({})
    assert snapshot.service_timeout("tis") == 180.0
    assert snapshot.service_login_timeout("bb") == 180.0
    # An unknown service still lands on the section default, not on 0/None.
    assert snapshot.service_timeout("no-such-service") == 180.0
    # The browser layer is a config leaf too, in seconds, exposed in ms.
    assert snapshot.page_timeout() == 180.0
    assert snapshot.page_timeout("no-such-service") == 180.0
    assert snapshot.page_timeout_ms() == 180_000


def test_config_json_overrides_win():
    snapshot = _net.NetworkTimeouts.from_config(
        {
            "http": {"default": 45},
            "login": {"default": 50},
            "page": {"default": 300},
            "services": {"tis": {"http": 90, "login": 90, "page": 240}},
        }
    )
    assert snapshot.service_timeout("bb") == 45.0
    assert snapshot.service_timeout("tis") == 90.0
    assert snapshot.service_login_timeout("tis") == 90.0
    assert snapshot.service_login_timeout("bb") == 50.0
    # per-service page override wins over the page section default
    assert snapshot.page_timeout("tis") == 240.0
    assert snapshot.page_timeout_ms("tis") == 240_000
    assert snapshot.page_timeout("bb") == 300.0


def test_shipped_config_file_carries_the_tree():
    """The operator-editable file must exist and actually govern resolution."""
    from sustech_survival import _cache

    path = _cache.config_file()
    if not path.exists():
        return  # a fresh machine: code defaults apply, tree is optional
    tree = _cache.load_config().get("timeouts") or {}
    assert tree, "config.json exists but carries no timeouts tree"
    snapshot = _net.timeouts()
    assert snapshot.http.default == float(tree["http"]["default"])
    if "page" in tree:
        assert snapshot.page_timeout() == float(tree["page"]["default"])


def test_no_module_hardcodes_a_request_timeout():
    """A literal `timeout=N` outside _net.py is a regression, not a local choice."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "_net.py":
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if "_net" in line or "PAGE_TIMEOUT" in line:
                continue
            if not re.search(r"timeout\s*=\s*\d", line):
                continue
            # Local process calls and browser waits are not request timeouts.
            if re.search(r"capture_output|subprocess|check_output|\.stdout|page\.|wait_for|wait_until", line):
                continue
            offenders.append("%s:%d: %s" % (path.relative_to(SRC), lineno, line.strip()[:90]))
    assert not offenders, "hardcoded request timeouts:\n" + "\n".join(offenders)
