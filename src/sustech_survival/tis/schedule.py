"""Personal weekly course schedule — xszykb API.

API: POST /xszykb/queryxszykbzhou  (xn, xq, zc)
     POST /xszykb/queryxszykbzong  (xn, xq)  — full semester
     POST /component/querydangqianxnxq  — current semester
     POST /component/querydangqianzc  — current week

No browser/Playwright needed — pure requests.
"""
from __future__ import annotations

import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from sustech_survival.sso import TISAuth
from sustech_survival.exceptions import APIError

# Singleton auth instance to avoid repeated re-auth on every call
_auth_instance = None

def session():
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = TISAuth()
    # ensure() = check() first, only refreshes if expired
    ok, reason = _auth_instance.ensure()
    if not ok:
        raise RuntimeError(f"TIS session error: {reason}")
    return _auth_instance.session


def current_semester() -> dict:
    """Return current semester info:XN, XQ, XNXQ, XNXQ_EN.

    Raises:
        APIError: TIS returned an empty body (server unreachable /
            session gone) or an error-page JSON without XN/XQ (stale
            session — auth re-runs automatically on the next call).
    """
    sess = session()
    r = sess.post('https://tis.sustech.edu.cn/component/querydangqianxnxq',
                  data={})
    body = (r.text or '').strip()
    if not body:
        raise APIError('TIS reported no current semester (empty response). '
                       'The server may be unreachable or the session expired — '
                       'run again and check the login step.')
    try:
        sem = r.json()
    except ValueError:
        raise APIError('TIS returned a non-JSON response for the current '
                       'semester: %r' % body[:160])
    if not isinstance(sem, dict) or 'XN' not in sem or 'XQ' not in sem:
        # A stale session makes TIS answer with an auth-error JSON page
        # (e.g. {"content": "...请用户重新登录页面"}) instead of semester
        # info. Surface that clearly instead of a raw KeyError downstream.
        snippet = body[:200] if body else '(empty body)'
        raise APIError('TIS did not report the current semester (server '
                       'said: %s). The session may have expired — auth retries automatically on the next run; if it persists, run `sustech sso check`.' % snippet)
    return sem


def current_week() -> int:
    """Return current week number (1-18).

    TIS answers with a bare number string (e.g. ``'5'``) only while a
    semester is active. Before the term starts the endpoint returns an
    EMPTY body — every schedule row is still marked 待生效 (pending
    activation) and there is no "current week" yet. A stale session can
    also answer with a JSON error page. None of those are integers, so a
    bare ``int(...)`` explodes with an unhelpful ValueError — parse
    defensively and raise a clear error instead.

    Raises:
        APIError: TIS did not report a current week (term not started, or
            an error page / empty body came back).
    """
    sess = session()
    r = sess.post('https://tis.sustech.edu.cn/component/querydangqianzc',
                  data={})
    body = (r.text or '').strip()
    if body.isdigit():
        return int(body)
    # Stale-session case: TIS answers with a JSON error page telling the
    # user to log in again (verified live). Detect it so the hint says
    # "refresh the session", not "term not started".
    low = body.lower()
    if '登录' in body or 'login' in low or '认证' in body or 'authentication' in low:
        snippet = body[:160] if body else '(empty body)'
        raise APIError(
            'TIS session is not accepted for the schedule query (server '
            'said: %s). Auth retries automatically on the next run; if it persists, run `sustech sso check`.' % snippet
        )
    # Empty body = term not started (every schedule row still 待生效 and
    # there is no "current week" yet). Point at the escape hatches that
    # still work.
    snippet = body[:160] if body else '(empty body)'
    raise APIError(
        'TIS did not report a current week (server said: %s). '
        'The term may not have started yet — schedule rows show 待生效 '
        '(pending activation). Fetch a specific week with --zc N or the '
        'whole term with --all.' % snippet
    )


def week_schedule(zc: int, xn: str | None = None, xq: str | None = None) -> list[dict]:
    """Return personal course schedule for week zc.

    Args:
        zc: Week number (1-18). Pass None to use current week.
        xn: Academic year e.g. "2025-2026". Auto-detected if omitted.
        xq: Semester number (1 or 2). Auto-detected if omitted.

    Returns:
        List of course-entry dicts with keys: SKSJ, SKSJ_EN, KEY, KSJC, JSJC,
        RWH, XB, PYLX, ZC, KCWZSM, SKFS, SFFXEXW, FILEURL.
        KEY is "xq{day}_jc{period}" e.g. "xq2_jc3" = Tuesday period 3.
        ZC is a 36-char bitmap "011111..." indicating which weeks the course runs.
    """
    if xn is None or xq is None:
        sem = current_semester()
        xn = xn or sem['XN']
        xq = xq or sem['XQ']

    sess = session()
    url = 'https://tis.sustech.edu.cn/xszykb/queryxszykbzhou'
    r = sess.post(url, data={'xn': xn, 'xq': xq, 'zc': str(zc)})
    r.raise_for_status()
    return r.json()


def semester_schedule(xn: str | None = None, xq: str | None = None) -> list[dict]:
    """Return full semester schedule (all weeks, all courses).

    Returns: Same dict shape as week_schedule() plus ZC bitmap field.
    """
    if xn is None or xq is None:
        sem = current_semester()
        xn = xn or sem['XN']
        xq = xq or sem['XQ']

    sess = session()
    url = 'https://tis.sustech.edu.cn/xszykb/queryxszykbzong'
    r = sess.post(url, data={'xn': xn, 'xq': xq})
    r.raise_for_status()
    return r.json()


def week_list() -> list[int]:
    """Return list of valid week numbers for the current semester."""
    sess = session()
    r = sess.post('https://tis.sustech.edu.cn/component/queryzclist',
                  data=current_semester())
    return [item['ZC'] for item in r.json()]


# -- Bridge to the academic-calendar layer --------------------------------

_WEEK_TEXT_RE = re.compile(r"\[(\d+)-(\d+)(单|双)?周\]")


def weeks_from_zc(zc: str | None, sksj: str = "", max_week: int = 16) -> tuple[int, ...]:
    """Teaching weeks for one row, from the ``ZC`` bitmap (text as fallback).

    ``ZC`` is a 34-char 0/1 string indexed by 1-based week — position 0 is a
    leading placeholder. Verified live: ``[1-16周]`` →
    ``'0111111111111111100000000000000000'``, ``[1-15单周]`` →
    ``'0101010101010101000000000000000000'``. Only ``semester_schedule()``
    (``queryxszykbzong``) returns it; the per-week endpoint answers ``ZC=None``,
    so the ``SKSJ`` annotation is the fallback.
    """
    if zc:
        weeks = tuple(i for i, c in enumerate(zc[1:], start=1) if c == "1")
        if weeks:
            return weeks
    m = _WEEK_TEXT_RE.search((sksj or "").replace("\n", " "))
    if not m:
        return tuple(range(1, max_week + 1))
    start, end, parity = int(m.group(1)), int(m.group(2)), m.group(3)
    weeks = list(range(start, end + 1))
    if parity == "单":
        weeks = [w for w in weeks if w % 2 == 1]
    elif parity == "双":
        weeks = [w for w in weeks if w % 2 == 0]
    return tuple(weeks)


def class_times(xn: str | None = None, xq: str | None = None) -> list:
    """This term's meetings as ``calendar.ClassTime`` objects.

    Bridges the TIS schedule feed into the academic-calendar layer, which owns
    date intelligence: week parity, holidays, and 补课 (makeup-class) transfer.
    Duplicate rows for one block (TIS repeats a row per ``jc`` slot) collapse —
    identity is (title, weekday, first period, last period).

    Periods come from ``KSJC``/``JSJC`` (NOT the ``jc`` tail in ``KEY``, which is
    only the day's block index).
    """
    from sustech_survival.calendar import ClassTime

    out, seen = [], set()
    for row in semester_schedule(xn, xq):
        sksj = (row.get("SKSJ") or "").replace("\n", " ")
        key = row.get("KEY", "")
        if not key.startswith("xq") or "_jc" not in key:
            continue
        title = sksj.split("[")[0].strip()
        if not title:
            continue
        try:
            weekday = int(key.split("_")[0][2:]) - 1        # 0=Mon .. 6=Sun
            ks, js = int(row.get("KSJC") or 0), int(row.get("JSJC") or 0)
        except (TypeError, ValueError):
            continue
        if ks <= 0 or js < ks:
            continue
        ident = (title, weekday, ks, js)
        if ident in seen:
            continue
        seen.add(ident)
        blocks = re.findall(r"\[([^\]]+)\]", sksj)
        out.append(ClassTime(
            weeks=weeks_from_zc(row.get("ZC"), sksj),
            weekday=weekday,
            periods=tuple(range(ks, js + 1)),
            title=title,
            teacher=blocks[0] if blocks else "",
            room=blocks[-2] if len(blocks) > 2 else "",
        ))
    return out

# NOTE: the standalone argparse CLI was removed 2026-08-10 during the
# CLI unification. Use `sustech tis schedule` (defined inline in
# sustech_survival/tis/cli.py) — it wraps `week_schedule` /
# `semester_schedule` / `current_week` from this module.

