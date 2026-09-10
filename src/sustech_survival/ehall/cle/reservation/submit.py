"""Submit a CLE reservation — the one real write in this module.

The POST goes to ``T_NKD_YYZX_XSYY_SAVE.do`` with the payload built by
:func:`~sustech_survival.ehall.cle.reservation.wire.build_payload`; the
server's own answer decides the outcome, and its ``S_CODE`` values are
surfaced verbatim (never swallowed, never softened).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .. import schema
from ..policy import CANCEL_LEAD_DAYS, SPECIAL_EMAIL
from .wire import SAVE_PATH, SERVER_CODES


class CleReservationError(Exception):
    """The server refused the reservation (or answered non-success)."""

    def __init__(self, message: str, *, code: Optional[str] = None, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.payload = payload or {}


def _server_code(*texts: str) -> Optional[str]:
    for text in texts:
        for code in SERVER_CODES:
            if code in (text or ""):
                return code
    return None


def submit(client: Any, payload: Dict[str, str]) -> Dict[str, Any]:
    """POST the save payload. Raises :class:`CleReservationError` on refusal.

    ``client`` is a :class:`~sustech_survival.ehall.cle.client.CleClient`;
    the POST rides the same authenticated browser session. Server refusals
    keep the raw ``code``/``msg`` in the message — never softened.
    """
    result = client.session.post_form(SAVE_PATH, payload)
    message = str(result.get("msg") or "")
    code = str(result.get("code") or "")

    refusal = _server_code(message, code)
    if refusal:
        raise CleReservationError(
            f"server refused: {SERVER_CODES[refusal]} "
            f"(code={code!r} msg={message!r})",
            code=code or refusal,
            payload=result,
        )
    if code not in ("0", "200"):
        raise CleReservationError(
            f"unexpected answer: code={code!r} msg={message!r}", payload=result
        )
    return result


def cancellation_note(slot: Optional[schema.CleSlot] = None) -> str:
    """How to undo a reservation, in plain terms.

    Self-cancel goes through the 我的预约 route
    (:func:`~sustech_survival.ehall.cle.reservation.cancel.cancel`), which
    is a status write the app only offers ≥2 days before the session;
    inside that window the Center has to do it.
    """
    lines = [
        f"Cancel with `sustech cle cancel --reservation <WID>` (see `cle mine`) — "
        f"the app allows self-cancel up to {CANCEL_LEAD_DAYS} days before the "
        f"session.",
        f"Closer than {CANCEL_LEAD_DAYS} days, the eHall app refuses and points you "
        f"to the Center: {SPECIAL_EMAIL}",
        f"Two un-cancelled no-shows stop booking for the rest of the semester.",
    ]
    if slot is not None:
        lines.insert(0, f"Reservation: {slot.sksj} ({slot.service or slot.service_code})")
    return "\n".join(lines)
