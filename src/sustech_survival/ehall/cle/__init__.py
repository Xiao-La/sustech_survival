"""ehall.cle — 语言中心语言指导服务 (CLE Language Help Service).

Reads: :class:`CleClient` (``semester``, ``slots``, ``mine``, ``quota`` …).
Writes: :mod:`sustech_survival.ehall.cle.reservation` (preview + submit,
``--commit`` gated). The eHall student app exposes no cancel action, so
this module has none either.
"""
from .client import CleClient, CleError
from .policy import POLICY_TEXT
from .schema import (
    CleQuota,
    CleReservation,
    CleSemester,
    CleServiceType,
    CleSlot,
)

__all__ = [
    "CleClient",
    "CleError",
    "CleQuota",
    "CleReservation",
    "CleSemester",
    "CleServiceType",
    "CleSlot",
    "POLICY_TEXT",
]
