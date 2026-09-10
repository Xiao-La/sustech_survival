"""Cancel a CLE reservation — the 我的预约 (``wdyy``) route, not ``fwyy``.

Captured 2026-09-10 from the app's own 我的预约 bundle:

``*/modules/wdyy/wdyy.js`` (``actionCancel``) shows the cancel link only
when ``rowData.YYZT == 1``, refuses when the slot is under two days away
(``if (days < 2) alert(i18n('tips.lxgly'))`` — i.e. "contact the
administrator"), then calls:

```js
var params = {WID: data.WID, YYZT: 4, isAdminCancel: false};  // 取消
bs.cancelYy(params)
```

``*/modules/wdyy/wdyyBS.js``:

```js
cancelYy: function(params){
  return BH_UTILS.doAjax('../modules/wdyy/T_NKD_YYZX_XSYY_SAVE.do',
                         { T_NKD_YYZX_XSYY_SAVE: JSON.stringify(params) });
}
```

So the cancel is a **status write**, not a delete: one form field named
after the model, carrying the JSON ``{"WID", "YYZT": 4, "isAdminCancel"}``.
``YYZT = 4`` is the cancelled state the app's own "my reservations" query
filters out. The ``fwyy`` booking module's own ``del`` handler is an
unimplemented TODO — this route is the real one.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .. import schema
from ..policy import CANCEL_LEAD_DAYS, SPECIAL_EMAIL, cancel_window_ok

CANCEL_PATH = "/dxggyw/sys/yyzxyy/modules/wdyy/T_NKD_YYZX_XSYY_SAVE.do"
CANCEL_FIELD = "T_NKD_YYZX_XSYY_SAVE"
CANCELLED_STATUS = "4"


class CleCancelError(Exception):
    """The cancel request was refused or answered non-success."""

    def __init__(self, message: str, *, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.payload = payload or {}


def build_cancel_request(reservation_wid: str) -> Dict[str, str]:
    """The exact form body the app posts to cancel (``isAdminCancel: false``)."""
    if not reservation_wid:
        raise CleCancelError("cancel needs the reservation's WID (see `cle mine`)")
    params = {
        "WID": str(reservation_wid),
        "YYZT": int(CANCELLED_STATUS),
        "isAdminCancel": False,
    }
    return {CANCEL_FIELD: json.dumps(params, separators=(", ", ": "))}


def cancel_blockers(
    reservation: schema.CleReservation, now=None
) -> list[str]:
    """Why the app would refuse to cancel this reservation.

    Two rules, both from the app's own code: the cancel link exists only
    for ``YYZT == 1``, and the client refuses inside the two-day window
    (``days < 2``) with a "contact the administrator" message.
    """
    from datetime import datetime

    problems: list[str] = []
    status = reservation.status_code
    if status and status != "1":
        problems.append(
            f"the app only offers cancel for YYZT=1 (this reservation is "
            f"YYZT={status} — {schema.status_label(status)})"
        )
    day, span = schema.parse_sksj(reservation.sksj)
    if day is None:
        problems.append(f"cannot read the slot time from {reservation.sksj!r}")
        return problems
    start_text = span.split("-")[0] if span else "00:00"
    start = datetime.strptime(f"{day.isoformat()} {start_text}", "%Y-%m-%d %H:%M")
    if not cancel_window_ok(start, now):
        problems.append(
            f"less than {CANCEL_LEAD_DAYS} days before the session — the app "
            f"blocks self-cancel and tells you to contact the Center "
            f"({SPECIAL_EMAIL})"
        )
    return problems


def cancel(client: Any, reservation_wid: str) -> Dict[str, Any]:
    """POST the status write that cancels ``reservation_wid``.

    ``client`` is a :class:`~sustech_survival.ehall.cle.client.CleClient`.
    """
    result = client.session.post_form(CANCEL_PATH, build_cancel_request(reservation_wid))
    code = str(result.get("code") or "")
    if code not in ("0", "200"):
        raise CleCancelError(
            f"cancel failed: code={code!r} msg={result.get('msg')!r}", payload=result
        )
    return result
