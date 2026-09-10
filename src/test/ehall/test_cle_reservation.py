"""Offline tests for the ehall.cle write path (no network, no browser).

Pins the wire shape captured 2026-09-10 from the app's own save branch
(``*/modules/fwyy/yuyueWindow.js``) and the checks the app runs before it
posts. Also pins the *absence* of a cancel action — the app ships none,
so this client must not grow one by accident.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from test_cle_client import CONFIG, FakeSession, grid_row, payload  # noqa: E402

from sustech_survival.ehall.cle import reservation  # noqa: E402
from sustech_survival.ehall.cle.client import CleClient  # noqa: E402
from sustech_survival.ehall.cle.policy import (  # noqa: E402
    SLOT_MINUTES,
    cancel_window_ok,
    lead_time_ok,
    special_request_template,
)
from sustech_survival.ehall.cle.reservation import (  # noqa: E402
    CANCEL_FIELD,
    CANCEL_PATH,
    SERVER_CODES,
    CleCancelError,
    ReservationWireError,
    build_cancel_request,
    build_payload,
    cancel,
    cancel_blockers,
    cancellation_note,
    preflight,
    submit,
)
from sustech_survival.ehall.cle.reservation.submit import (  # noqa: E402
    CleReservationError,
)
from sustech_survival.ehall.cle.reservation.wire import (  # noqa: E402
    PAYLOAD_KEYS,
    SAVE_PATH,
)
from sustech_survival.ehall.cle.schema import (  # noqa: E402
    CleReservation,
    CleSlot,
    status_label,
)

SLOT_ID = "ad5b0372b9254058a346251893f0fcee"
RESOURCE_ID = "35f82030b71745dea8a313747444077c"
IDENTITY = {"student_id": "12413021", "name": "段斯宸"}


def slot(
    day: date = date(2026, 12, 1),
    bucket: str = "7",
    time_range: str = "11:05-11:30",
    slot_id: str = SLOT_ID,
    resource_id: str = RESOURCE_ID,
    **overrides,
) -> CleSlot:
    """A slot shaped exactly like the live grid row used in verification."""
    return CleSlot(
        day=day,
        week=13,
        weekday=2,
        bucket=bucket,
        time_range=time_range,
        teacher_no="30027764",
        teacher="Daniel Billington",
        service_code="A51B975A",
        service="英语指导",
        room="琳恩图书馆二楼203语言中心辅导间",
        slot_id=slot_id,
        resource_id=resource_id,
        **overrides,
    )


def client(**kwargs) -> CleClient:
    models = {
        "T_NKD_YYZX_FWPZ_QUERY": payload("T_NKD_YYZX_FWPZ_QUERY", [CONFIG]),
        "T_NKD_YYZX_SKSJB_QUERY": payload("T_NKD_YYZX_SKSJB_QUERY", []),
    }
    models.update(kwargs.pop("models", {}))
    return CleClient(session=FakeSession(models=models, **kwargs))  # type: ignore[arg-type]


# -- wire shape --------------------------------------------------------------


class TestWirePayloadProbe2026_09_10:
    """Golden payload — the app's own booking POST, captured live 2026-09-10.

    The captured body (urlencoded) is quoted in full in
    ``reservation/wire.py``; this pins the client to the same 30 controls
    in the same order.
    """

    CAPTURED_KEYS = [
        "FWLX_DISPLAY", "FWLX", "FWLS_DISPLAY", "FWLS", "FWZY_DISPLAY", "FWZY",
        "WID", "XSXH", "JSGH", "JSXM", "SKSJ", "KCH", "YYZT_DISPLAY", "YYZT",
        "YYSJ", "YYSM", "WJSCSJ", "BZ", "FWZY_ID", "SCWJ", "FWRQ", "XSXM",
        "PZ_ID", "LSYY_DISPLAY", "LSYY", "WFYXXTX", "YFYJSXXTX",
        "SFGLYQX_DISPLAY", "SFGLYQX", "SJSZ_WID",
    ]

    def test_payload_matches_the_apps_save_branch(self):
        expected = {key: "" for key in self.CAPTURED_KEYS}
        expected.update(
            {
                "FWLX_DISPLAY": "(英语指导)",
                "FWLX": "A51B975A",
                "FWLS_DISPLAY": "Daniel Billington",
                "FWLS": "A51B975A-30027764",
                "FWZY_DISPLAY": "琳恩图书馆二楼203语言中心辅导间",
                "FWZY": RESOURCE_ID,
                "XSXH": "12413021",
                "JSGH": "30027764",
                "JSXM": "Daniel Billington",
                "SKSJ": "2026-12-01 11:05-11:30",
                "KCH": "7",
                "YYZT": "1",
                "YYSM": "IELTS prep",
                "FWZY_ID": RESOURCE_ID,
                "FWRQ": "2026-12-01",
                "XSXM": "段斯宸",
                "PZ_ID": "cfg1",
                "SJSZ_WID": SLOT_ID,
            }
        )
        assert build_payload(slot(), CONFIG["WID"], IDENTITY, note="IELTS prep") == expected

    def test_payload_posts_the_forms_full_control_set(self):
        # the subset the JS assignment block suggests is NOT enough — the
        # server refuses it (#E2140600091); the form posts every control
        payload = build_payload(slot(), CONFIG["WID"], IDENTITY, note="x")
        assert list(payload) == list(PAYLOAD_KEYS) == self.CAPTURED_KEYS
        for key in ("FWLS", "FWZY", "YYSM", "WID", "YYSJ", "SCWJ", "LSYY"):
            assert key in payload

    def test_note_is_required_by_the_server_but_defaults_to_empty(self):
        # the model marks YYSM required; the field is always sent, even blank
        assert build_payload(slot(), CONFIG["WID"], IDENTITY)["YYSM"] == ""

    def test_note_is_capped_at_the_form_limit(self):
        from sustech_survival.ehall.cle.policy import MATERIAL_WORD_LIMIT

        with pytest.raises(ReservationWireError, match="500"):
            build_payload(slot(), CONFIG["WID"], IDENTITY, note="x" * (MATERIAL_WORD_LIMIT + 1))

    def test_remark_maps_to_bz(self):
        payload = build_payload(slot(), CONFIG["WID"], IDENTITY, note="口语", remark="带草稿")
        assert payload["BZ"] == "带草稿"
        assert build_payload(slot(), CONFIG["WID"], IDENTITY, note="口语")["BZ"] == ""

    def test_payload_key_order_matches_the_app(self):
        assert list(build_payload(slot(), CONFIG["WID"], IDENTITY)) == list(PAYLOAD_KEYS)

    def test_save_path_is_the_single_write_endpoint(self):
        assert SAVE_PATH.endswith("/modules/fwyy/T_NKD_YYZX_XSYY_SAVE.do")

    @pytest.mark.parametrize(
        "missing",
        [
            {"slot_id": ""},
            {"bucket": ""},
            {"time_range": ""},
            {"resource_id": ""},
        ],
    )
    def test_incomplete_slots_are_refused(self, missing):
        with pytest.raises(ReservationWireError):
            build_payload(slot(**missing), CONFIG["WID"], IDENTITY)

    def test_missing_config_or_student_is_refused(self):
        with pytest.raises(ReservationWireError):
            build_payload(slot(), "", IDENTITY)
        with pytest.raises(ReservationWireError):
            build_payload(slot(), CONFIG["WID"], {"student_id": "", "name": ""})

    def test_server_codes_cover_every_code_the_app_handles(self):
        assert set(SERVER_CODES) == {
            "S_CODE: 20002",
            "S_CODE: 20004",
            "S_CODE: 20006",
            "S_CODE: 20008",
            "E2140600091",  # EMAP required-param refusal (missing YYSM)
        }

    def test_missing_required_field_error_is_explained(self):
        c = client()
        c.session.post_form = lambda path, data: {  # type: ignore[method-assign]
            "code": "#E2140600091", "msg": "预约失败！"}
        with pytest.raises(CleReservationError, match="YYSM"):
            submit(c, build_payload(slot(), CONFIG["WID"], IDENTITY))


class TestSubmit:
    def test_submit_posts_the_wire_body(self):
        c = client()
        wire = build_payload(slot(), CONFIG["WID"], IDENTITY)
        result = submit(c, wire)
        assert result["code"] == "0"
        assert c.session.posts == [(SAVE_PATH, wire)]  # type: ignore[attr-defined]

    @pytest.mark.parametrize("code", sorted(SERVER_CODES))
    def test_server_refusals_are_surfaced_not_swallowed(self, code):
        c = client()
        c.session.post_form = lambda path, data: {"code": "0", "msg": code}  # type: ignore[method-assign]
        with pytest.raises(CleReservationError) as excinfo:
            submit(c, build_payload(slot(), CONFIG["WID"], IDENTITY))
        # the mapped explanation is present and the raw answer is kept
        assert SERVER_CODES[code] in str(excinfo.value)
        assert excinfo.value.payload.get("msg") == code

    def test_unexpected_answer_is_an_error(self):
        c = client()
        c.session.post_form = lambda path, data: {"code": "500", "msg": "boom"}  # type: ignore[method-assign]
        with pytest.raises(CleReservationError, match="unexpected answer"):
            submit(c, build_payload(slot(), CONFIG["WID"], IDENTITY))


# -- pre-flight --------------------------------------------------------------


class TestPreflight:
    NOW = datetime(2026, 11, 30, 9, 0)

    def _client(self, **kwargs) -> CleClient:
        models = {"hqyycs": payload("hqyycs", kwargs.pop("occupancy", []))}
        models.update(kwargs.pop("models", {}))
        return client(models=models, **kwargs)

    def test_clean_slot_passes_with_notes(self):
        checks = preflight(self._client(), slot(), now=self.NOW)
        assert checks.ok
        assert checks.quota_remaining == 3
        assert any("2 days" in note for note in checks.notes)

    def test_same_day_slot_is_blocked_by_lead_time(self):
        # the app refuses when floor((slot_date - today)/1d) < 1 → tomorrow is fine,
        # today is not (today's free slots are walk-ins)
        checks = preflight(self._client(), slot(day=date(2026, 11, 30)), now=datetime(2026, 11, 30, 9, 0))
        assert "lead-time" in [b.code for b in checks.blockers]

    def test_next_day_slot_is_allowed(self):
        checks = preflight(self._client(), slot(day=date(2026, 12, 1)), now=datetime(2026, 11, 30, 23, 0))
        assert checks.ok

    def test_started_slot_is_blocked(self):
        checks = preflight(self._client(), slot(day=date(2026, 12, 1)), now=datetime(2026, 12, 1, 12, 0))
        assert "already-started" in [b.code for b in checks.blockers]

    def test_taken_slot_is_blocked(self):
        occupancy = [{"FWZY_ID": RESOURCE_ID, "SKSJ": "2026-12-01 11:05-11:30",
                      "XSXH": "12510120", "times": "1"}]
        checks = preflight(self._client(occupancy=occupancy), slot(), now=self.NOW)
        assert "slot-taken" in [b.code for b in checks.blockers]

    def test_my_own_slot_is_not_reported_as_taken(self):
        occupancy = [{"FWZY_ID": RESOURCE_ID, "SKSJ": "2026-12-01 11:05-11:30",
                      "XSXH": "12413021", "times": "1"}]
        checks = preflight(self._client(occupancy=occupancy), slot(), now=self.NOW)
        assert "slot-taken" not in [b.code for b in checks.blockers]

    def test_offline_only_slot_is_blocked_with_its_notice(self):
        guard = payload("T_NKD_YYZX_FWZY_SJSZ_QUERY",
                        [{"WID": SLOT_ID, "SFXXYY": "1", "TSNR": "线下预约，请到现场"}])
        checks = preflight(self._client(models={"T_NKD_YYZX_FWZY_SJSZ_QUERY": guard}),
                           slot(), now=self.NOW)
        blockers = {b.code: b.message for b in checks.blockers}
        assert blockers["offline-only"] == "线下预约，请到现场"

    def test_duplicate_reservation_is_blocked(self):
        c = self._client(reservations=[{"SKSJ": "2026-12-01 11:05-11:30", "YYZT": "1"}])
        checks = preflight(c, slot(), now=self.NOW)
        assert "duplicate-reservation" in [b.code for b in checks.blockers]

    def test_exhausted_quota_is_blocked(self):
        c = self._client(reservations=[
            {"SKSJ": "2026-11-20 11:05-11:30", "YYZT": "1"},
            {"SKSJ": "2026-11-21 11:05-11:30", "YYZT": "1"},
            {"SKSJ": "2026-11-22 11:05-11:30", "YYZT": "1"},
        ])
        checks = preflight(c, slot(), now=self.NOW)
        assert "quota-exhausted" in [b.code for b in checks.blockers]
        assert checks.quota_remaining == 0

    def test_closed_service_window_is_blocked(self):
        closed = dict(CONFIG, FWKSRQ="2026-01-01 09:00:00", FWJSRQ="2026-02-01 18:00:00")
        models = {"T_NKD_YYZX_FWPZ_QUERY": payload("T_NKD_YYZX_FWPZ_QUERY", [closed])}
        checks = preflight(self._client(models=models), slot(), now=self.NOW)
        assert "service-window" in [b.code for b in checks.blockers]

    def test_result_serialises_for_json_output(self):
        checks = preflight(self._client(), slot(), now=self.NOW)
        data = checks.to_dict()
        assert data["ok"] is True
        assert data["slot"]["slot_id"] == SLOT_ID
        assert data["quota_remaining"] == 3


# -- the missing cancel path -------------------------------------------------


class TestCancelWire2026_09_10:
    """Cancel = a status write on the 我的预约 route (``wdyy``), not a delete."""

    WID = "9f2b1c0d4e5f6a7b8c9d0e1f2a3b4c5d"

    def test_cancel_request_is_the_apps_json_status_write(self):
        request = build_cancel_request(self.WID)
        assert list(request) == [CANCEL_FIELD]
        assert json.loads(request[CANCEL_FIELD]) == {
            "WID": self.WID,
            "YYZT": 4,
            "isAdminCancel": False,
        }

    def test_cancel_path_is_the_wdyy_route_not_the_fwyy_del_stub(self):
        # fwyyBS.js's `del` handler is a commented-out TODO using a different
        # model — cancel must not be built on it.
        assert CANCEL_PATH.endswith("/modules/wdyy/T_NKD_YYZX_XSYY_SAVE.do")
        assert "/fwyy/" not in CANCEL_PATH
        root = Path(reservation.__file__).parent
        for path in root.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "T_PXXX_XSJBXX_DELETE" not in text, f"{path.name} uses the TODO stub"

    def test_cancel_needs_a_wid(self):
        with pytest.raises(CleCancelError, match="needs the reservation's WID"):
            build_cancel_request("")

    def test_cancel_posts_once_and_returns_the_result(self):
        c = client()
        result = cancel(c, self.WID)
        assert result["code"] == "0"
        assert c.session.posts == [(CANCEL_PATH, build_cancel_request(self.WID))]  # type: ignore[attr-defined]

    def test_cancel_refusal_is_surfaced(self):
        c = client()
        c.session.post_form = lambda path, data: {"code": "500", "msg": "boom"}  # type: ignore[method-assign]
        with pytest.raises(CleCancelError, match="cancel failed"):
            cancel(c, self.WID)

    def test_cancel_is_blocked_inside_the_two_day_window(self):
        # the app's own guard: `if (days < 2) alert('contact the administrator')`
        reservation = CleReservation.from_row(
            {"WID": self.WID, "YYZT": "1", "SKSJ": "2026-09-11 11:05-11:30"}
        )
        blockers = cancel_blockers(reservation, now=datetime(2026, 9, 10, 9, 0))
        assert any("less than 2 days" in b for b in blockers)

    def test_cancel_is_allowed_four_days_out(self):
        reservation = CleReservation.from_row(
            {"WID": self.WID, "YYZT": "1", "SKSJ": "2026-09-14 11:05-11:30"}
        )
        assert cancel_blockers(reservation, now=datetime(2026, 9, 10, 9, 0)) == []

    def test_cancel_is_blocked_when_the_status_is_not_1(self):
        reservation = CleReservation.from_row(
            {"WID": self.WID, "YYZT": "4", "SKSJ": "2026-09-14 11:05-11:30"}
        )
        blockers = cancel_blockers(reservation, now=datetime(2026, 9, 10, 9, 0))
        assert any("YYZT=1" in b for b in blockers)

    def test_cancellation_note_points_at_the_real_route(self):
        note = cancellation_note(slot())
        assert "sustech cle cancel --reservation" in note
        assert "cle@sustech.edu.cn" in note
        assert "no cancel action" not in note

    def test_status_labels_match_the_app(self):
        assert status_label("1").startswith("未赴约")
        assert status_label("4").startswith("已取消")
        assert status_label("9").startswith("unknown")

    def test_status_codes_are_normalised_from_float_strings(self):
        # the live row came back as YYZT="1.0" (2026-09-10)
        reservation = CleReservation.from_row(
            {"WID": self.WID, "YYZT": "1.0", "SKSJ": "2026-09-14 11:05-11:30"}
        )
        assert reservation.status_code == "1"
        assert reservation.active is True
        assert reservation.status.startswith("未赴约")
        assert cancel_blockers(reservation, now=datetime(2026, 9, 10, 9, 0)) == []


# -- policy helpers ----------------------------------------------------------


class TestPolicy:
    def test_slot_length_matches_the_published_25_minutes(self):
        assert SLOT_MINUTES == 25

    def test_lead_time_needs_a_full_day(self):
        now = datetime(2026, 11, 30, 9, 0)
        assert lead_time_ok(datetime(2026, 12, 1, 9, 0), now) is True
        assert lead_time_ok(datetime(2026, 11, 30, 23, 0), now) is False

    def test_cancel_window_needs_two_days(self):
        now = datetime(2026, 11, 30, 9, 0)
        assert cancel_window_ok(datetime(2026, 12, 2, 9, 0), now) is True
        assert cancel_window_ok(datetime(2026, 12, 1, 9, 0), now) is False

    def test_special_tutoring_email_template(self):
        template = special_request_template(topic="雅思写作", name="段斯宸", student_id="12413021")
        assert template["to"] == "cle@sustech.edu.cn"
        assert template["mailto"].startswith("mailto:cle@sustech.edu.cn?")
        assert "雅思写作" in template["body"]
        assert "500" in template["body"]

    def test_preflight_notes_flag_the_email_only_path(self):
        checks = preflight(client(), slot(), now=datetime(2026, 11, 30, 9, 0))
        assert any("email-only" in note for note in checks.notes)


# -- module surface ----------------------------------------------------------


class TestModuleSurface:
    def test_public_names_are_english(self):
        assert set(reservation.__all__) == {
            "CANCEL_FIELD", "CANCEL_PATH", "CANCELLED_STATUS", "CleCancelError",
            "PreflightResult", "SERVER_CODES", "ReservationWireError",
            "build_cancel_request", "build_payload", "cancel", "cancel_blockers",
            "cancellation_note", "preflight", "submit",
        }
