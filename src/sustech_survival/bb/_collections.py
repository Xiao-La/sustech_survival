"""Complete BB REST collections and current-user attempt filtering."""
from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

BB_BASE = "https://bb.sustech.edu.cn"


def collection(path: str, session, fetch: Callable) -> list[dict]:
    """Read every page, raising when the server has not returned a collection."""
    rows = []
    visited = set()
    while path:
        if path in visited:
            raise ValueError("BB collection pagination repeated a page")
        visited.add(path)
        data = fetch(path, session)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("BB collection response is missing a results list")
        if any(not isinstance(row, dict) for row in data["results"]):
            raise ValueError("BB collection contains an invalid row")
        rows.extend(data["results"])
        paging = data.get("paging", {})
        if not isinstance(paging, dict):
            raise ValueError("BB collection contains invalid pagination")
        next_page = paging.get("nextPage")
        if not next_page:
            break
        if not isinstance(next_page, str):
            raise ValueError("BB collection contains an invalid next page")
        target = urlsplit(urljoin(BB_BASE + path, next_page))
        if target.scheme != "https" or target.netloc != "bb.sustech.edu.cn":
            raise ValueError("BB collection next page is outside Blackboard")
        path = target.path + ("?" + target.query if target.query else "")
    return rows


def current_user_id(session, fetch: Callable) -> str:
    me = fetch("/learn/api/public/v1/users/me", session)
    uid = me.get("id") if isinstance(me, dict) else None
    if not isinstance(uid, str) or not uid:
        raise ValueError("BB current user could not be identified")
    return uid


def user_attempts(course_id: str, column_id: str, session, fetch: Callable) -> list[dict]:
    """A course-wide attempts endpoint must not become another student's status."""
    uid = current_user_id(session, fetch)
    rows = collection(
        f"/learn/api/public/v1/courses/{course_id}/gradebook/columns/{column_id}/attempts",
        session, fetch,
    )
    if any(not isinstance(row.get("userId"), str) or not row["userId"] for row in rows):
        raise ValueError("BB attempt ownership is unavailable")
    mine = [row for row in rows if row["userId"] == uid]
    return sorted(mine, key=lambda row: (row.get("created", ""), row.get("id", "")))
