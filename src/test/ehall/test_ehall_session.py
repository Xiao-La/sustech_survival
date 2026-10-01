"""Credential lookup for eHall uses the shared SSO source, with no network."""

import pytest

from sustech_survival.ehall._session import EhallAuthError, _load_credentials
from sustech_survival.sso import authorizer


def test_ehall_credentials_use_shared_environment_path(tmp_path, monkeypatch):
    credentials = tmp_path / "credentials.txt"
    credentials.write_text("test-student:test-password\n", encoding="utf-8")
    monkeypatch.setattr(authorizer, "_IN_MEMORY_CREDS", None)
    monkeypatch.setenv("SUSTECH_CREDENTIALS", str(credentials))
    monkeypatch.setenv("SUSTECH_CREDENTIALS_FILE", str(tmp_path / "obsolete.txt"))

    assert _load_credentials() == ("test-student", "test-password")


def test_ehall_credentials_respect_in_memory_override(tmp_path, monkeypatch):
    monkeypatch.setattr(
        authorizer, "_IN_MEMORY_CREDS", ("memory-student", "memory-password")
    )
    monkeypatch.setenv("SUSTECH_CREDENTIALS", str(tmp_path / "missing.txt"))

    assert _load_credentials() == ("memory-student", "memory-password")


def test_ehall_credentials_report_missing_shared_source(tmp_path, monkeypatch):
    monkeypatch.setattr(authorizer, "_IN_MEMORY_CREDS", None)
    monkeypatch.setenv("SUSTECH_CREDENTIALS", str(tmp_path / "missing.txt"))

    with pytest.raises(EhallAuthError, match="No credentials"):
        _load_credentials()
