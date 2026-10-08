"""Run the delegated classroom entry point entirely against local fixtures."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
from click.testing import CliRunner
import pytest
from sustech_survival.tis.classroom import __main__ as commands
from sustech_survival.tis.classroom.cli import cli

@pytest.fixture
def classroom(monkeypatch):
    c = Mock(xn="2026-2027", xq="1")
    c.rooms.return_value = [SimpleNamespace(name="Building A 101", short_name="A", capacity=40, slot_count=2)]
    c.free.return_value = ["Building A 101"]
    c._query_didian_catalog.return_value = [{"dm":"A101", "mc":"Building A 101", "zws":40, "xiaoqu":"1"}]
    monkeypatch.setattr(commands, "classroom_factory", lambda **kw: c)
    import importlib
    module = importlib.import_module("sustech_survival.tis.classroom.classroom")
    monkeypatch.setattr(module, "ClassroomOccupancy", lambda **kw: c)
    # Exercise the exact argv from Click, without a second process or network.
    monkeypatch.setattr("subprocess.call", lambda argv: commands.main(argv[3:]))
    return c

def test_rooms_json_and_term_options(classroom):
    result = CliRunner().invoke(cli, ["rooms", "--xn", "2026-2027", "--xq", "1", "--building", "A", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["capacity"] == 40

def test_free_period_range_json(classroom):
    result = CliRunner().invoke(cli, ["free", "--week", "5", "--day", "3", "--period", "3", "--period", "4", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == ["Building A 101"]
    classroom.free.assert_called_once_with(5, 3, 3, 4)

def test_booking_default_is_preview_and_preserves_ranges(classroom):
    result = CliRunner().invoke(cli, ["book", "--room", "A101", "--day", "3", "--period", "3", "--period", "4", "--week", "5", "--week", "6", "--headcount", "10", "--purpose", "Study"])
    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output
    assert "period 3-4, week 5 6" in result.output
    classroom.ensure_session.assert_not_called()

def test_empty_live_availability_is_not_all_rooms(classroom):
    classroom.live_rooms_free_at.return_value = []
    result = CliRunner().invoke(cli, ["search-rooms", "--day", "3", "--period", "3", "--period", "4"])
    assert result.exit_code == 0, result.output
    assert "Free at that time: 0" in result.output

def test_failed_live_availability_is_reported(classroom):
    classroom.live_rooms_free_at.side_effect = RuntimeError("unavailable")
    result = CliRunner().invoke(cli, ["search-rooms", "--day", "3", "--period", "3"])
    assert result.exit_code == 1
    assert "Could not read live occupancy" in result.output
