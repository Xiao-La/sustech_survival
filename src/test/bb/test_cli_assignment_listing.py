"""Offline regressions for Blackboard course and attempt discovery in the CLI."""

import pytest
from click.testing import CliRunner

from sustech_survival.bb import cli, courses


@pytest.fixture
def one_attempt(monkeypatch):
    attempts = [("326812", 1, "2026-09-27 13:10:32")]
    monkeypatch.setattr(cli, "safe_attempts", lambda *_: attempts)
    monkeypatch.setattr(
        cli,
        "scrape_attempt_details",
        lambda *_: {"created": attempts[0][2], "files": [], "graded": False},
    )
    return attempts


def test_find_course_accepts_numeric_id_without_live_fallback(monkeypatch):
    course = {"id": "_8852_1", "name": "Data Structures and Algorithm Analysis(H)"}
    monkeypatch.setattr(courses, "load_courses", lambda: [course])
    monkeypatch.setattr(
        courses, "api", lambda *_: pytest.fail("local numeric ID must not hit BB"),
    )

    assert courses.find_course("8852") == [(course["id"], course["name"])]
    assert courses.find_course("_8852_1") == [(course["id"], course["name"])]


def test_find_course_accepts_numeric_id_in_mocked_live_fallback(monkeypatch):
    monkeypatch.setattr(courses, "load_courses", lambda: [])

    def fake_api(path):
        if path.endswith("/users/me"):
            return {"id": "_student_1"}
        if path.endswith("/users/_student_1/courses"):
            return {"results": [{"courseId": "_8852_1"}]}
        if path.endswith("/courses/_8852_1"):
            return {"name": "Data Structures and Algorithm Analysis(H)"}
        pytest.fail(f"unexpected endpoint: {path}")

    monkeypatch.setattr(courses, "api", fake_api)
    assert courses.find_course("8852") == [
        ("_8852_1", "Data Structures and Algorithm Analysis(H)")
    ]


def test_assignment_list_displays_three_field_attempt(one_attempt, capsys):
    cli.list_assignments(None, "8852", "DSAA(H)", [("661412", "Assignment 3")])

    output = capsys.readouterr().out
    assert "Assignment 3" in output
    assert "1 attempt(s)" in output
    assert "Attempt 1" in output


@pytest.mark.parametrize("argument", ["status", "attempts", "1"])
def test_single_assignment_handles_three_field_attempt(one_attempt, capsys, argument):
    cli.single_assignment(None, [], "8852", "661412", argument, False, None)

    output = capsys.readouterr().out
    assert "Attempt 1" in output


def test_submit_cli_dry_run_routes_to_preview(monkeypatch, tmp_path):
    import sustech_survival.bb.submit as submit_module

    pdf = tmp_path / "homework.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    calls = []
    monkeypatch.setattr(cli, "load_session_or_exit", lambda: [])

    def fake_submit(content_id, file_path, course_id=None, dry_run=False):
        calls.append((content_id, file_path, course_id, dry_run))
        return True, "DRY-RUN"

    monkeypatch.setattr(submit_module, "submit_file", fake_submit)
    result = CliRunner().invoke(
        cli.cli, ["submit", "661412", str(pdf), "-c", "8852", "--dry-run"]
    )

    assert result.exit_code == 0
    assert calls == [("661412", str(pdf), "8852", True)]
    assert "Preview successful" in result.output
    assert "Submission successful" not in result.output


def test_submit_cli_returns_failure_exit_code(monkeypatch, tmp_path):
    import sustech_survival.bb.submit as submit_module

    pdf = tmp_path / "homework.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(cli, "load_session_or_exit", lambda: [])
    monkeypatch.setattr(
        submit_module, "submit_file", lambda *_, **__: (False, "form unavailable")
    )

    result = CliRunner().invoke(
        cli.cli, ["submit", "661412", str(pdf), "-c", "8852", "--dry-run"]
    )

    assert result.exit_code == 1
    assert "form unavailable" in result.output


def test_submit_file_forwards_dry_run_to_rest(monkeypatch, tmp_path):
    import sustech_survival.bb.submit as submit_module

    pdf = tmp_path / "homework.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    calls = []

    class Result:
        def to_tuple(self):
            return True, "DRY-RUN"

    def fake_rest(*args, **kwargs):
        calls.append((args, kwargs))
        return Result()

    monkeypatch.setattr(submit_module, "submit_assignment_rest", fake_rest)
    ok, message = submit_module.submit_file(
        "661412", str(pdf), course_id="8852", dry_run=True
    )

    assert (ok, message) == (True, "DRY-RUN")
    assert calls[0][0][:2] == ("8852", "661412")
    assert calls[0][1]["dry_run"] is True
