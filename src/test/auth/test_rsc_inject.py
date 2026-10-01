"""RSC browser cookie handoff stays in memory and never loads a session file."""

import pytest

from sustech_survival.sso.authlib import rsc, rsc_inject
from sustech_survival.sso import cred_clear, cred_set


def test_load_rsc_session_uses_authorizer_memory(monkeypatch):
    class FakeAuth:
        _session_cache = {
            "plain": "value",
            "scoped": {"value": "other", "domain": ".example.invalid"},
        }

    monkeypatch.setattr(rsc, "RSCAuthorizer", FakeAuth)
    assert rsc_inject.load_rsc_session() == [
        {"name": "plain", "value": "value"},
        {"name": "scoped", "value": "other", "domain": ".example.invalid"},
    ]


def test_load_rsc_session_rejects_disk_path(tmp_path):
    legacy = tmp_path / "session.json"
    legacy.write_text('{"cookie": "stale"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Disk-backed"):
        rsc_inject.load_rsc_session(str(legacy))


def test_load_rsc_session_uses_existing_browser_context(monkeypatch):
    class FakeContext:
        def cookies(self):
            return [{"name": "live", "value": "cookie"}]

    class FakeAuth:
        _session_cache = {}
        page = type("Page", (), {"context": FakeContext()})()

    monkeypatch.setattr(rsc, "RSCAuthorizer", FakeAuth)
    assert rsc_inject.load_rsc_session() == [{"name": "live", "value": "cookie"}]


def test_rsc_authorizer_uses_shared_credentials():
    cred_set("sid", "password")
    try:
        assert rsc.RSCAuthorizer().creds == ("sid", "password")
    finally:
        cred_clear()
