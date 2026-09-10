"""Wire shape for ``T_NKD_YYZX_XSYY_SAVE.do`` (the CLE booking write).

Captured 2026-09-10 from the app's own booking code
(``*/modules/fwyy/yuyueWindow.js``, the save branch):

```js
formData.KCH      = data.courseNum;      // bucket number (KS)
formData.JSGH     = fw.JSGH;             // teacher staff no
formData.JSXM     = fw.JSXM;             // teacher name
formData.FWLX     = fw.FWLX;             // service type code
formData.FWZY_ID  = fw.WID;              // service resource id
formData.PZ_ID    = curPz.WID;           // semester service config id
formData.FWRQ    = formData.SKSJ.substring(0,10);
formData.XSXM     = userName;            // window.userName
formData.XSXH     = userId;              // window.userId
formData.YYZT     = 1;
formData.SJSZ_WID = fw.SJSZ_WID;         // slot id
```

``SKSJ`` ("2026-09-11 11:05-11:30") is set before that block. The app
first re-reads the slot via ``T_NKD_YYZX_FWZY_SJSZ_QUERY`` and aborts when
``SFXXYY == 1`` (offline-only), showing the slot's ``TSNR`` notice.

Let the record show the wire truth (captured from a live app POST, 2026-09-10,
``T_NKD_YYZX_XSYY_SAVE.do``, urlencoded body):

```
FWLX_DISPLAY=(英语指导)&FWLX=A51B975A&FWLS_DISPLAY=CHU Yu&
FWLS=A51B975A-30000599&FWZY_DISPLAY=琳恩图书馆二楼203语言中心辅导间&
FWZY=3c6725b178a84e4b8d04847564bce44b&WID=&XSXH=12413021&JSGH=30000599&
JSXM=CHU Yu&SKSJ=2026-09-14 08:00-08:25&KCH=1&YYZT_DISPLAY=&YYZT=1&YYSJ=&
YYSM=IELTS prep&WJSCSJ=&BZ=&FWZY_ID=3c6725b178a84e4b8d04847564bce44b&SCWJ=&
FWRQ=2026-09-14&XSXM=段斯宸&PZ_ID=aae25ebaba7748e3a446ac046e602666&
LSYY_DISPLAY=&LSYY=&WFYXXTX=&YFYJSXXTX=&SFGLYQX_DISPLAY=&SFGLYQX=&
SJSZ_WID=b34fca86e9c54b138bbd01c14e9a5f9c
```

So the form posts **every control of the loaded model**, display labels and
empty values included — not just the fields ``yuyueWindow.js`` assigns.
Sending the small assigned subset is refused by the server with
``code: "#E2140600091"`` (``msg: 预约失败！``), verified live 2026-09-10.
The reservation model (``modules/fwyy.do`` with ``*json=1``, ``models[4]``)
marks ``YYSM`` (预约说明, the 500-character textarea) as its required
control; ``FWLS``/``FWZY`` carry the teacher/room the form selected, and
``FWZY_ID`` repeats the resource id.

The server answers with ``{"code": "0"}` on success; the app maps these
``msg`` values (its own i18n keys in parentheses):

===============  ===========================================================
``S_CODE``       meaning as the app labels it
===============  ===========================================================
20002            slot at capacity — ``error.yyMaxPeople``
20004            cancel-window rule — ``error.yyMaxcancelTime``
20006            per-semester limit reached — ``error.yyMaxTime``
20008            conflicting reservation — ``error.yyCf``
===============  ===========================================================
"""
from __future__ import annotations

from typing import Any, Dict

from .. import schema
from ..policy import DEFAULT_QUOTA, MATERIAL_WORD_LIMIT

SAVE_PATH = (
    "/dxggyw/sys/yyzxyy/modules/fwyy/T_NKD_YYZX_XSYY_SAVE.do"
)

PAYLOAD_KEYS = (
    "FWLX_DISPLAY",
    "FWLX",
    "FWLS_DISPLAY",
    "FWLS",
    "FWZY_DISPLAY",
    "FWZY",
    "WID",
    "XSXH",
    "JSGH",
    "JSXM",
    "SKSJ",
    "KCH",
    "YYZT_DISPLAY",
    "YYZT",
    "YYSJ",
    "YYSM",
    "WJSCSJ",
    "BZ",
    "FWZY_ID",
    "SCWJ",
    "FWRQ",
    "XSXM",
    "PZ_ID",
    "LSYY_DISPLAY",
    "LSYY",
    "WFYXXTX",
    "YFYJSXXTX",
    "SFGLYQX_DISPLAY",
    "SFGLYQX",
    "SJSZ_WID",
)
"""The exact key set the app's booking form posts, in the captured order."""

EMPTY_MODEL_FIELDS = (
    "WID", "YYZT_DISPLAY", "YYSJ", "WJSCSJ", "BZ", "SCWJ", "LSYY_DISPLAY",
    "LSYY", "WFYXXTX", "YFYJSXXTX", "SFGLYQX_DISPLAY", "SFGLYQX",
)
"""Controls the form always submits blank unless the caller fills them
(PK, timestamps, upload, feedback; ``BZ``/备注 takes a value when given)."""

ACTIVE_STATUS = "1"
"""``YYZT`` value the app writes for a live reservation."""

NOTE_JSON_FIELD = "YYSM"
"""Reservation model control carrying the 预约说明 text (required)."""

SERVER_CODES = {
    "S_CODE: 20002": "the slot is full (app key error.yyMaxPeople)",
    "S_CODE: 20004": "the cancel-window rule was hit (app key error.yyMaxcancelTime)",
    "S_CODE: 20006": (
        f"per-semester reservation limit reached "
        f"({DEFAULT_QUOTA}/semester, walk-ins excluded) — app key error.yyMaxTime"
    ),
    "S_CODE: 20008": "you already hold a reservation for this slot (app key error.yyCf)",
    "E2140600091": (
        "the EMAP save refused the request — the app posts the model's full "
        "control set (FWLS/FWZY/YYSM included); a subset is rejected"
    ),
}


class ReservationWireError(Exception):
    """The payload could not be built from the given slot/config/identity."""


def build_payload(
    slot: schema.CleSlot,
    config_id: str,
    identity: Dict[str, str],
    note: str = "",
    remark: str = "",
) -> Dict[str, str]:
    """Build the SAVE body for ``slot`` exactly as the app's form posts it.

    ``slot`` must carry ``slot_id`` (``SJSZ_WID``), a bucket number, a time
    range and the resource id; ``config_id`` is the active
    ``T_NKD_YYZX_FWPZ_QUERY`` row's ``WID``; ``identity`` is
    :meth:`CleClient.identity`'s output.

    ``note`` fills ``YYSM`` (预约说明) — required by the server and where
    the session topic goes (≤500 chars, matching the form's counter).
    ``remark`` fills ``BZ`` (备注).
    """
    missing = [
        name
        for name, value in (
            ("slot_id (SJSZ_WID)", slot.slot_id),
            ("bucket (KCH)", slot.bucket),
            ("time range (SKSJ)", slot.time_range),
            ("resource id (FWZY_ID/FWZY)", slot.resource_id),
            ("config id (PZ_ID)", config_id),
            ("student id (XSXH)", identity.get("student_id")),
        )
        if not value
    ]
    if missing:
        raise ReservationWireError(
            "cannot build the save payload — missing: " + ", ".join(missing)
        )
    if len(note or "") > MATERIAL_WORD_LIMIT:
        raise ReservationWireError(
            f"YYSM (预约说明) is capped at {MATERIAL_WORD_LIMIT} characters "
            f"by the form counter — got {len(note)}"
        )

    payload = {key: "" for key in EMPTY_MODEL_FIELDS}
    payload.update(
        {
            "FWLX_DISPLAY": f"({slot.service})" if slot.service else "",
            "FWLX": str(slot.service_code),
            # the form's teacher option value: "<FWLX>-<JSGH>"
            "FWLS_DISPLAY": str(slot.teacher),
            "FWLS": f"{slot.service_code}-{slot.teacher_no}",
            "FWZY_DISPLAY": str(slot.room),
            "FWZY": str(slot.resource_id),
            "XSXH": str(identity["student_id"]),
            "JSGH": str(slot.teacher_no),
            "JSXM": str(slot.teacher),
            "SKSJ": slot.sksj,
            "KCH": str(slot.bucket),
            "YYZT": ACTIVE_STATUS,
            "YYSM": note or "",
            "FWZY_ID": str(slot.resource_id),
            "FWRQ": slot.day.isoformat(),
            "XSXM": str(identity.get("name") or ""),
            "PZ_ID": str(config_id),
            "SJSZ_WID": str(slot.slot_id),
        }
    )
    if remark:
        payload["BZ"] = remark
    return {key: payload[key] for key in PAYLOAD_KEYS}

