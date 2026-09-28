"""Offline regressions for BB identifiers and unknown submission status."""

import pytest

from sustech_survival.bb import cli, courses, download, query, submit
from sustech_survival.bb.ids import numeric_id


@pytest.mark.parametrize(
    ("bb_id", "expected"),
    [("_8851_1", "8851"), ("_661411_1", "661411"),
     ("_326811_1", "326811"), ("326811", "326811")],
)
def test_numeric_id_preserves_trailing_ones(bb_id, expected):
    assert numeric_id(bb_id) == expected


def test_assignment_discovery_preserves_content_id(monkeypatch):
    monkeypatch.setattr(
        courses, "api",
        lambda *_: {"results": [{"contentId": "_661411_1", "name": "Assignment"}]},
    )
    assert courses.discover_assignments_for_course("_8851_1") == [
        ("661411", "Assignment")
    ]


def test_attempt_and_column_ids_preserve_trailing_ones(monkeypatch):
    monkeypatch.setattr(download, "session", lambda: object())
    monkeypatch.setattr(
        download, "api",
        lambda *_: {"results": [{"id": "_326811_1", "created": "2026-09-27T13:10:32Z"}]},
    )
    monkeypatch.setattr(
        download, "get_content_item",
        lambda *_: {"contentHandler": {"gradeColumnId": "_426151_1"}},
    )

    assert download.get_column_id_for_content("8851", "661411") == "426151"
    assert download.get_assignment_attempts("8851", "426151", strict=True) == [
        ("326811", 1, "2026-09-27 13:10:32")
    ]
    assert download.scrape_attempt_details(None, "8851", "661411", "326811")[
        "attempt_num"
    ] == "1"


def test_course_query_preserves_trailing_one(monkeypatch):
    monkeypatch.setattr(
        query, "api",
        lambda path: ({"id": "user"} if path.endswith("/users/me") else
                      {"results": [{"courseId": "_8851_1", "name": "Course"}]}),
    )
    assert query.discover_courses() == [("8851", "Course")]


def test_failed_attempt_lookup_is_not_reported_as_no_submission(monkeypatch, capsys):
    monkeypatch.setattr(download, "session", lambda: object())

    def failed_api(*_):
        raise RuntimeError("gradebook unavailable")

    monkeypatch.setattr(download, "api", failed_api)
    with pytest.raises(RuntimeError, match="gradebook unavailable"):
        download.get_assignment_attempts("8851", "426151", strict=True)

    monkeypatch.setattr(
        cli, "discover_attempt_ids", lambda *_: failed_api()
    )
    cli.list_assignments(None, "8851", "Course", [("661411", "Assignment")])
    output = capsys.readouterr().out
    assert "status unavailable" in output
    assert "not submitted" not in output


def test_check_attempts_raises_when_column_missing(monkeypatch):
    monkeypatch.setattr(download, "get_content_item", lambda *_: {"title": "Assignment"})
    monkeypatch.setattr(download, "get_column_id_for_content", lambda *_: None)
    with pytest.raises(LookupError, match="No gradebook column"):
        submit.check_attempts("661411", course_id="8851")
