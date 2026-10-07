"""Private desktop pipe protocol uses the existing in-memory credential API."""
import io
import json
from unittest.mock import Mock
import pytest
from sustech_survival.webui import _desktop
from sustech_survival.sso import cred_clear
from sustech_survival.sso.authorizer import read_credentials


def test_bootstrap_injects_credentials_before_serving(monkeypatch, tmp_path):
    monkeypatch.setenv('SUSTECH_HOME', str(tmp_path))
    def serve(**kwargs):
        assert read_credentials() == ('fixture-sid', 'fixture-password')
        assert kwargs == {'port': 12345, 'host': '127.0.0.1', 'skin': 'default_zh'}
        assert not list(tmp_path.rglob('credentials.txt'))
        return 0
    monkeypatch.setattr(_desktop, 'run', serve)
    source = io.StringIO(json.dumps({'sid':'fixture-sid', 'password':'fixture-password'}))
    assert _desktop.main(['--port', '12345', '--skin', 'default_zh'], source) == 0


@pytest.mark.parametrize('input', ['not json', '[]', '{"sid":"fixture-sid"}',
                                  '{"sid":"fixture-sid","password":""}'])
def test_invalid_pipe_input_never_starts_backend(monkeypatch, input, capsys):
    serve = Mock()
    monkeypatch.setattr(_desktop, 'run', serve)
    assert _desktop.main([], io.StringIO(input)) == 2
    serve.assert_not_called()
    assert 'fixture-sid' not in capsys.readouterr().err


def test_empty_pipe_preserves_cli_credential_resolution(monkeypatch):
    cred_clear()
    serve = Mock(return_value=0)
    monkeypatch.setattr(_desktop, 'run', serve)
    assert _desktop.main([], io.StringIO('{}')) == 0
    serve.assert_called_once()
