"""Pre-flight checks for a CLE reservation — the app's own checks, in Python.

The app runs these before it posts (``yuyueWindow.js``):

1. duplicate/conflict query — ``T_NKD_YYZX_XSYY_QUERY`` with ``SKSJ``
   include + ``XSXH`` equal + ``YYZT != 4``; ``totalSize > 0`` → "重复预约".
2. "already started" — ``SKSJ < now`` → refuse.
3. lead time — ``floor((slot_date - today)/1d) < 1`` → "需要提前一天预约".
4. offline-only — the slot's ``SFXXYY == 1`` → show ``TSNR``, refuse.

This module also checks the per-semester quota and the published service
window, which the app enforces server-side. Blockers are advisory: they
mirror what the server will do, and the server's answer always wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .. import schema
from ..policy import CANCEL_LEAD_DAYS, SPECIAL_EMAIL, cancel_window_ok, lead_time_ok

# Blocker codes (stable strings the CLI/tests can assert on).
BLOCK_WINDOW = "service-window"
BLOCK_STARTED = "already-started"
BLOCK_LEAD_TIME = "lead-time"
BLOCK_OFFLINE_ONLY = "offline-only"
BLOCK_TAKEN = "slot-taken"
BLOCK_CONFLICT = "duplicate-reservation"
BLOCK_QUOTA = "quota-exhausted"


@dataclass
class Blocker:
    code: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "message": self.message}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


@dataclass
class PreflightResult:
    """Outcome of the pre-flight checks for one slot."""

    slot: schema.CleSlot
    blockers: List[Blocker] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    quota_remaining: Optional[int] = None

    @property
    def ok(self) -> bool:
        return not self.blockers

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot": self.slot.to_dict(),
            "ok": self.ok,
            "blockers": [b.to_dict() for b in self.blockers],
            "notes": self.notes,
            "quota_remaining": self.quota_remaining,
        }


def preflight(
    client: Any,
    slot: schema.CleSlot,
    now: Optional[datetime] = None,
) -> PreflightResult:
    """Run every client-side check the app runs (plus quota + window).

    ``client`` is a :class:`~sustech_survival.ehall.cle.client.CleClient`.
    """
    now = now or datetime.now()
    result = PreflightResult(slot=slot)

    # service window (app gate: isVaildDateStrInArea(pz.FWKSRQ, pz.FWJSRQ))
    semester = client.semester()
    if semester.window_state != "open":
        result.blockers.append(
            Blocker(
                BLOCK_WINDOW,
                f"the service window is {semester.window_state} "
                f"({semester.service_open} → {semester.service_close})",
            )
        )

    # already started
    if slot.start <= now:
        result.blockers.append(
            Blocker(BLOCK_STARTED, f"the slot has started ({slot.sksj})")
        )
    elif not lead_time_ok(slot.start, now):
        result.blockers.append(
            Blocker(
                BLOCK_LEAD_TIME,
                f"must be booked at least 1 day ahead (slot {slot.sksj})",
            )
        )

    # offline-only flag + notice, straight from the server's slot guard
    guard = client.slot_guard(slot.slot_id)
    if str(guard.get("SFXXYY") or "0") == "1":
        notice = guard.get("TSNR") or "this slot is offline-only"
        result.blockers.append(Blocker(BLOCK_OFFLINE_ONLY, str(notice)))

    # occupancy (fresh read, not the cached slot list)
    occupied = client.occupancy_map(semester.slot_capacity)
    holder = occupied.get((slot.resource_id, slot.sksj), "")
    if holder and holder != client.identity()["student_id"]:
        result.blockers.append(
            Blocker(BLOCK_TAKEN, f"the slot is already booked ({slot.sksj})")
        )

    # duplicate reservation by me
    for reservation in client.my_reservations():
        if reservation.sksj and reservation.sksj == slot.sksj:
            result.blockers.append(
                Blocker(
                    BLOCK_CONFLICT,
                    f"you already hold a reservation for {slot.sksj} "
                    f"(status YYZT={reservation.status_code or '?'})",
                )
            )
            break

    # per-semester quota
    quota = client.quota()
    result.quota_remaining = quota.remaining
    if quota.remaining <= 0:
        result.blockers.append(
            Blocker(
                BLOCK_QUOTA,
                f"no reservations left this semester ({quota.used}/{quota.limit} used)",
            )
        )

    # informational notes
    result.notes.append(
        f"cancelling later requires ≥{CANCEL_LEAD_DAYS} days' notice; "
        f"2 un-cancelled no-shows stop booking for the semester"
    )
    if not cancel_window_ok(slot.start, now):
        result.notes.append(
            f"this slot is already inside the {CANCEL_LEAD_DAYS}-day "
            f"cancellation window"
        )
    result.notes.append(
        f"特别专项指导 (Thursday ≥10:00) is email-only — {SPECIAL_EMAIL}"
    )
    return result
