"""Local launcher defaults and map proxy scope without service requests."""
from unittest.mock import Mock
from click.testing import CliRunner
import pytest
from sustech_survival.webui import app, __main__ as legacy
from sustech_survival.transit.api import _mirror_url


def test_python_launcher_defaults_to_loopback(monkeypatch):
    server = Mock(config={})
    monkeypatch.setattr(app, '_port_in_use', lambda *a: False)
    monkeypatch.setattr(app, 'create_app', lambda **kw: server)
    app.run()
    assert server.run.call_args.kwargs['host'] == '127.0.0.1'


def test_module_launcher_and_explicit_host(monkeypatch):
    run = Mock(return_value=0)
    monkeypatch.setattr(legacy, 'run', run)
    monkeypatch.setattr('sys.argv', ['webui', 'serve'])
    assert legacy.main() == 0
    assert run.call_args.kwargs['host'] == '127.0.0.1'
    monkeypatch.setattr('sys.argv', ['webui', 'serve', '--host', '0.0.0.0'])
    legacy.main()
    assert run.call_args.kwargs['host'] == '0.0.0.0'


@pytest.mark.parametrize('upstream', [
    'example.com/file.pmtiles', '127.0.0.1/file.pmtiles',
    'mirrors.sustech.edu.cn:444/site/pmtiles-data/a.pmtiles',
    'mirrors.sustech.edu.cn/site/other/a.pmtiles',
    'mirrors.sustech.edu.cn/site/pmtiles-data/../other/a.pmtiles',
    'mirrors.sustech.edu.cn/site/pmtiles-data/%2e%2e/other/a.pmtiles',
])
def test_proxy_rejects_unrelated_resources(monkeypatch, upstream):
    get = Mock()
    monkeypatch.setattr('requests.get', get)
    response = app.create_app(skin='default').test_client().get('/pmtiles-proxy/' + upstream)
    assert response.status_code == 400
    get.assert_not_called()


def test_proxy_keeps_map_ranges_and_glyphs(monkeypatch):
    upstream = 'mirrors.sustech.edu.cn/site/pmtiles-data/20250114.pmtiles'
    response = Mock(status_code=206, headers={'Content-Range':'bytes 0-3/100', 'Content-Type':'application/octet-stream'})
    response.iter_content.return_value = [b'map!']
    get = Mock(return_value=response)
    monkeypatch.setattr('requests.get', get)
    client = app.create_app(skin='default').test_client()
    result = client.get('/pmtiles-proxy/' + upstream, headers={'Range':'bytes=0-3'})
    assert result.status_code == 206
    assert result.data == b'map!'
    assert result.headers['Content-Range'] == 'bytes 0-3/100'
    assert get.call_args.kwargs['headers'] == {'Range':'bytes=0-3'}
    assert get.call_args.kwargs['allow_redirects'] is False
    assert _mirror_url('mirrors.sustech.edu.cn/site/pmtiles-data/fonts/Noto Sans/0-255.pbf')


def test_proxy_reports_redirect_without_following(monkeypatch):
    response = Mock(status_code=302)
    monkeypatch.setattr('requests.get', Mock(return_value=response))
    result = app.create_app(skin='default').test_client().get('/pmtiles-proxy/mirrors.sustech.edu.cn/site/pmtiles-data/a.pmtiles')
    assert result.status_code == 502
    response.close.assert_called_once()


def test_unified_cli_defaults_to_loopback(monkeypatch):
    from sustech_survival.cli.main import cli
    run = Mock(return_value=0)
    monkeypatch.setattr(app, 'run', run)
    result = CliRunner().invoke(cli, ['webui', 'serve'])
    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs['host'] == '127.0.0.1'
