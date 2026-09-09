"""ehall.cle — 语言中心语言指导服务 (Language Help Service) client.

Service facts (verified 2026-09-09, see sustech-dev
``references/ehall-clersv-language-help-service.md`` and the module
backlog entry in sustech-architecture):

- E-Hall app path: ``/dxggyw/sys/yyzxyy/*default/index.do#/fwyy``
- EMAP model endpoints under ``/dxggyw/sys/yyzxyy/modules/fwyy/``:
  ``T_NKD_YYZX_FWPZ_QUERY.do`` (semester service configs),
  ``T_NKD_YYZX_FWZY_QUERY.do`` (service resources/teachers),
  ``T_NKD_YYZX_SKSJB_QUERY.do`` (time buckets 08:00–…),
  ``T_NKD_YYZX_XSYY_QUERY.do`` (student reservations),
  ``hqxzkcb.do`` / ``hqkcb.do`` (teacher schedule grids),
  ``hqyycs.do`` (occupied slots).
- Envelope: ``{"datas": {<model>: {"totalSize","rows"}}, "code": "0"}``;
  responses carry ``<FIELD>_DISPLAY`` columns with human text.
- Reserve (write): POST ``T_NKD_YYZX_XSYY_SAVE.do`` with flat fields
  incl. ``SKSJ`` (slot datetime range), ``FWRQ`` (date), ``YYZT=1``,
  ``SJSZ_WID``. Server errors return ``S_CODE: 20006`` (quota 3/semester
  reached) and ``S_CODE: 20002`` (slot full).
- Public policy: 25-min slots, book ≥1 day ahead, 3 reservations per
  semester (walk-ins excluded), cancel ≥2 days ahead, 2 no-show strikes
  → semester ban; 特别专项指导 is email-only (cle@sustech.edu.cn).

Only reads + reserve are implemented; the cancel path's wire call is
not yet captured (the app's own JS has it commented out as TODO).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .._session import EhallSession, EhallError

FWYY = "/dxggyw/sys/yyzxyy/modules/fwyy/"
EDU = "/dxggyw/sys/yyzxyy/educational/"


class CleError(EhallError):
    """CLE-domain error."""


class CleClient:
    """Read/reserve client for the Language Help Service."""

    def __init__(self, session: Optional[EhallSession] = None):
        self.session = session or EhallSession()

    # -- internal ---------------------------------------------------------

    def _query(self, model: str, params: Optional[Dict[str, Any]] = None, page_size: int = 100) -> List[Dict[str, Any]]:
        url = FWYY + model
        if not url.endswith(".do"):
            url += ".do"
        payload = self.session.get_json(url, params={"pageSize": page_size, "pageNumber": 1, **(params or {})})
        return self.session.rows(payload, model)

    # -- reads ------------------------------------------------------------

    def current_semester(self, xn: Optional[str] = None, xq: Optional[str] = None) -> Dict[str, Any]:
        """Current school semester context (XNXQH like 2026-2027-1).

        The endpoint needs the XQ (and often XN) of the active term; pass
        them from a service_configs() row when a bare call returns no rows.
        """
        params = {}
        if xn is not None:
            params["XN"] = xn
        if xq is not None:
            params["XQ"] = xq
        payload = self.session.get_json(EDU + "getCurrentSchoolSemester.do", params=params or None)
        rows = self.session.rows(payload, "pageAction")
        if not rows:
            raise CleError("current semester returned no rows (pass xn/xq from `cle configs`)")
        return _clean(rows[0])

    def service_configs(self) -> List[Dict[str, Any]]:
        """Semester service configurations (periods, quotas)."""
        rows = self._query("T_NKD_YYZX_FWPZ_QUERY", {"SFZZSY": 1})
        return [_clean(r) for r in rows]

    def teachers(self) -> List[Dict[str, Any]]:
        """Service resources (teacher + service type) for the active config."""
        rows = self._query("T_NKD_YYZX_FWZY_QUERY", {"SFZZSY": 1})
        return [_clean(r) for r in rows]

    def time_buckets(self) -> List[Dict[str, Any]]:
        """Fixed 25-minute time buckets (KS 课时, SJD 时间段)."""
        rows = self._query("T_NKD_YYZX_SKSJB_QUERY")
        return [_clean(r) for r in rows]

    def schedule(self, week: Optional[int] = None) -> List[Dict[str, Any]]:
        """Weekly course grid (hqxzkcb); filter by DJZ week if given."""
        params = {"DJZ": week} if week is not None else None
        payload = self.session.get_json(FWYY + "hqxzkcb.do", params=params)
        return [_clean(r) for r in self.session.rows(payload, "hqxzkcb")]

    def my_reservations(self) -> List[Dict[str, Any]]:
        """My reservations (T_NKD_YYZX_XSYY). YYZT == 4 means cancelled."""
        rows = self._query("T_NKD_YYZX_XSYY_QUERY")
        return [_clean(r) for r in rows]

    # -- write (reserve only; cancel wire not yet captured) ---------------

    def reserve_preview(self, slot: Dict[str, Any]) -> Dict[str, Any]:
        """Build the SAVE payload for a schedule slot row (no request sent).

        Required keys on the slot row: SKSJ (e.g. "2026-09-11 11:05-11:30"),
        SJSZ_WID. Additive fields: XSXH/student id + XSXM/name are resolved
        from the session user when not supplied.
        """
        sksj = str(slot.get("SKSJ") or slot.get("sksj") or "")
        sjsz_wid = str(slot.get("SJSZ_WID") or slot.get("sjsz_wid") or "")
        if not sksj or not sjsz_wid:
            raise CleError("reserve needs a schedule slot row with SKSJ and SJSZ_WID")
        payload: Dict[str, Any] = {
            "SKSJ": sksj,
            "FWRQ": sksj[:10],
            "SJSZ_WID": sjsz_wid,
            "YYZT": 1,
            "FWZY_ID": str(slot.get("FWZY_ID") or slot.get("fwzy_id") or ""),
        }
        return payload

    def reserve(self, slot: Dict[str, Any]) -> Dict[str, Any]:
        """Submit a reservation for a schedule slot (real record — confirm first)."""
        payload = self.reserve_preview(slot)
        result = self.session.post_form(FWYY + "T_NKD_YYZX_XSYY_SAVE.do", payload)
        _raise_on_server_code(result)
        return result


def _raise_on_server_code(result: Dict[str, Any]) -> None:
    msg = str(result.get("msg") or "")
    if "S_CODE: 20006" in msg:
        raise CleError("server refused: per-semester reservation quota reached (3/semester, walk-ins excluded)")
    if "S_CODE: 20002" in msg:
        raise CleError("server refused: this time slot is already full")


def _clean(row: Dict[str, Any]) -> Dict[str, Any]:
    """Strip None values for readability (wire keys preserved)."""
    return {k: v for k, v in row.items() if v is not None}
