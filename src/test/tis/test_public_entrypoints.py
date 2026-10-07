"""Regression tests for documented Python display/export entry points."""
import csv
import pytest
from sustech_survival.tis import courses, grades

@pytest.mark.parametrize("module,fetch,kwargs,filename", [
    (courses, "get_courses", {"format": "csv"}, "courses_tis.csv"),
    (grades, "get_grades", {"export": "csv"}, "grades.csv"),
])
def test_public_run_uses_session_and_exports(monkeypatch, tmp_path, capsys,
                                             module, fetch, kwargs, filename):
    monkeypatch.setenv("SUSTECH_HOME", str(tmp_path))
    session = object()
    monkeypatch.setattr(module, "make_session", lambda: session)
    def query(given, semester):
        assert given is session
        assert semester == "2025-20262"
        return [{"kcdm": "TEST101", "kcmc": "Example course", "xf": 3,
                 "xnxqmc": "2025春季", "xscj": "A", "zzcj": 90}]
    monkeypatch.setattr(module, fetch, query)
    module.run(semester="2025-20262", **kwargs)
    path = tmp_path / ".sustech_survival" / "exports" / filename
    with path.open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["课程名称"] == "Example course"
    assert "_label" not in rows[0]
    assert str(path) in capsys.readouterr().out
