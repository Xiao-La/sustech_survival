"""Tests for the two-step submission surface: `bb submit preview` / `apply`.

Rule coverage: the preview must never POST, `apply` must refuse without an
explicit review (confirm + expected hash), and it must refuse changed bytes
instead of uploading them. Mirrors sustech-cli v0.11.1's surface.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sustech_survival.bb import submit as submit_mod

TARGET = {
    "course_id": "8613",
    "content_id": "637897",
    "column_id": "12345",
    "assignment_name": "Experiment 1-Report (Thermochromic)",
    "course_name": "Experiments for Advanced Materials Science and Engineering I",
}


@pytest.fixture()
def report_file(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.5 fake reviewed report bytes")
    return path


def _patch_target(attempts: int = 0):
    return patch.multiple(
        submit_mod,
        verify_assignment_target=MagicMock(return_value=dict(TARGET)),
        _safe_initial_attempts_count=MagicMock(return_value=attempts),
    )


def test_preview_reports_hash_and_size_without_posting(report_file):
    with _patch_target(attempts=0), \
         patch.object(submit_mod, "_get_upload_form") as form_fetch, \
         patch.object(submit_mod, "_bb_session") as session, \
         patch.object(submit_mod, "submit_assignment_rest") as rest:
        plan = submit_mod.preview_submission("637897", str(report_file), course_id="8613")

    assert plan["will_post"] is False
    assert plan["sha256"] == submit_mod.sha256_of_file(report_file)
    assert plan["size_bytes"] == report_file.stat().st_size
    assert plan["attempts_so_far"] == 0
    assert plan["action"] == "create a new attempt"
    assert plan["assignment_name"] == TARGET["assignment_name"]
    # none of the network/POST paths ran
    session.assert_not_called()
    rest.assert_not_called()
    form_fetch.assert_not_called()  # no --comment, so the form is not fetched


def test_preview_numbers_the_next_attempt(report_file):
    with _patch_target(attempts=2):
        plan = submit_mod.preview_submission("637897", str(report_file), course_id="8613")
    assert plan["action"] == "create attempt #3"
    assert plan["attempts_so_far"] == 2


def test_apply_refuses_without_confirmation(report_file):
    digest = submit_mod.sha256_of_file(report_file)
    with pytest.raises(submit_mod.SubmissionNotConfirmedError):
        submit_mod.apply_submission("637897", str(report_file), course_id="8613",
                                    expected_sha256=digest, confirm=False)


def test_apply_refuses_without_reviewed_hash(report_file):
    with pytest.raises(submit_mod.SubmissionNotConfirmedError):
        submit_mod.apply_submission("637897", str(report_file), course_id="8613",
                                    expected_sha256=None, confirm=True)


def test_apply_refuses_changed_bytes(report_file):
    stale = "0" * 64
    with patch.object(submit_mod, "submit_assignment_rest") as rest:
        with pytest.raises(submit_mod.FileChangedSinceReviewError) as err:
            submit_mod.apply_submission("637897", str(report_file), course_id="8613",
                                        expected_sha256=stale, confirm=True)
    assert err.value.reviewed_sha256 == stale
    assert err.value.current_sha256 == submit_mod.sha256_of_file(report_file)
    rest.assert_not_called()  # nothing left the machine


def test_apply_submits_the_reviewed_bytes_once(report_file):
    digest = submit_mod.sha256_of_file(report_file)
    fake_result = MagicMock()
    fake_result.to_tuple.return_value = (True, "attempt 1 recorded")
    with patch.object(submit_mod, "submit_assignment_rest", return_value=fake_result) as rest:
        result = submit_mod.apply_submission("637897", str(report_file),
                                             course_id="8613",
                                             expected_sha256=digest, confirm=True)

    assert result is fake_result
    assert rest.call_count == 1  # exactly one POST, no replay
    assert rest.call_args.kwargs["reviewed_sha256"] == digest
    assert rest.call_args.args[:2] == ("8613", "637897")


def test_preview_rejects_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        submit_mod.preview_submission("637897", str(tmp_path / "nope.pdf"),
                                      course_id="8613")


def test_cli_submit_without_arguments_prints_usage():
    from click.testing import CliRunner

    from sustech_survival.bb import cli as cli_mod

    result = CliRunner().invoke(cli_mod.cli, ["submit"])
    assert result.exit_code == 2
    assert "preview" in result.output


def test_cli_preview_dispatch_takes_content_id_and_file(tmp_path):
    from click.testing import CliRunner

    from sustech_survival.bb import cli as cli_mod

    report = tmp_path / "report.pdf"
    report.write_bytes(b"x")
    plan = {
        "assignment_name": "Experiment 1-Report (Thermochromic)",
        "content_id": "637897", "course_id": "8613",
        "action": "create a new attempt", "attempts_so_far": 0,
        "file_name": "report.pdf", "size_bytes": 1,
        "sha256": "a" * 64, "comment": None, "comment_field": None,
    }
    with patch.object(cli_mod, "load_session_or_exit"), \
         patch("sustech_survival.bb.submit.preview_submission",
               return_value=plan) as preview:
        result = CliRunner().invoke(
            cli_mod.cli, ["submit", "preview", "637897", str(report)])

    assert result.exit_code == 0, result.output
    preview.assert_called_once()
    assert preview.call_args.args[0] == "637897"
    assert preview.call_args.args[1] == str(report)
    assert "nothing uploaded" in result.output
