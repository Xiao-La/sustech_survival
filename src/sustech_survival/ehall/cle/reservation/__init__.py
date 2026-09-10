"""The reservation write paths for ``ehall.cle``.

Everything that can change a real record lives here — the read layer in
``cle/client.py`` never posts. Shape follows the destructive-op CLI
pattern (iron law #7): build the exact wire body, run the same
client-side checks the app runs, then submit only on an explicit
``--commit``.

Two writes exist, both verified from the app's own bundles (2026-09-10):

- **book** → ``modules/fwyy/T_NKD_YYZX_XSYY_SAVE.do`` (flat field set,
  built by ``yuyueWindow.js``). See :mod:`.wire`.
- **cancel** → ``modules/wdyy/T_NKD_YYZX_XSYY_SAVE.do`` with a single
  ``T_NKD_YYZX_XSYY_SAVE`` field holding ``{"WID", "YYZT": 4,
  "isAdminCancel": false}`` — a status write, not a delete. See
  :mod:`.cancel`. The booking module's own ``del`` handler in
  ``fwyyBS.js`` is an unimplemented TODO and is deliberately not used.
"""
from .cancel import (
    CANCEL_FIELD,
    CANCEL_PATH,
    CANCELLED_STATUS,
    CleCancelError,
    build_cancel_request,
    cancel,
    cancel_blockers,
)
from .preflight import PreflightResult, preflight
from .submit import cancellation_note, submit
from .wire import SERVER_CODES, ReservationWireError, build_payload

__all__ = [
    "CANCEL_FIELD",
    "CANCEL_PATH",
    "CANCELLED_STATUS",
    "CleCancelError",
    "PreflightResult",
    "SERVER_CODES",
    "ReservationWireError",
    "build_cancel_request",
    "build_payload",
    "cancel",
    "cancel_blockers",
    "cancellation_note",
    "preflight",
    "submit",
]
