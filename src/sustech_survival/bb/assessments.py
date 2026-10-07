"""Read-only, term-scoped Blackboard pending work and submitted attempt grades."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import requests

from sustech_survival import _net

BASE = "https://bb.sustech.edu.cn"
API = "/learn/api/public/v1"
CST = timezone(timedelta(hours=8))
SUBMITTED = {"NeedsGrading", "NeedsGradingAgain", "Completed"}
KINDS = {
    "resource/x-bb-assignment": "assignment",
    "resource/x-bb-asmt-test-link": "test_or_assignment",
}
EXTERNAL_NOTICE = re.compile(
    r"请勿在此处提交|仅做通知提醒|do not submit (?:here|on blackboard|on bb)|notification only",
    re.IGNORECASE,
)


class _ReadError(ValueError):
    """An incomplete or unexpected BB response, with a safe diagnostic reason."""


def _timestamp(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise _ReadError("Invalid Blackboard timestamp") from exc
    if parsed.tzinfo is None:
        raise _ReadError("Blackboard timestamp has no time zone")
    return parsed.astimezone(CST)


def _iso(value: str | None) -> str | None:
    parsed = _timestamp(value)
    return parsed.isoformat() if parsed else None


def _course_id(value: str) -> str:
    if re.fullmatch(r"\d+", value):
        return f"_{value}_1"
    if re.fullmatch(r"_\d+_\d+", value):
        return value
    raise ValueError("Course IDs must be numeric or Blackboard IDs such as _123_1")


class _Reader:
    def __init__(self, session: requests.Session):
        self.session = session
        self.path = ""

    def get(self, path: str) -> dict:
        self.path = path
        response = self.session.get(BASE + path, timeout=_net.service_timeout("bb"))
        response.raise_for_status()
        if "json" not in response.headers.get("content-type", "").lower():
            raise _ReadError("Blackboard returned non-JSON content")
        data = response.json()
        if not isinstance(data, dict):
            raise _ReadError("Blackboard returned an invalid JSON object")
        return data

    def pages(self, path: str) -> list[dict]:
        rows: list[dict] = []
        visited: set[str] = set()
        while path:
            if path in visited:
                raise _ReadError("Blackboard pagination repeated a page")
            visited.add(path)
            data = self.get(path)
            page = data.get("results")
            if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
                raise _ReadError("Blackboard collection has no valid results list")
            rows.extend(page)
            paging = data.get("paging") or {}
            if not isinstance(paging, dict):
                raise _ReadError("Invalid Blackboard pagination")
            next_page = paging.get("nextPage")
            if not next_page:
                return rows
            if not isinstance(next_page, str):
                raise _ReadError("Invalid Blackboard next page")
            target = urlsplit(urljoin(BASE + path, next_page))
            if target.scheme != "https" or target.netloc != "bb.sustech.edu.cn":
                raise _ReadError("Blackboard pagination left the expected host")
            path = target.path + ("?" + target.query if target.query else "")
        return rows


def _unknown(report: dict, row: dict, reason: str) -> None:
    report["unknown"].append({**row, "state": "unknown", "reason": reason})


def _problem(report: dict, reader: _Reader | None, stage: str, exc: Exception, row: dict) -> None:
    response = getattr(exc, "response", None)
    # Paths are enough to locate a failed read; omit user IDs and all query values.
    path = urlsplit(reader.path).path if reader else None
    if path:
        path = re.sub(r"/users/[^/]+", "/users/<current-user>", path)
    report["errors"].append(
        {
            **row,
            "stage": stage,
            "error": type(exc).__name__,
            "reason": str(exc) if isinstance(exc, _ReadError) else "Blackboard read failed",
            "http_status": getattr(response, "status_code", None),
            "endpoint": path,
        }
    )


def _terms(reader: _Reader, query: str, now: datetime) -> list[dict]:
    from .courses import term_code

    terms = reader.pages(API + "/terms?limit=200")
    if query.lower() == "current":
        matches = []
        for term in terms:
            duration = (term.get("availability") or {}).get("duration") or {}
            if not duration.get("start") or not duration.get("end"):
                continue
            start, end = _timestamp(duration["start"]), _timestamp(duration["end"])
            if start <= now <= end:
                matches.append(term)
        if len(matches) != 1:
            raise _ReadError(
                "Current BB term is ambiguous or undated; specify --semester or --course"
            )
    else:
        exact = [t for t in terms if str(t.get("id")) in {query, f"_{query}_1"}]
        matches = exact or [
            t
            for t in terms
            if query.lower() in str(t.get("name", "")).lower() or term_code(t) == query
        ]
        if not matches:
            raise _ReadError("No BB term matches the requested semester")
    if any(not isinstance(t.get("id"), str) or not t["id"] for t in matches):
        raise _ReadError("Blackboard term has no ID")
    return matches


def _scope(
    reader: _Reader,
    report: dict,
    user_id: str,
    semester: str | None,
    requested: list[str],
    now: datetime,
) -> list[dict]:
    enrollments = reader.pages(
        API + f"/users/{quote(user_id, safe='')}/courses?expand=course&limit=200"
    )
    terms = _terms(reader, semester, now) if semester else []
    report["scope"]["terms"] = [{"id": t["id"], "name": t.get("name", "")} for t in terms]
    term_ids = {t["id"] for t in terms}
    courses = []
    seen = set()
    enrolled_ids = set()
    for enrollment in enrollments:
        cid = enrollment.get("courseId")
        if not isinstance(cid, str) or not cid:
            _unknown(report, {}, "enrollment_missing_course_id")
            continue
        enrolled_ids.add(cid)
        if cid in seen or (requested and cid not in requested):
            continue
        seen.add(cid)
        course = enrollment.get("course") or {}
        if (
            not isinstance(course, dict)
            or not course.get("name")
            or (semester and not course.get("termId"))
        ):
            try:
                course = reader.get(API + f"/courses/{quote(cid, safe='')}")
            except Exception as exc:
                _problem(report, reader, "course", exc, {"course_id": cid})
                continue
        if semester and not course.get("termId"):
            _unknown(
                report, {"course_id": cid, "course": course.get("name", cid)}, "course_term_unknown"
            )
            continue
        if semester and course["termId"] not in term_ids:
            if requested:
                _unknown(report, {"course_id": cid}, "requested_course_outside_semester")
            continue
        courses.append(
            {"id": cid, "name": course.get("name") or cid, "term_id": course.get("termId")}
        )
    for cid in requested:
        if cid not in enrolled_ids:
            _unknown(report, {"course_id": cid}, "requested_course_not_enrolled")
    return sorted(courses, key=lambda c: (c["name"], c["id"]))


def _attempt_summary(attempt: dict) -> dict:
    score = attempt.get("score")
    raw = score.get("raw") if isinstance(score, dict) else score
    display = score.get("display") if isinstance(score, dict) else attempt.get("text")
    # Newer responses may publish a displayGrade instead of a raw numeric score.
    grade = attempt.get("displayGrade") or {}
    if raw is None:
        raw = grade.get("score") if grade.get("scaleType") == "Score" else None
    if display is None:
        display = grade.get("text")
    if raw == "":
        raw = None
    if display == "":
        display = None
    return {
        "id": attempt.get("id"),
        "status": attempt.get("status"),
        "created": _iso(attempt.get("created")),
        "score": raw,
        "display_score": display,
        "has_score": raw is not None or display is not None,
        "grading_pending": attempt.get("status") in {"NeedsGrading", "NeedsGradingAgain"},
        "exempt": attempt.get("exempt") is True,
    }


def _attempt_state(own: list[dict]) -> str:
    active = [a for a in own if a.get("exempt") is not True]
    statuses = {a.get("status") for a in active}
    if statuses & SUBMITTED:
        return "submitted"
    if not active:
        return "unsubmitted"
    if statuses <= {"InProgress", "NotAttempted"}:
        return "draft" if "InProgress" in statuses else "unsubmitted"
    return "unknown"


def _collect_course(
    reader: _Reader,
    report: dict,
    course: dict,
    user_id: str,
    now: datetime,
    due_from: datetime | None,
    due_until: datetime | None,
) -> None:
    mode = report["mode"]
    label = {"course_id": course["id"], "course": course["name"]}
    prefix = API + f"/courses/{quote(course['id'], safe='')}"
    try:
        columns = reader.pages(prefix + "/gradebook/columns?limit=200")
    except Exception as exc:
        _problem(report, reader, "columns", exc, label)
        return
    report["courses_checked"].append(course)
    columns = [c for c in columns if (c.get("grading") or {}).get("type") == "Attempts"]
    if not columns:
        return
    cells = None
    try:
        grades = reader.pages(prefix + f"/gradebook/users/{quote(user_id, safe='')}?limit=200")
        if any(g.get("userId") != user_id or not g.get("columnId") for g in grades):
            raise _ReadError("Blackboard user grade ownership or column ID is unavailable")
        cells = {g["columnId"]: g for g in grades}
    except Exception as exc:
        _problem(report, reader, "user_grades", exc, label)

    seen = set()
    for column in columns:
        column_id = column.get("id")
        row = {
            **label,
            "column_id": column_id,
            "content_id": column.get("contentId"),
            "name": column.get("name") or "(unnamed assessment)",
            "possible": (column.get("score") or {}).get("possible"),
            "kind": "unknown",
            "due": None,
            "due_source": "gradebook",
        }
        if not isinstance(column_id, str) or not column_id:
            _unknown(report, row, "missing_column_id")
            continue
        if column_id in seen:
            continue
        seen.add(column_id)
        report["attempt_columns_checked"] += 1
        try:
            due = _timestamp(column["grading"].get("due"))
            row["due"] = due.isoformat() if due else None
        except _ReadError as exc:
            _problem(report, None, "due", exc, row)
            if due_from or due_until:
                _unknown(report, row, "due_unavailable_for_window")
                continue
            due = None
        if due_from or due_until:
            if due is None or (due_from and due < due_from) or (due_until and due > due_until):
                continue
        cell = cells.get(column_id, {}) if cells is not None else None
        if cell and cell.get("exempt") is True:
            report["excluded"].append({**row, "state": "exempt"})
            continue
        if mode == "pending" and EXTERNAL_NOTICE.search(row["name"]):
            _unknown(report, row, "external_submission")
            continue
        try:
            attempts = reader.pages(
                prefix + f"/gradebook/columns/{quote(column_id, safe='')}/attempts"
                f"?userId={quote(user_id, safe='')}&limit=200"
            )
        except Exception as exc:
            _problem(report, reader, "attempts", exc, row)
            _unknown(report, row, "attempts_unavailable")
            continue
        own = [a for a in attempts if a.get("userId") == user_id]
        ownership_unknown = any(
            not isinstance(a.get("userId"), str) or not a["userId"] for a in attempts
        )
        state = _attempt_state(own)
        if ownership_unknown:
            _unknown(report, row, "attempt_ownership_unknown")
            if mode == "pending" or state != "submitted":
                continue
        if state == "unknown":
            _unknown(report, row, "attempt_status_unknown")
            continue
        if mode == "pending" and state == "submitted":
            continue
        if mode == "grades" and state != "submitted":
            continue
        row["state"] = state
        summaries = []
        for attempt in sorted(own, key=lambda a: (a.get("created") or "", a.get("id") or "")):
            try:
                summaries.append(_attempt_summary(attempt))
            except _ReadError as exc:
                _problem(report, None, "attempt_time", exc, row)
                summaries.append(_attempt_summary({**attempt, "created": None}))
        row["attempts"] = summaries
        row["scoring_model"] = column["grading"].get("scoringModel")
        row["grade"] = (
            {
                "status": cell.get("status"),
                "score": cell.get("score"),
                "display_score": cell.get("text"),
                "source": "gradebook_user_grade",
            }
            if cell
            else None
        )
        content = None
        if row["content_id"]:
            try:
                content = reader.get(prefix + f"/contents/{quote(row['content_id'], safe='')}")
            except Exception as exc:
                _problem(report, reader, "content", exc, row)
        handler = (content or {}).get("contentHandler") or {}
        row["kind"] = KINDS.get(
            handler.get("id"), "other_assessment" if handler.get("id") else "unknown"
        )
        if mode == "pending":
            reason = None
            if cells is None:
                reason = "exemption_state_unavailable"
            elif content is None:
                reason = "content_unavailable"
            elif handler.get("groupContent") is True:
                reason = "group_submission_unknown"
            elif (content.get("availability") or {}).get("available") == "No":
                reason = "content_unavailable_to_students"
            elif row["kind"] not in KINDS.values():
                reason = "unsupported_assessment_type"
            if reason:
                _unknown(report, row, reason)
                continue
            row["overdue"] = due < now if due else None
        report["items"].append(row)


def query(
    mode: str = "pending",
    *,
    semester: str | None = None,
    course_ids: Sequence[str] = (),
    due_from: datetime | None = None,
    due_until: datetime | None = None,
    session: requests.Session | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Query enrolled-course pending work or submitted attempt grades using GETs.

    With no course IDs or semester, select the unique BB term whose dated
    duration contains ``now``. Explicit course IDs alone need no term inference.
    A semester accepts a BB term ID, name substring or academic code. Optional
    aware due bounds apply only to pending work and exclude undated tasks.

    The JSON-compatible report retains known data on partial failures, but
    ``complete`` is false whenever ``errors`` or ``unknown`` is nonempty. Grades
    include all own attempts; ``grade`` is the site's reported column grade,
    never a locally inferred aggregation. No private config/cache is required.
    """
    if mode not in {"pending", "grades"}:
        raise ValueError("mode must be pending or grades")
    if isinstance(course_ids, str):
        raise ValueError("course_ids must be a sequence of course IDs")
    requested = list(dict.fromkeys(_course_id(c) for c in course_ids))
    if semester is not None:
        semester = semester.strip()
        if not semester:
            raise ValueError("semester cannot be empty")
    elif not requested:
        semester = "current"
    now = now or datetime.now(CST)
    for value in (now, due_from, due_until):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("now and due bounds must include a time zone")
    now = now.astimezone(CST)
    due_from = due_from.astimezone(CST) if due_from else None
    due_until = due_until.astimezone(CST) if due_until else None
    if mode == "grades" and (due_from or due_until):
        raise ValueError("due bounds are only supported for pending work")
    if due_from and due_until and due_from > due_until:
        raise ValueError("due_from must not be later than due_until")
    report = {
        "schema_version": 1,
        "mode": mode,
        "checked_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai",
        "scope": {
            "source": "live_blackboard_enrollments",
            "semester_query": semester,
            "terms": [],
            "requested_course_ids": requested,
            "courses": [],
            "assessment_type": "Attempts",
            "due_from": due_from.isoformat() if due_from else None,
            "due_until": due_until.isoformat() if due_until else None,
            "include_undated": not (due_from or due_until),
        },
        "courses_checked": [],
        "attempt_columns_checked": 0,
        "items": [],
        "excluded": [],
        "unknown": [],
        "errors": [],
        "complete": False,
    }
    reader = None
    try:
        if session is None:
            from .courses import session as authenticated_session

            session = authenticated_session()
        reader = _Reader(session)
        me = reader.get(API + "/users/me")
        uid = me.get("id")
        if not isinstance(uid, str) or not uid:
            raise _ReadError("Blackboard current user could not be identified")
        courses = _scope(reader, report, uid, semester, requested, now)
        report["scope"]["courses"] = courses
    except Exception as exc:
        _problem(report, reader, "scope", exc, {})
        return report
    for course in courses:
        try:
            _collect_course(reader, report, course, uid, now, due_from, due_until)
        except Exception as exc:
            _problem(
                report,
                reader,
                "assessment_data",
                exc,
                {"course_id": course["id"], "course": course["name"]},
            )
    report["items"].sort(key=lambda r: (r["due"] is None, r["due"] or "", r["course"], r["name"]))
    report["complete"] = not report["errors"] and not report["unknown"]
    return report


__all__ = ["query"]
