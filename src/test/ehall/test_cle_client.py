"""Offline tests for the ehall.cle read layer (no network, no browser).

The wire facts these tests pin were captured live 2026-09-10:

- ``hqxzkcb?DJZ=N`` returns a three-week window (weeks N..N+2), so the
  client must filter back to week ``N`` — the bug this test class exists
  for (``TestGridWindowFilter``).
- The default EMAP page size (100) truncates that window, so ``grid``
  requests a large page (``test_grid_requests_a_large_page``).
- ``hqyycs`` marks occupancy with ``times`` vs the config's
  ``SJDXZRS`` capacity.
- ``T_NKD_YYZX_XSYY_QUERY`` must carry the app's own ``querySetting``
  (``XSXH`` equal + ``YYZT`` not in {4,5}) so other students' rows can
  never appear in "my reservations".
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from sustech_survival.ehall.cle.client import CleClient, CleError
from sustech_survival.ehall.cle.schema import CleSlot

CONFIG = {
    "WID": "cfg1",
    "PZMC": "2026-2027学年  第一学期",
    "XN": "2026",
    "XQ": "1",
    "FWKSRQ": "2026-09-04 09:00:00",
    "FWJSRQ": "2026-12-25 18:00:00",
    "ZDYYCS": 3.0,
    "SJDXZRS": 1.0,
    "SFZZSY": "是",
}
CALENDAR = {"QSRQ": "2026-09-07", "JSZC": 23, "XNXQH": "2026-2027-1", "QSZC": 1}
BUCKETS = [
    {"KS": "1", "SJD": "08:00-08:25"},
    {"KS": "6", "SJD": "10:35-11:00"},
    {"KS": "7", "SJD": "11:05-11:30"},
]


def grid_row(week: str, weekday: str, bucket: str, teacher: str = "Conrad Herrera",
             slot_id: str = "", resource_id: str = "res-1", offline: str = "0") -> dict:
    """A row shaped like the real ``hqxzkcb`` payload."""
    return {
        "WID": resource_id,
        "SJSZ_WID": slot_id or f"slot-{week}-{weekday}-{bucket}",
        "DJZ": week,
        "DJT": weekday,
        "DJJK": bucket,
        "JSXM": teacher,
        "JSGH": "30000212",
        "FWLX": "A51B975A",
        "FWLX_DISPLAY": "(英语指导)",
        "DD": "琳恩图书馆二楼203语言中心辅导间",
        "DD_EN": "Room 203, Lynn Library",
        "SFXXYY": offline,
        "TSNR": "" if offline == "0" else "此时间段为线下预约",
    }


def payload(model: str, rows: list) -> dict:
    return {"datas": {model: {"rows": rows}}, "code": "0"}


class FakeSession:
    """Minimal stand-in for :class:`EhallSession` (no browser, no HTTP)."""

    def __init__(self, models: dict | None = None, reservations: list | None = None,
                 student_id: str = "12413021", name: str = "段斯宸"):
        self.models = models or {}
        self.reservations = reservations or []
        self.calls: list[tuple[str, dict]] = []
        self.posts: list[tuple[str, dict]] = []
        self._student_id = student_id
        self._name = name

    def get_json(self, path: str, params=None) -> dict:
        model = path.rsplit("/", 1)[-1].removesuffix(".do")
        self.calls.append((model, dict(params or {})))
        if model == "getCurrentSchoolSemester":
            return payload("pageAction", [CALENDAR])
        if model == "T_NKD_YYZX_XSYY_QUERY":
            return payload(model, self.reservations)
        return self.models.get(model, payload(model, []))

    def rows(self, payload_dict: dict, model: str) -> list:
        datas = payload_dict.get("datas") or {}
        block = datas.get(model) or datas.get("pageAction") or {}
        return list(block.get("rows") or [])

    def identity(self) -> dict:
        return {"student_id": self._student_id, "name": self._name}

    def post_form(self, path: str, data: dict) -> dict:
        self.posts.append((path, data))
        return {"code": "0"}

    def close(self) -> None:  # pragma: no cover - nothing to release
        pass


def client(**kwargs) -> CleClient:
    models = {
        "T_NKD_YYZX_FWPZ_QUERY": payload("T_NKD_YYZX_FWPZ_QUERY", [CONFIG]),
        "T_NKD_YYZX_SKSJB_QUERY": payload("T_NKD_YYZX_SKSJB_QUERY", BUCKETS),
    }
    models.update(kwargs.pop("models", {}))
    # Pin the clock to week 1 (QSRQ 2026-09-07): every fixture row in this file
    # is week 1, and the client's scan starts at its current week.
    today = kwargs.pop("today", date(2026, 9, 8))
    return CleClient(
        session=FakeSession(models=models, **kwargs),  # type: ignore[arg-type]
        today=today)


def calls(c: CleClient) -> list[tuple[str, dict]]:
    """The (model, params) pairs a fake session recorded, in order."""
    assert isinstance(c.session, FakeSession)
    return c.session.calls


# -- semester + week math ----------------------------------------------------


class TestWeekMath:
    """``QSRQ`` anchors week 1 (2026-09-07 = Monday of week 1)."""

    def test_week_of_uses_semester_start(self):
        c = client()
        assert c.week_of(date(2026, 9, 7)) == 1
        assert c.week_of(date(2026, 9, 13)) == 1
        assert c.week_of(date(2026, 9, 14)) == 2

    def test_day_of_inverts_week_of(self):
        c = client()
        for week in (1, 3, 9):
            for weekday in (1, 4, 5):
                day = c.day_of(week, weekday)
                assert c.week_of(day) == week
                assert day.isoweekday() == weekday

    def test_semester_reports_window_and_week(self):
        sem = client().semester()
        assert sem.label.startswith("2026-2027")
        assert sem.quota == 3
        assert sem.slot_capacity == 1
        assert sem.starts_on == date(2026, 9, 7)
        assert sem.weeks == 23
        assert sem.window_state == "open"

    def test_semester_does_not_recurse(self):
        # Regression: semester() → week_of() used to call semester() again
        # (RecursionError inside playwright's inspect.stack()).
        assert client().semester().current_week is not None


# -- grid --------------------------------------------------------------------


class TestGridWindowFilter:
    """``hqxzkcb?DJZ=N`` answers with weeks N..N+2 — the client keeps only N."""

    def test_grid_keeps_only_the_requested_week(self):
        window = [
            grid_row("1", "5", "1", slot_id="w1-fri"),
            grid_row("2", "5", "1", slot_id="w2-fri"),
            grid_row("3", "5", "1", slot_id="w3-fri"),
        ]
        c = client(models={"hqxzkcb": payload("hqxzkcb", window)})
        rows = c.grid(1)
        assert [r["SJSZ_WID"] for r in rows] == ["w1-fri"]

    def test_grid_requests_a_large_page(self):
        # pageSize=100 (the EMAP default) truncated the 376-row window and
        # silently produced an empty day.
        c = client(models={"hqxzkcb": payload("hqxzkcb", [grid_row("1", "5", "1")])})
        c.grid(1)
        model, params = [call for call in calls(c) if call[0] == "hqxzkcb"][-1]
        assert model == "hqxzkcb"
        assert params["DJZ"] == 1
        assert params["pageSize"] >= 1000

    def test_grid_without_the_week_raises(self):
        c = client(models={"hqxzkcb": payload("hqxzkcb", [grid_row("5", "1", "1")])})
        with pytest.raises(CleError):
            c.grid(1)


# -- occupancy join ----------------------------------------------------------


class TestOccupancyJoin:
    OCC = [
        {"FWZY_ID": "res-1", "SKSJ": "2026-09-11 11:05-11:30", "XSXH": "12410812", "times": "1"},
        {"FWZY_ID": "res-2", "SKSJ": "2026-09-11 11:05-11:30", "XSXH": "12532320", "times": "1"},
    ]

    def _client(self, rows, occupancy=None, reservations=None):
        return client(
            models={
                "hqxzkcb": payload("hqxzkcb", rows),
                "hqyycs": payload("hqyycs", occupancy if occupancy is not None else self.OCC),
            },
            reservations=reservations,
        )

    def test_occupancy_map_counts_slots_at_capacity(self):
        c = self._client([])
        occ = c.occupancy_map()
        assert occ[("res-1", "2026-09-11 11:05-11:30")] == "12410812"

    def test_partially_filled_slot_is_not_full_when_capacity_is_2(self):
        occ_rows = [dict(self.OCC[0], times="1")]
        c = self._client([])
        occ = c.occupancy_map(capacity=2)
        assert occ == {}
        _ = occ_rows

    def test_slots_join_marks_taken_and_hides_it_by_default(self):
        rows = [
            grid_row("1", "5", "7", slot_id="taken", resource_id="res-1"),
            grid_row("1", "5", "6", slot_id="free", resource_id="res-9", teacher="LIU Zirui"),
        ]
        c = self._client(rows)
        day = date(2026, 9, 11)
        free = c.slots(day=day)
        assert [s.slot_id for s in free] == ["free"]
        everything = c.slots(day=day, include_taken=True)
        taken = [s for s in everything if s.status == "taken"]
        assert [s.slot_id for s in taken] == ["taken"]
        assert taken[0].occupied_by == "12410812"

    def test_my_own_slot_is_marked_as_mine(self):
        rows = [grid_row("1", "5", "7", slot_id="mine", resource_id="res-1")]
        c = self._client(rows, reservations=[{"SKSJ": "2026-09-11 11:05-11:30", "YYZT": "1"}])
        slot = c.slots(day=date(2026, 9, 11), include_taken=True)[0]
        assert slot.occupied_by == "12413021"

    def test_offline_only_slots_are_excluded_unless_asked(self):
        rows = [grid_row("1", "5", "6", slot_id="offline", offline="1")]
        c = self._client(rows, occupancy=[])
        assert c.slots(day=date(2026, 9, 11)) == []
        blocked = c.slots(day=date(2026, 9, 11), include_blocked=True)
        assert blocked[0].status == "blocked"
        assert blocked[0].notice == "此时间段为线下预约"

    def test_slot_time_range_comes_from_the_bucket_grid(self):
        rows = [grid_row("1", "5", "7", slot_id="x")]
        c = self._client(rows, occupancy=[])
        slot = c.slots(day=date(2026, 9, 11))[0]
        assert slot.sksj == "2026-09-11 11:05-11:30"
        assert slot.start.hour == 11 and slot.start.minute == 5
        assert slot.weekday_name == "星期五"


# -- filters -----------------------------------------------------------------


class TestFilters:
    def test_service_and_teacher_filters(self):
        rows = [
            grid_row("1", "5", "6", slot_id="a", teacher="Conrad Herrera", resource_id="r1"),
            grid_row("1", "5", "7", slot_id="b", teacher="LIU Zirui", resource_id="r2"),
        ]
        c = client(models={
            "hqxzkcb": payload("hqxzkcb", rows),
            "hqyycs": payload("hqyycs", []),
        })
        day = date(2026, 9, 11)
        assert [s.slot_id for s in c.slots(day=day, teacher="zirui")] == ["b"]
        assert [s.slot_id for s in c.slots(day=day, teacher="Herrera")] == ["a"]
        assert [s.slot_id for s in c.slots(day=day, service="英语指导")] == ["a", "b"]
        assert c.slots(day=day, service="法语") == []

    def test_find_slot_accepts_an_unambiguous_prefix(self):
        rows = [
            grid_row("1", "5", "6", slot_id="0086e24faaaa"),
            grid_row("1", "5", "7", slot_id="ae09060ebbbb"),
        ]
        c = client(models={"hqxzkcb": payload("hqxzkcb", rows), "hqyycs": payload("hqyycs", [])})
        assert c.find_slot("0086e24f").slot_id == "0086e24faaaa"

    def test_find_slot_rejects_an_ambiguous_prefix(self):
        rows = [
            grid_row("1", "5", "6", slot_id="0086e24faaaa"),
            grid_row("1", "5", "7", slot_id="0086e24fbbbb"),
        ]
        c = client(models={"hqxzkcb": payload("hqxzkcb", rows), "hqyycs": payload("hqyycs", [])})
        with pytest.raises(CleError, match="matches 2 slots"):
            c.find_slot("0086e24f")

    def test_find_slot_reports_unknown_ids(self):
        c = client(models={"hqxzkcb": payload("hqxzkcb", [grid_row("1", "5", "6", slot_id="aa")]),
                           "hqyycs": payload("hqyycs", [])})
        with pytest.raises(CleError, match="no slot with SJSZ_WID"):
            c.find_slot("zzzz")


# -- my reservations + quota -------------------------------------------------


class TestMyReservations:
    def test_query_is_scoped_to_my_own_rows(self):
        c = client()
        c.my_reservations()
        model, params = [call for call in calls(c)
                         if call[0] == "T_NKD_YYZX_XSYY_QUERY"][-1]
        assert model == "T_NKD_YYZX_XSYY_QUERY"
        clauses = json.loads(params["querySetting"])
        assert {"name": "XSXH", "value": "12413021", "builder": "equal", "linkOpt": "AND"} in clauses
        assert [c for c in clauses if c["name"] == "YYZT"] == [
            {"name": "YYZT", "value": "4", "builder": "notEqual", "linkOpt": "AND"},
            {"name": "YYZT", "value": "5", "builder": "notEqual", "linkOpt": "AND"},
        ]

    def test_history_filter_drops_the_status_exclusions(self):
        c = client()
        c.my_reservations(include_inactive=True)
        model, params = [call for call in calls(c)
                         if call[0] == "T_NKD_YYZX_XSYY_QUERY"][-1]
        clauses = json.loads(params["querySetting"])
        assert clauses == [
            {"name": "XSXH", "value": "12413021", "builder": "equal", "linkOpt": "AND"}
        ]

    def test_quota_counts_active_rows_only(self):
        c = client(reservations=[
            {"SKSJ": "2026-09-11 11:05-11:30", "YYZT": "1"},
            {"SKSJ": "2026-09-18 11:05-11:30", "YYZT": "1"},
        ])
        quota = c.quota()
        assert (quota.limit, quota.used, quota.remaining) == (3, 2, 1)

    def test_reservation_exposes_wire_columns(self):
        c = client(reservations=[{"SKSJ": "2026-09-11 11:05-11:30", "YYZT": "4"}])
        rows = c.my_reservations()
        assert rows[0].sksj == "2026-09-11 11:05-11:30"
        assert rows[0].active is False
        assert rows[0].to_dict()["YYZT"] == "4"


# -- catalogs ----------------------------------------------------------------


class TestCatalogs:
    def test_service_types_are_derived_from_live_resources(self):
        resources = [
            {"FWLX": "A51B975A", "FWLX_DISPLAY": "(英语指导)"},
            {"FWLX": "A51B975A", "FWLX_DISPLAY": "(英语指导)"},
            {"FWLX": "E2698E6B", "FWLX_DISPLAY": "(法语/西班牙语指导)"},
        ]
        c = client(models={"T_NKD_YYZX_FWZY_QUERY": payload("T_NKD_YYZX_FWZY_QUERY", resources)})
        types = c.service_types()
        assert types[0].code == "A51B975A" and types[0].teachers == 2
        assert {t.name for t in types} == {"英语指导", "法语/西班牙语指导"}

    def test_identity_comes_from_the_portal_page(self):
        assert client().identity()["student_id"] == "12413021"


# -- schema helpers ----------------------------------------------------------


class TestSchema:
    def test_parse_sksj_splits_date_and_range(self):
        from sustech_survival.ehall.cle.schema import parse_sksj

        day, span = parse_sksj("2026-09-11 11:05-11:30")
        assert day == date(2026, 9, 11) and span == "11:05-11:30"

    def test_parse_sksj_tolerates_garbage(self):
        from sustech_survival.ehall.cle.schema import parse_sksj

        assert parse_sksj("") == (None, "")

    def test_slot_str_carries_a_copyable_id_prefix(self):
        slot = CleSlot(day=date(2026, 9, 11), week=1, weekday=5, bucket="7",
                       time_range="11:05-11:30", slot_id="ae09060ebbbb", teacher="LIU Zirui")
        assert "id=ae09060e" in str(slot)
        assert slot.status == "free"
