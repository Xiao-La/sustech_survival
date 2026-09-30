"""Read layer for ehall.cle — 语言中心语言指导服务 (Language Help Service).

Everything here is a read; the booking write path lives in
``ehall.cle.reservation``.

Wire facts verified live 2026-09-10 against ``ehall.sustech.edu.cn``
(app ``yyzxyy``, route ``#/fwyy``):

- EMAP model queries: ``GET /dxggyw/sys/yyzxyy/modules/fwyy/<MODEL>.do``
  returning ``{"datas": {"<MODEL>": {"rows": [...]}}, "code": "0"}``.
- ``hqxzkcb`` REQUIRES ``DJZ`` (week) — without it the backend answers
  ``code: "#E2140600091"`` / ``The param DJZ hasn't setted``.
- ``hqyycs`` returns occupancy: ``FWZY_ID`` (resource id), ``SKSJ``
  (``2026-09-11 11:05-11:30``), ``XSXH``, ``times``.
- ``T_NKD_YYZX_XSYY_QUERY`` is scoped by the app to the caller's own
  rows (``XSXH`` equal + ``YYZT`` not in {4, 5}); this client sends the
  same filter so a missing server-side scope can't leak other students'
  reservations into the output.
- Semester calendar: ``GET /dxggyw/sys/yyzxyy/educational/
  getCurrentSchoolSemester.do?XQ=<XN>-<XN+1>-<XQ>`` → ``QSRQ``
  (week-1 Monday), ``JSZC`` (total weeks), ``XNXQH``.
- Identity: the eHall portal sets ``window.userId`` / ``window.userName``
  on the app page.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from .._session import EhallError, EhallSession
from .policy import VISIBLE_WORKING_DAYS, working_days
from .schema import (
    ACTIVE_STATUSES_EXCLUDED,
    CleQuota,
    CleReservation,
    CleSemester,
    CleServiceType,
    CleSlot,
    parse_sksj,
)

FWYY = "/dxggyw/sys/yyzxyy/modules/fwyy/"
EDU = "/dxggyw/sys/yyzxyy/educational/"

MODEL_CONFIG = "T_NKD_YYZX_FWPZ_QUERY"
MODEL_RESOURCE = "T_NKD_YYZX_FWZY_QUERY"
MODEL_BUCKET = "T_NKD_YYZX_SKSJB_QUERY"
MODEL_RESERVATION = "T_NKD_YYZX_XSYY_QUERY"
MODEL_SLOT_GUARD = "T_NKD_YYZX_FWZY_SJSZ_QUERY"
MODEL_GRID = "hqxzkcb"
MODEL_COURSE_GRID = "hqkcb"
MODEL_OCCUPIED = "hqyycs"


class CleError(EhallError):
    """CLE-domain error."""


class CleClient:
    """Read client for the Language Help Service.

    ``session`` is an :class:`EhallSession`; one is created lazily. Call
    :meth:`close` when done (the CLI does it in a ``finally``).
    """

    def __init__(self, session: Optional[EhallSession] = None,
                 today: Optional[date] = None):
        self.session = session or EhallSession()
        # Injectable clock, same shape as Context(dt=...) / course_when(now=...):
        # the week window is derived from "today", so offline fixtures must pin
        # it or they drift into "week 2" the moment the real calendar moves.
        self._today = today
        self._identity: Optional[Dict[str, str]] = None
        self._buckets: Optional[Dict[str, str]] = None
        self._grid: Dict[int, List[Dict[str, Any]]] = {}
        self._configs: Optional[List[Dict[str, Any]]] = None

    # -- internal ---------------------------------------------------------

    def _query(
        self,
        model: str,
        params: Optional[Dict[str, Any]] = None,
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        url = FWYY + model
        if not url.endswith(".do"):
            url += ".do"
        merged: Dict[str, Any] = {"pageSize": page_size, "pageNumber": 1}
        merged.update(params or {})
        payload = self.session.get_json(url, params=merged)
        return self.session.rows(payload, model)

    def _own_rows_filter(
        self,
        extra: Optional[List[Dict[str, str]]] = None,
        include_inactive: bool = False,
    ) -> str:
        """The app's own ``querySetting`` for "my reservations".

        ``include_inactive`` drops the ``YYZT`` exclusions so cancelled
        (``4``) and no-show (``5``) rows come back too — the only way to
        prove a cancel landed.
        """
        sid = self.identity()["student_id"]
        clauses: List[Dict[str, str]] = [
            {"name": "XSXH", "value": sid, "builder": "equal", "linkOpt": "AND"}
        ]
        if not include_inactive:
            for code in ACTIVE_STATUSES_EXCLUDED:
                clauses.append(
                    {"name": "YYZT", "value": code, "builder": "notEqual", "linkOpt": "AND"}
                )
        clauses.extend(extra or [])
        return json.dumps(clauses)

    # -- identity + semester ---------------------------------------------

    def identity(self) -> Dict[str, str]:
        """The signed-in student (``student_id`` from ``window.userId``)."""
        if self._identity is None:
            raw = self.session.identity()
            self._identity = {
                "student_id": str(raw.get("student_id") or ""),
                "name": str(raw.get("name") or ""),
            }
        return self._identity

    def configs(self) -> List[Dict[str, Any]]:
        """Raw service-configuration rows for the active semester."""
        if self._configs is None:
            self._configs = self._query(MODEL_CONFIG, {"SFZZSY": 1})
        return self._configs

    def semester_calendar(
        self, semester_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """``QSRQ`` / ``JSZC`` / ``XNXQH`` for the current term."""
        if semester_code is None:
            config = self.configs()[0] if self.configs() else {}
            xn = str(config.get("XN") or "")
            xq = str(config.get("XQ") or "")
            semester_code = f"{xn}-{int(xn) + 1}-{xq}" if xn else ""
        rows = self.session.rows(
            self.session.get_json(
                EDU + "getCurrentSchoolSemester.do",
                params={"XQ": semester_code} if semester_code else None,
            ),
            "pageAction",
        )
        if not rows:
            raise CleError(
                "semester calendar returned no rows (pass a semester code like 2026-2027-1)"
            )
        return rows[0]

    def semester(self) -> CleSemester:
        """Active semester + window + week context."""
        configs = self.configs()
        if not configs:
            raise CleError("no active service configuration (SFZZSY=1) returned")
        sem = CleSemester.from_rows(configs[0], self.semester_calendar())
        if sem.starts_on:
            sem.current_week = self.week_of(self.today(), sem)
        return sem

    def today(self) -> date:
        """The client's clock — the injected date, else the real one."""
        return self._today or date.today()

    def week_of(self, day: date, semester: Optional[CleSemester] = None) -> int:
        """Teaching week number for ``day`` (week 1 starts on ``QSRQ``)."""
        sem = semester or self.semester()
        if not sem.starts_on:
            raise CleError("semester start date (QSRQ) unknown — cannot compute weeks")
        return (day - sem.starts_on).days // 7 + 1

    def day_of(self, week: int, weekday: int, semester: Optional[CleSemester] = None) -> date:
        """Calendar date for ``week`` (1-based) + ``weekday`` (1=Mon … 7=Sun)."""
        sem = semester or self.semester()
        if not sem.starts_on:
            raise CleError("semester start date (QSRQ) unknown — cannot compute dates")
        return sem.starts_on + timedelta(days=(week - 1) * 7 + (weekday - 1))

    # -- catalogs ---------------------------------------------------------

    def buckets(self) -> Dict[str, str]:
        """``{"1": "08:00-08:25", …}`` — the fixed 25-minute grid."""
        if self._buckets is None:
            rows = self._query(MODEL_BUCKET, {"*order": "+KS"})
            self._buckets = {
                str(row.get("KS")): str(row.get("SJD") or "") for row in rows
            }
        return self._buckets

    def resources(self) -> List[Dict[str, Any]]:
        """Service resources: one row per teacher × service type × room."""
        return self._query(MODEL_RESOURCE, {"SFZZSY": 1})

    def service_types(self) -> List[CleServiceType]:
        """Distinct 指导范围 (service types) offered this semester, live-derived."""
        counts: Dict[str, int] = {}
        names: Dict[str, str] = {}
        for row in self.resources():
            code = str(row.get("FWLX") or "")
            counts[code] = counts.get(code, 0) + 1
            names.setdefault(code, str(row.get("FWLX_DISPLAY") or "").strip("()"))
        return [
            CleServiceType(code=code, name=names.get(code, ""), teachers=count)
            for code, count in sorted(counts.items(), key=lambda kv: -kv[1])
        ]

    def teachers(
        self, service: Optional[str] = None, teacher: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Resources filtered by service type and/or teacher (substring match)."""
        rows = self.resources()
        if service:
            rows = [
                r for r in rows if _matches(str(r.get("FWLX") or ""), service)
                or _matches(str(r.get("FWLX_DISPLAY") or ""), service)
            ]
        if teacher:
            rows = [
                r for r in rows if _matches(str(r.get("JSXM") or ""), teacher)
                or _matches(str(r.get("JSGH") or ""), teacher)
            ]
        return rows

    # -- schedule + availability -----------------------------------------

    def grid(self, week: int) -> List[Dict[str, Any]]:
        """Teacher grid for one teaching week (``hqxzkcb``).

        The backend answers ``DJZ=N`` with a **three-week window**
        (weeks N, N+1, N+2 — verified 2026-09-10: ``DJZ=1`` → 376 rows
        spanning weeks 1–3), so the rows are filtered back down to the
        requested week here. Getting this wrong labels next week's slots
        with this week's dates.
        """
        if week not in self._grid:
            rows = self._query(MODEL_GRID, {"DJZ": week}, page_size=2000)
            if not rows:
                raise CleError(
                    f"no grid rows for week {week} — check the week number "
                    "(week 1 starts at the semester start date)"
                )
            exact = [r for r in rows if str(r.get("DJZ")) == str(week)]
            if not exact:
                raise CleError(
                    f"the grid window anchored at week {week} returned weeks "
                    f"{sorted({str(r.get('DJZ')) for r in rows})} — no rows for week {week}"
                )
            self._grid[week] = exact
        return self._grid[week]

    def occupied(self) -> List[Dict[str, Any]]:
        """Occupancy rows (``hqyycs``): resource id, slot datetime, student id, times."""
        return self._query(MODEL_OCCUPIED)

    def occupancy_map(self, capacity: Optional[int] = None) -> Dict[tuple, str]:
        """``{(resource_id, "SKSJ"): student_id}`` for slots at/over capacity."""
        cap = capacity if capacity is not None else self.semester().slot_capacity
        out: Dict[tuple, str] = {}
        for row in self.occupied():
            try:
                times = int(float(row.get("times") or 0))
            except (TypeError, ValueError):
                times = 0
            if times >= cap:
                key = (str(row.get("FWZY_ID") or ""), str(row.get("SKSJ") or ""))
                out[key] = str(row.get("XSXH") or "")
        return out

    def slots(
        self,
        day: Optional[date] = None,
        days: int = VISIBLE_WORKING_DAYS,
        service: Optional[str] = None,
        teacher: Optional[str] = None,
        include_taken: bool = False,
        include_blocked: bool = False,
    ) -> List[CleSlot]:
        """Bookable slots for the next ``days`` working days.

        Join: the weekly grid gives every teacher × weekday × bucket
        instance; ``hqyycs`` marks the instances already at capacity
        (``slot_capacity``). Without ``day`` the window starts tomorrow —
        today's free slots are walk-ins (:meth:`walkin`), since booking
        needs ≥1 day of lead time. ``day`` pins one calendar date instead.
        Offline-only slots (``SFXXYY == 1``) are excluded unless
        ``include_blocked`` — those cannot be booked online.
        """
        if day is not None:
            targets = [day]
        else:
            targets = working_days(self.today() + timedelta(days=1), days)

        buckets = self.buckets()
        sem = self.semester()
        occ = self.occupancy_map(sem.slot_capacity)
        mine = {
            str(r.get("SKSJ") or "")
            for r in self._reservation_rows()
        }

        out: List[CleSlot] = []
        for target in targets:
            week = self.week_of(target, sem)
            try:
                rows = self.grid(week)
            except CleError:
                continue
            weekday = target.isoweekday()
            for row in rows:
                if int(row.get("DJT") or 0) != weekday:
                    continue
                slot = CleSlot.from_grid_row(row, target, buckets)
                if service and not (
                    _matches(slot.service_code, service) or _matches(slot.service, service)
                ):
                    continue
                if teacher and not (
                    _matches(slot.teacher, teacher) or _matches(slot.teacher_no, teacher)
                ):
                    continue
                slot.occupied_by = occ.get((slot.resource_id, slot.sksj), "")
                if slot.sksj in mine:
                    slot.occupied_by = self.identity()["student_id"] or "me"
                if slot.offline_only and not include_blocked:
                    continue
                if slot.occupied_by and not include_taken:
                    continue
                out.append(slot)
        out.sort(key=lambda s: (s.start, s.service, s.teacher))
        return out

    def walkin(self, service: Optional[str] = None) -> List[CleSlot]:
        """Today's still-free slots — the walk-in candidates (don't count on quota)."""
        return self.slots(
            day=self.today(), days=1, service=service, include_blocked=False
        )

    def find_slot(self, slot_id: str, weeks_ahead: int = 6) -> CleSlot:
        """Resolve a ``slot_id`` (``SJSZ_WID``, or an unambiguous prefix) to a slot.

        Prefixes are accepted because the CLI shows the first 8 characters;
        an ambiguous prefix raises rather than guessing.
        """
        sem = self.semester()
        start_week = sem.current_week or 1
        buckets = self.buckets()
        occ = self.occupancy_map(sem.slot_capacity)
        wanted = (slot_id or "").strip().lower()
        exact: Optional[CleSlot] = None
        partial: List[CleSlot] = []
        for week in range(max(start_week, 1), start_week + weeks_ahead):
            try:
                rows = self.grid(week)
            except CleError:
                continue
            for row in rows:
                candidate = str(row.get("SJSZ_WID") or "")
                if not candidate or not candidate.lower().startswith(wanted):
                    continue
                weekday = int(row.get("DJT") or 0)
                slot = CleSlot.from_grid_row(row, self.day_of(week, weekday, sem), buckets)
                slot.occupied_by = occ.get((slot.resource_id, slot.sksj), "")
                if candidate.lower() == wanted:
                    exact = slot
                partial.append(slot)
        if exact is not None:
            return exact
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            raise CleError(
                f"{slot_id!r} matches {len(partial)} slots — pass more characters"
            )
        raise CleError(
            f"no slot with SJSZ_WID {slot_id!r} in weeks "
            f"{max(start_week, 1)}–{start_week + weeks_ahead - 1}"
        )

    def slot_guard(self, slot_id: str) -> Dict[str, Any]:
        """Server-side per-slot guard row (``SFXXYY`` offline flag + ``TSNR`` notice)."""
        rows = self._query(MODEL_SLOT_GUARD, {"WID": slot_id})
        return rows[0] if rows else {}

    # -- my reservations + quota ------------------------------------------

    def _reservation_rows(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        return self._query(
            MODEL_RESERVATION,
            {"querySetting": self._own_rows_filter(include_inactive=include_inactive)},
        )

    def my_reservations(self, include_inactive: bool = False) -> List[CleReservation]:
        """My own reservations (server-scoped by ``XSXH``; cancelled excluded).

        ``include_inactive=True`` also returns cancelled (``4``) and
        no-show (``5``) rows — the proof surface for a cancel.
        """
        return [
            CleReservation.from_row(r)
            for r in self._reservation_rows(include_inactive=include_inactive)
        ]

    def find_reservation(self, wid: str) -> CleReservation:
        """Resolve a reservation WID (or an unambiguous prefix).

        ``WID`` is what cancel needs; the CLI shows it in ``cle mine``.
        """
        wanted = (wid or "").strip().lower()
        rows = self.my_reservations()
        exact = [r for r in rows if r.wid.lower() == wanted]
        if exact:
            return exact[0]
        partial = [r for r in rows if r.wid.lower().startswith(wanted)] if wanted else []
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            raise CleError(f"{wid!r} matches {len(partial)} reservations — pass more characters")
        raise CleError(
            f"no active reservation with WID {wid!r} (cancelled ones are filtered out)"
        )

    def quota(self) -> CleQuota:
        """Used / remaining reservations for this semester."""
        sem = self.semester()
        used = len(self.my_reservations())
        return CleQuota(limit=sem.quota, used=used, window_state=sem.window_state)

    def close(self) -> None:
        self.session.close()


def _matches(haystack: str, needle: str) -> bool:
    return needle.strip().lower() in (haystack or "").lower()
