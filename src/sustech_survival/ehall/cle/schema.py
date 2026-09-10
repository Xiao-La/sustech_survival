"""Domain objects for the CLE Language Help Service (eHall app ``yyzxyy``).

The public surface is semantic English (iron law #10). Wire field names
appear only in ``reservation/wire.py`` and in the ``from_*`` constructors
here, which are the translation boundary.

Wire facts verified live 2026-09-10 (see
``sustech-dev/references/ehall-cle-language-help-service.md``):

- ``T_NKD_YYZX_FWPZ_QUERY`` — service config: semester, window, quota,
  slot capacity.
- ``T_NKD_YYZX_FWZY_QUERY`` — service resources = teacher × service type
  × room.
- ``T_NKD_YYZX_SKSJB_QUERY`` — fixed 25-minute buckets (``KS``, ``SJD``).
- ``hqxzkcb`` — weekly grid for one week (``DJZ``); one row per
  teacher × weekday × bucket, carrying ``SJSZ_WID`` (the slot id the
  booking POST needs).
- ``hqyycs`` — occupied slots (``FWZY_ID``, ``SKSJ``, ``XSXH``, ``times``).
- ``T_NKD_YYZX_XSYY_QUERY`` — my reservations; the app filters by
  ``XSXH`` and excludes ``YYZT`` 4 and 5.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

WEEKDAY_NAMES = {
    1: "星期一",
    2: "星期二",
    3: "星期三",
    4: "星期四",
    5: "星期五",
    6: "星期六",
    7: "星期天",
}

ACTIVE_STATUSES_EXCLUDED = ("4", "5")
"""``YYZT`` values the app's own query excludes (cancelled / no-show).

From the app's ``querySetting`` in ``fwyy.js`` / ``yuyueWindow.js``:
``XSXH`` equal + ``YYZT`` notEqual 4 + ``YYZT`` notEqual 5. Only 4 and 5
are filtered, so a reservation in either state no longer counts.
"""

STATUS_LABELS = {
    "1": "未赴约 booked",
    "2": "已赴约 attended",
    "3": "已评价 rated",
    "4": "已取消 cancelled",
    "5": "缺席 no-show",
}
"""Reservation states.

Evidence (2026-09-10): ``wdyy.js`` renders the cancel link for
``YYZT == 1``, the evaluate link for ``2``, view-evaluation for ``3``, and
its cancel handler writes ``YYZT = 4`` with the comment 将预约状态改为 取消;
the 我的预约 filter list offers 未赴约 / 已赴约 / 已评价 / 取消预约 / 缺席 in
that order. A published "2 un-cancelled no-shows → semester ban" rule
implies the no-show state, which is the remaining one (5).
"""


def status_label(code: str) -> str:
    """Human label for a ``YYZT`` code (``"4"`` → ``"已取消 cancelled"``).

    The server returns the status as a numeric string (``"1.0"`` live,
    2026-09-10), so codes are normalised before lookup.
    """
    return STATUS_LABELS.get(normalize_status(code), f"unknown ({code})")


def normalize_status(code: Any) -> str:
    """``"1.0"`` / ``1.0`` / ``"1"`` → ``"1"`` (wire returns floats)."""
    text = str(code if code is not None else "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def parse_sksj(sksj: str) -> "tuple[Optional[date], str]":
    """Split ``"2026-09-11 11:05-11:30"`` into (date, ``"11:05-11:30"``)."""
    text = (sksj or "").strip()
    if not text:
        return None, ""
    parts = text.split(" ", 1)
    try:
        day = datetime.strptime(parts[0], "%Y-%m-%d").date()
    except ValueError:
        return None, parts[-1]
    return day, (parts[1] if len(parts) > 1 else "")


@dataclass
class CleSemester:
    """Active service configuration + calendar context."""

    label: str = ""
    year: str = ""
    term: str = ""
    config_id: str = ""
    service_open: str = ""
    service_close: str = ""
    quota: int = 3
    slot_capacity: int = 1
    semester_code: str = ""
    starts_on: Optional[date] = None
    weeks: Optional[int] = None
    current_week: Optional[int] = None

    @property
    def open(self) -> bool:
        return bool(self.service_open)

    @property
    def window_state(self) -> str:
        """``before`` / ``open`` / ``closed`` / ``unknown`` for the service window."""
        if not self.service_open or not self.service_close:
            return "unknown"
        now = datetime.now()
        start = _parse_dt(self.service_open)
        end = _parse_dt(self.service_close)
        if start and now < start:
            return "before"
        if end and now > end:
            return "closed"
        return "open"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "semester": self.label,
            "service_window": f"{self.service_open} → {self.service_close}",
            "window_state": self.window_state,
            "reservations_per_semester": self.quota,
            "slot_capacity": self.slot_capacity,
            "current_week": self.current_week,
            "weeks_total": self.weeks,
            "semester_start": self.starts_on.isoformat() if self.starts_on else None,
            "config_id": self.config_id,
        }

    @classmethod
    def from_rows(
        cls, config: Dict[str, Any], calendar: Optional[Dict[str, Any]] = None
    ) -> "CleSemester":
        cal = calendar or {}
        starts = _parse_dt(cal.get("QSRQ") or "")
        return cls(
            label=(config.get("PZMC") or "").strip(),
            year=str(config.get("XN") or ""),
            term=str(config.get("XQ") or ""),
            config_id=str(config.get("WID") or ""),
            service_open=str(config.get("FWKSRQ") or ""),
            service_close=str(config.get("FWJSRQ") or ""),
            quota=int(float(config.get("ZDYYCS") or 0)) or 3,
            slot_capacity=int(float(config.get("SJDXZRS") or 0)) or 1,
            semester_code=str(cal.get("XNXQH") or ""),
            starts_on=starts.date() if starts else None,
            weeks=int(cal["JSZC"]) if cal.get("JSZC") is not None else None,
        )


@dataclass
class CleServiceType:
    """A 指导范围 (service type) as offered this semester."""

    code: str
    name: str
    teachers: int = 0

    def __str__(self) -> str:
        return f"{self.name or self.code} ({self.code}) — {self.teachers} teacher(s)"


@dataclass
class CleSlot:
    """One bookable 25-minute slot instance."""

    day: date
    week: int
    weekday: int
    bucket: str
    time_range: str
    teacher_no: str = ""
    teacher: str = ""
    service_code: str = ""
    service: str = ""
    room: str = ""
    room_en: str = ""
    slot_id: str = ""
    resource_id: str = ""
    offline_only: bool = False
    notice: str = ""
    occupied_by: str = ""

    @property
    def sksj(self) -> str:
        """Wire-shaped datetime range, e.g. ``2026-09-11 11:05-11:30``."""
        return f"{self.day.isoformat()} {self.time_range}"

    @property
    def start(self) -> datetime:
        start_text = (self.time_range or "").split("-")[0]
        return datetime.strptime(f"{self.day.isoformat()} {start_text}", "%Y-%m-%d %H:%M")

    @property
    def weekday_name(self) -> str:
        return WEEKDAY_NAMES.get(self.weekday, str(self.weekday))

    @property
    def status(self) -> str:
        """``blocked`` (offline-only) / ``taken`` / ``free``."""
        if self.offline_only:
            return "blocked"
        return "taken" if self.occupied_by else "free"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "when": self.sksj,
            "week": self.week,
            "weekday": self.weekday_name,
            "service": self.service,
            "service_code": self.service_code,
            "teacher": self.teacher,
            "teacher_no": self.teacher_no,
            "room": self.room,
            "status": self.status,
            "notice": self.notice or None,
        }

    def __str__(self) -> str:
        head = f"{self.sksj}  {self.weekday_name}  第{self.bucket}节"
        who = f"{self.service or self.service_code} / {self.teacher or self.teacher_no}"
        tail = self.room or ""
        mark = {"free": "", "taken": "  [taken]", "blocked": "  [offline-only]"}[self.status]
        return f"{head}  {who}  {tail}  id={self.slot_id[:8]}{mark}".strip()

    @classmethod
    def from_grid_row(
        cls, row: Dict[str, Any], day: date, buckets: Dict[str, str]
    ) -> "CleSlot":
        bucket = str(row.get("DJJK") or "")
        return cls(
            day=day,
            week=int(row.get("DJZ") or 0),
            weekday=int(row.get("DJT") or 0),
            bucket=bucket,
            time_range=buckets.get(bucket, ""),
            teacher_no=str(row.get("JSGH") or ""),
            teacher=str(row.get("JSXM") or ""),
            service_code=str(row.get("FWLX") or ""),
            service=str(row.get("FWLX_DISPLAY") or "").strip("()"),
            room=str(row.get("DD") or ""),
            room_en=str(row.get("DD_EN") or ""),
            slot_id=str(row.get("SJSZ_WID") or ""),
            resource_id=str(row.get("WID") or ""),
            offline_only=str(row.get("SFXXYY") or "0") == "1",
            notice=str(row.get("TSNR") or ""),
        )


@dataclass
class CleReservation:
    """One reservation row from ``T_NKD_YYZX_XSYY_QUERY``.

    The field set is inferred from the SAVE payload's own model columns
    (``reservation/wire.py``); no row had been observed at capture time
    (2026-09-10, ``totalSize = 0``), so unknown columns stay in ``raw``.
    """

    raw: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    @property
    def wid(self) -> str:
        """The reservation's primary key — what cancel needs."""
        return str(self.raw.get("WID") or "")

    @property
    def sksj(self) -> str:
        return str(self.raw.get("SKSJ") or "")

    @property
    def slot_id(self) -> str:
        return str(self.raw.get("SJSZ_WID") or "")

    @property
    def status_code(self) -> str:
        return normalize_status(self.raw.get("YYZT"))

    @property
    def status(self) -> str:
        return status_label(self.status_code)

    @property
    def active(self) -> bool:
        return self.status_code not in ACTIVE_STATUSES_EXCLUDED

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "CleReservation":
        return cls(raw=dict(row))

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.raw)


@dataclass
class CleQuota:
    """Per-semester reservation budget."""

    limit: int
    used: int
    window_state: str = "unknown"

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "walkin_note": "same-day walk-in slots do not count toward the limit",
            "no_show_note": "2 un-cancelled no-shows → semester booking ban",
        }

    def __str__(self) -> str:
        return f"{self.used}/{self.limit} used — {self.remaining} left this semester"


def _parse_dt(text: str) -> Optional[datetime]:
    text = (text or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
