"""Synthetic BB reports: cross-course scope, task states, scores and partial reads."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from click.testing import CliRunner

from sustech_survival.bb import assessment_cli, assessments, courses
from sustech_survival.bb.cli import cli

API = assessments.API
UID = "_user_1"
NOW = datetime(2026, 10, 7, 12, tzinfo=assessments.CST)
C1, C2, OLD = "_101_1", "_102_1", "_103_1"
TERM = "_10_1"


class Response:
    def __init__(self, data, status=200, content_type="application/json"):
        self.data = data
        self.status_code = status
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("synthetic failure", response=self)

    def json(self):
        return copy.deepcopy(self.data)


class Session:
    def __init__(self):
        self.routes = {
            API + "/users/me": {"id": UID},
            API
            + f"/users/{UID}/courses": {
                "results": [
                    {"courseId": cid, "course": {"name": name, "termId": term}}
                    for cid, name, term in [
                        (C1, "Course A", TERM),
                        (C2, "Course B", TERM),
                        (OLD, "Old course", "_9_1"),
                    ]
                ]
            },
            API
            + "/terms": {
                "results": [
                    {
                        "id": TERM,
                        "name": "2026 Fall",
                        "availability": {
                            "duration": {
                                "start": "2026-09-01T00:00:00Z",
                                "end": "2027-01-31T23:59:59Z",
                            }
                        },
                    },
                    {
                        "id": "_9_1",
                        "name": "2026 Spring",
                        "availability": {
                            "duration": {
                                "start": "2026-02-01T00:00:00Z",
                                "end": "2026-07-01T00:00:00Z",
                            }
                        },
                    },
                ]
            },
        }
        self.calls = []
        for cid in (C1, C2, OLD):
            prefix = API + f"/courses/{cid}/gradebook"
            self.routes[prefix + "/columns"] = {"results": []}
            self.routes[prefix + f"/users/{UID}"] = {"results": []}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        target = urlsplit(url)
        assert target.netloc == "bb.sustech.edu.cn"
        full = target.path + ("?" + target.query if target.query else "")
        key = full if full in self.routes else target.path
        assert key in self.routes, f"Unexpected request: {url}"
        value = self.routes[key]
        return value if isinstance(value, Response) else Response(value)

    def add(
        self,
        number,
        attempts=(),
        *,
        cid=C1,
        due=None,
        possible=16,
        name=None,
        handler="resource/x-bb-assignment",
        group=False,
        exempt=False,
    ):
        column, content = f"_column{number}_1", f"_content{number}_1"
        prefix = API + f"/courses/{cid}"
        self.routes[prefix + "/gradebook/columns"]["results"].append(
            {
                "id": column,
                "name": name or f"Task {number}",
                "contentId": content,
                "score": {"possible": possible},
                "grading": {"type": "Attempts", "due": due, "scoringModel": "Highest"},
            }
        )
        self.routes[prefix + f"/gradebook/users/{UID}"]["results"].append(
            {
                "userId": UID,
                "columnId": column,
                "exempt": exempt,
                "status": "NotAttempted",
            }
        )
        self.routes[prefix + f"/gradebook/columns/{column}/attempts"] = {"results": list(attempts)}
        self.routes[prefix + f"/contents/{content}"] = {
            "id": content,
            "contentHandler": {"id": handler, "groupContent": group},
            "availability": {"available": "Yes"},
        }
        return prefix, column, content


def attempt(status="Completed", score=None, owner=UID, aid="_attempt_1", **extra):
    return {
        "id": aid,
        "userId": owner,
        "status": status,
        "score": score,
        "created": "2026-10-01T12:00:00Z",
        **extra,
    }


@pytest.fixture
def session(monkeypatch, tmp_path):
    monkeypatch.setenv("SUSTECH_HOME", str(tmp_path))
    monkeypatch.setenv("SUSTECH_CREDENTIALS", str(tmp_path / "missing"))

    def blocked(*args, **kwargs):
        pytest.fail("A synthetic assessment test attempted live traffic")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    return Session()


def query(session, mode="pending", **kwargs):
    return assessments.query(mode, session=session, now=NOW, **kwargs)


def test_current_term_and_cross_course_scope_are_live_not_private(session):
    session.add(1, due="2026-09-01T00:00:00Z")
    session.add(2, [attempt("InProgress")], cid=C2)
    session.add(3, cid=OLD)
    report = query(session)
    assert report["complete"]
    assert {r["course_id"] for r in report["items"]} == {C1, C2}
    assert report["items"][0]["overdue"] is True
    assert report["items"][1]["state"] == "draft"
    assert report["items"][1]["due"] is None
    assert report["scope"]["terms"] == [{"id": TERM, "name": "2026 Fall"}]
    assert len(report["courses_checked"]) == 2
    assert report["scope"]["include_undated"]
    assert all("personal" not in str(v) for v in report.values())


@pytest.mark.parametrize("semester", [TERM, "10", "Fall", "2026-2027-1"])
def test_explicit_semester_forms(session, semester):
    assert query(session, semester=semester)["complete"]
    assert {c["id"] for c in query(session, semester=semester)["scope"]["courses"]} == {C1, C2}


def test_explicit_courses_do_not_require_terms_or_course_cache(session):
    session.routes.pop(API + "/terms")
    session.add(1, cid=OLD)
    report = query(session, course_ids=["103", OLD])
    assert report["complete"]
    assert report["scope"]["requested_course_ids"] == [OLD]
    assert report["items"][0]["course_id"] == OLD


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"course_ids": ["999"]}, "requested_course_not_enrolled"),
        ({"course_ids": [OLD], "semester": TERM}, "requested_course_outside_semester"),
    ],
)
def test_scope_mismatches_are_not_empty_success(session, changes, reason):
    report = query(session, **changes)
    assert not report["complete"]
    assert report["unknown"][0]["reason"] == reason


def test_missing_course_term_and_failed_fallback_are_reported(session):
    entries = session.routes[API + f"/users/{UID}/courses"]["results"]
    entries[0]["course"].pop("termId")
    session.routes[API + f"/courses/{C1}"] = {"name": "Course A"}
    report = query(session)
    assert not report["complete"]
    assert report["unknown"][0]["reason"] == "course_term_unknown"


@pytest.mark.parametrize("term_change", ["undated", "overlap"])
def test_current_term_ambiguity_does_not_guess(session, term_change):
    terms = session.routes[API + "/terms"]["results"]
    if term_change == "undated":
        terms[0].pop("availability")
    else:
        terms.append({**terms[0], "id": "_11_1"})
    report = query(session)
    assert not report["complete"]
    assert "specify --semester" in report["errors"][0]["reason"]
    assert not report["items"]


def test_all_collection_pages_and_attempt_ownership(session):
    prefix, col, _ = session.add(1, [attempt(owner="_other_1")])
    path = prefix + f"/gradebook/columns/{col}/attempts"
    session.routes[path]["paging"] = {"nextPage": "?offset=1"}
    session.routes[path + "?offset=1"] = {"results": [attempt("NeedsGrading")]}
    report = query(session, "grades")
    assert report["complete"]
    assert len(report["items"][0]["attempts"]) == 1
    assert report["items"][0]["attempts"][0]["grading_pending"]
    attempt_urls = [url for url, _ in session.calls if urlsplit(url).path.endswith("/attempts")]
    assert len(attempt_urls) == 2
    assert all("userId" not in parse_qs(urlsplit(url).query) for url in attempt_urls)
    assert UID not in json.dumps(report)


def test_enrollment_columns_and_user_grades_pagination(session):
    prefix, col, _ = session.add(1)
    paths = [
        API + f"/users/{UID}/courses",
        prefix + "/gradebook/columns",
        prefix + f"/gradebook/users/{UID}",
    ]
    for path in paths:
        data = session.routes[path]
        session.routes[path + "?offset=1"] = copy.deepcopy(data)
        session.routes[path] = {"results": [], "paging": {"nextPage": "?offset=1"}}
    report = query(session)
    assert report["complete"]
    assert report["items"][0]["column_id"] == col


def test_zero_actual_possible_and_all_attempts_without_inferred_aggregation(session):
    session.add(
        1,
        [
            attempt(score=0),
            attempt("NeedsGrading", aid="_attempt_2"),
            attempt(score=999, owner="_other_1"),
        ],
        handler="resource/x-bb-asmt-test-link",
    )
    report = query(session, "grades")
    row = report["items"][0]
    assert report["complete"]
    assert row["possible"] == 16
    assert row["kind"] == "test_or_assignment"
    assert [a["score"] for a in row["attempts"]] == [0, None]
    assert row["attempts"][0]["has_score"]
    assert row["grade"] == {
        "status": "NotAttempted",
        "score": None,
        "display_score": None,
        "source": "gradebook_user_grade",
    }


@pytest.mark.parametrize(
    "score,extra,expected",
    [
        ({"raw": 0, "display": "0"}, {}, 0),
        (None, {"displayGrade": {"scaleType": "Score", "score": 8}}, 8),
        (None, {"displayGrade": {"scaleType": "Percent", "score": 50, "text": "50%"}}, None),
    ],
)
def test_score_shapes_do_not_confuse_percent_and_raw_points(session, score, extra, expected):
    session.add(1, [attempt(score=score, **extra)])
    row = query(session, "grades")["items"][0]
    assert row["attempts"][0]["score"] == expected


def test_column_exemption_without_attempts_is_not_pending(session):
    session.add(1, exempt=True)
    report = query(session)
    assert report["complete"]
    assert not report["items"]
    assert report["excluded"][0]["state"] == "exempt"
    assert not any("/attempts" in url for url, _ in session.calls)


def test_exempt_attempt_does_not_exempt_an_entire_column(session):
    session.add(1, [attempt(exempt=True), attempt("InProgress", aid="_draft_1")])
    report = query(session)
    assert report["complete"]
    assert report["items"][0]["state"] == "draft"


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"group": True}, "group_submission_unknown"),
        ({"name": "Task (notification only, do not submit here)"}, "external_submission"),
        ({"name": "任务（仅做通知提醒，请勿在此处提交）"}, "external_submission"),
        ({"handler": "resource/x-bb-blti-link"}, "unsupported_assessment_type"),
    ],
)
def test_unverifiable_tasks_are_separate_from_pending(session, kwargs, reason):
    session.add(1, **kwargs)
    report = query(session)
    assert not report["complete"]
    assert not report["items"]
    assert report["unknown"][0]["reason"] == reason


@pytest.mark.parametrize("status", ["NeedsGrading", "NeedsGradingAgain", "Completed"])
def test_submitted_assessments_are_not_pending(session, status):
    session.add(1, [attempt(status)])
    assert not query(session)["items"]
    assert query(session)["complete"]


def test_reopened_activity_and_missing_owner_remain_unknown(session):
    session.add(1, [attempt("InProgressAgain")])
    missing = attempt()
    missing.pop("userId")
    session.add(2, [missing])
    report = query(session)
    assert not report["complete"]
    assert {r["reason"] for r in report["unknown"]} == {
        "attempt_status_unknown",
        "attempt_ownership_unknown",
    }


@pytest.mark.parametrize("stage", ["columns", "attempts", "content", "user_grades"])
def test_failed_reads_preserve_other_courses_without_false_pending(session, stage):
    prefix, col, content = session.add(1)
    session.add(2, cid=C2)
    path = {
        "columns": prefix + "/gradebook/columns",
        "attempts": prefix + f"/gradebook/columns/{col}/attempts",
        "content": prefix + f"/contents/{content}",
        "user_grades": prefix + f"/gradebook/users/{UID}",
    }[stage]
    session.routes[path] = Response({}, status=403)
    report = query(session)
    assert not report["complete"]
    assert [r["course_id"] for r in report["items"]] == [C2]
    assert report["errors"][0]["http_status"] == 403
    assert UID not in json.dumps(report)


def test_content_failure_preserves_known_grade(session):
    prefix, _, content = session.add(1, [attempt(score=12)])
    session.routes[prefix + f"/contents/{content}"] = Response({}, status=403)
    report = query(session, "grades")
    assert not report["complete"]
    assert report["items"][0]["attempts"][0]["score"] == 12


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"results": [None]},
        {"results": [], "paging": "bad"},
        {"results": [], "paging": {"nextPage": "https://example.com/page"}},
    ],
)
def test_malformed_collections_cannot_be_empty_success(session, data):
    session.routes[API + f"/users/{UID}/courses"] = data
    assert not query(session)["complete"]


def test_repeated_pagination_and_login_html_are_incomplete(session):
    path = API + f"/users/{UID}/courses"
    session.routes[path] = {"results": [], "paging": {"nextPage": path}}
    assert not query(session)["complete"]
    session.routes[path] = Response({}, content_type="text/html")
    report = query(session)
    assert "non-JSON" in report["errors"][0]["reason"]
    assert report["errors"][0]["http_status"] == 200


def test_due_bounds_are_inclusive_and_explicitly_exclude_undated(session):
    session.add(1, due="2026-10-07T04:00:00Z")
    session.add(2)
    session.add(3, due="2026-09-01T00:00:00Z")
    report = query(session, due_from=NOW, due_until=NOW.astimezone(timezone.utc))
    assert report["complete"]
    assert [r["name"] for r in report["items"]] == ["Task 1"]
    assert not report["scope"]["include_undated"]


def test_invalid_due_is_reported_not_silently_filtered(session):
    session.add(1, due="bad timestamp")
    report = query(session, due_from=NOW)
    assert not report["complete"]
    assert report["unknown"][0]["reason"] == "due_unavailable_for_window"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "invalid"},
        {"course_ids": "101"},
        {"course_ids": ["name"]},
        {"semester": ""},
        {"now": datetime(2026, 10, 7)},
        {"due_from": NOW + assessments.timedelta(days=1), "due_until": NOW},
        {"mode": "grades", "due_from": NOW},
    ],
)
def test_invalid_arguments_fail_before_authentication(monkeypatch, kwargs):
    factory = lambda: pytest.fail("Invalid arguments reached authentication")
    monkeypatch.setattr(courses, "session", factory)
    with pytest.raises(ValueError):
        assessments.query(**kwargs)


def test_single_authorizer_session_and_timeout_budget(session, monkeypatch):
    calls = []
    monkeypatch.setattr(courses, "session", lambda: calls.append(1) or session)
    monkeypatch.setattr(assessments._net, "service_timeout", lambda service: 37)
    report = assessments.query(now=NOW)
    assert report["complete"]
    assert calls == [1]
    assert all(kwargs["timeout"] == 37 for _, kwargs in session.calls)


@pytest.mark.parametrize("command", ["pending", "grades"])
def test_cli_json_is_clean_and_partial_coverage_exits_one(session, monkeypatch, command):
    session.add(1)
    report = query(session, command)

    def read(*args, **kwargs):
        print("auth diagnostic")
        return report

    monkeypatch.setattr(assessment_cli, "query", read)
    result = CliRunner().invoke(cli, [command, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["complete"]
    assert "auth diagnostic" in result.stderr
    report["complete"] = False
    report["unknown"] = [{"reason": "synthetic"}]
    result = CliRunner().invoke(cli, [command, "--json"])
    assert result.exit_code == 1
    assert not json.loads(result.stdout)["complete"]


def test_cli_scope_and_due_bounds_are_forwarded(session, monkeypatch):
    seen = []
    monkeypatch.setattr(
        assessment_cli,
        "query",
        lambda *args, **kwargs: seen.append((args, kwargs)) or query(session),
    )
    result = CliRunner().invoke(
        cli,
        [
            "pending",
            "--term",
            TERM,
            "-c",
            "101",
            "-c",
            "102",
            "--due-until",
            "2026-10-10T23:59:59+08:00",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen[0][1]["semester"] == TERM
    assert seen[0][1]["course_ids"] == ("101", "102")
    assert seen[0][1]["due_until"].utcoffset().total_seconds() == 8 * 3600


def test_cli_grade_text_preserves_zero_and_actual_possible(session, monkeypatch):
    session.add(1, [attempt(score=0), attempt("NeedsGrading", aid="_attempt_2")])
    monkeypatch.setattr(assessment_cli, "query", lambda *args, **kwargs: query(session, "grades"))
    result = CliRunner().invoke(cli, ["grades"])
    assert result.exit_code == 0, result.output
    assert "0/16" in result.stdout
    assert "awaiting grading" in result.stdout
    assert "/100" not in result.stdout


def test_cli_naive_bound_is_usage_error_without_query(monkeypatch):
    monkeypatch.setattr(assessment_cli, "query", lambda *args, **kwargs: pytest.fail("query ran"))
    result = CliRunner().invoke(cli, ["pending", "--due-from", "2026-10-07"])
    assert result.exit_code == 2
    assert "time zone" in result.output


def test_empty_score_text_is_not_a_published_grade(session):
    session.add(1, [attempt(score="", text="")])
    row = query(session, "grades")["items"][0]
    assert row["attempts"][0]["score"] is None
    assert row["attempts"][0]["display_score"] is None
    assert not row["attempts"][0]["has_score"]


def test_partial_owner_data_preserves_confirmed_own_scores(session):
    session.add(1, [attempt(score=8), attempt(owner=None, aid="_unknown_1")])
    report = query(session, "grades")
    assert not report["complete"]
    assert report["unknown"][0]["reason"] == "attempt_ownership_unknown"
    assert [a["score"] for a in report["items"][0]["attempts"]] == [8]


def test_due_bounds_and_report_preserve_subsecond_precision(session):
    session.add(1, due="2026-10-07T04:00:00.500Z")
    lower = NOW.replace(microsecond=250000)
    upper = NOW.replace(microsecond=750000)
    report = query(session, due_from=lower, due_until=upper)
    assert report["complete"]
    assert report["items"][0]["due"] == "2026-10-07T12:00:00.500000+08:00"
    assert report["scope"]["due_from"] == lower.isoformat()
    assert report["scope"]["due_until"] == upper.isoformat()


@pytest.mark.parametrize("mode", ["pending", "grades"])
def test_attempt_query_works_when_user_filter_is_forbidden(session, monkeypatch, mode):
    # Model the deployed API: the same GET succeeds without the optional filter.
    records = [attempt(score=99, owner="_other_1")]
    if mode == "grades":
        records.append(attempt(score=8, aid="_own_1"))
    session.add(1, records)
    get = session.get

    def deployed_get(url, **kwargs):
        response = get(url, **kwargs)
        target = urlsplit(url)
        if target.path.endswith("/attempts") and "userId" in parse_qs(target.query):
            return Response({}, status=403)
        return response

    monkeypatch.setattr(session, "get", deployed_get)
    report = query(session, mode)
    assert report["complete"]
    assert not report["errors"]
    row = report["items"][0]
    if mode == "pending":
        assert row["state"] == "unsubmitted"
        assert row["attempts"] == []  # Another student's work is not our submission.
    else:
        assert [a["score"] for a in row["attempts"]] == [8]
    attempt_urls = [url for url, _ in session.calls if urlsplit(url).path.endswith("/attempts")]
    assert len(attempt_urls) == 1
    assert parse_qs(urlsplit(attempt_urls[0]).query) == {"limit": ["200"]}


@pytest.mark.parametrize("mode", ["pending", "grades"])
def test_real_attempt_permission_failure_is_not_retried(session, mode):
    prefix, column, _ = session.add(1, [attempt(score=8)])
    path = prefix + f"/gradebook/columns/{column}/attempts"
    session.routes[path] = Response({}, status=403)
    report = query(session, mode)
    assert not report["complete"]
    assert report["items"] == []
    assert report["errors"][0]["stage"] == "attempts"
    assert report["errors"][0]["http_status"] == 403
    assert report["unknown"][0]["reason"] == "attempts_unavailable"
    assert len([url for url, _ in session.calls if urlsplit(url).path == path]) == 1
