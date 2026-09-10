"""Booking policy for the CLE Language Help Service (语言中心语言指导服务).

Local snapshot of the published rules. The service itself — and the
Center for Language Education's current semester announcement — stays
authoritative; this text only explains the checks the client performs.

Verified sources (2026-09-10):
- Public announcement: https://twww.sustech.edu.cn/zh/events/language-help-service.html
- Live service config row (``T_NKD_YYZX_FWPZ_QUERY``): 25-minute slots,
  ``ZDYYCS = 3`` reservations per semester, ``SJDXZRS = 1`` student per slot,
  window 2026-09-04 09:00 → 2026-12-25 18:00 (2026-2027 第一学期).
- The app's own client-side checks (``yuyueWindow.js``): conflict query,
  "已到上课时间" reject, "需要提前一天预约" reject (days < 1), and the
  offline-only flag (``SFXXYY == 1``) which blocks online booking and
  shows the slot's ``TSNR`` notice instead.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

# ── rules, as published ────────────────────────────────────────────

SLOT_MINUTES = 25
"""One reservation = one 25-minute slot (config ``SJDXZRS``/bucket grid)."""

LEAD_TIME_DAYS = 1
"""Book at least one calendar day ahead (the app rejects ``days < 1``)."""

CANCEL_LEAD_DAYS = 2
"""Cancel at least two days ahead of the session."""

VISIBLE_WORKING_DAYS = 5
"""Students see the next five working days of slots."""

DEFAULT_QUOTA = 3
"""Reservations per semester; walk-ins are excluded from the count."""

NO_SHOW_STRIKES = 2
"""Two un-cancelled no-shows → no booking for the rest of the semester."""

MATERIAL_WORD_LIMIT = 500
"""Writing/document tutoring material cap (字/words)."""

SPECIAL_EMAIL = "cle@sustech.edu.cn"
"""Contact for 特别专项指导 (Thursday ≥10:00), which is email-only."""

SPECIAL_CATEGORY_NOTE = (
    "特别专项指导 (Thursday, 10:00 onwards) is booked by email to "
    f"{SPECIAL_EMAIL}, not through eHall."
)

POLICY_TEXT = """\
南方科技大学语言中心语言指导服务使用办法（本地快照）
==================================================

以下内容用于解释客户端中实现的预约检查。语言中心发布的最新通知及预约
系统显示的规则优先于本地文本。

1. 每次指导 25 分钟，一次预约对应一个 25 分钟时段。
2. 系统向学生展示未来 5 个工作日的可约时段；至少提前 1 天预约。
3. 每人每学期最多预约 3 次（现场候补预约不计入次数）。
4. 如需取消，应至少提前 2 天操作；2 次未取消爽约后将暂停本学期预约资格。
5. 同一时段重复预约会被拒绝（系统冲突检查）。
6. 文书类指导请自带材料，篇幅不超过 500 字；印好的纸质材料更佳。
7. 特别专项指导（周四 10:00 之后）需邮件预约 cle@sustech.edu.cn，不能
   通过网上服务大厅预约。
8. 地点：琳恩图书馆二楼 203 语言中心辅导间（部分学期有调整，以系统显示
   的地点为准）。

语言指导服务面向全校师生，请按时到达并遵守语言中心的现场安排。具体开放
时间、次数上限及违规处理以语言中心当前通知为准。
"""

__all__ = [
    "SPECIAL_CATEGORY_NOTE",
    "SPECIAL_EMAIL",
    "POLICY_TEXT",
    "SLOT_MINUTES",
    "LEAD_TIME_DAYS",
    "CANCEL_LEAD_DAYS",
    "VISIBLE_WORKING_DAYS",
    "DEFAULT_QUOTA",
    "NO_SHOW_STRIKES",
    "MATERIAL_WORD_LIMIT",
    "cancel_window_ok",
    "lead_time_ok",
]


def lead_time_ok(slot_start: datetime, now: Optional[datetime] = None) -> bool:
    """True when ``slot_start`` is at least ``LEAD_TIME_DAYS`` calendar days away.

    Mirrors the app's own check: ``floor((slot_date - today) / 1 day) >= 1``.
    """
    now = now or datetime.now()
    delta = (slot_start.date() - now.date()).days
    return delta >= LEAD_TIME_DAYS


def cancel_window_ok(slot_start: datetime, now: Optional[datetime] = None) -> bool:
    """True when cancelling now is still ≥ ``CANCEL_LEAD_DAYS`` before the slot.

    The eHall app's own JS has no cancel action (see ``cle/reservation``);
    this is the published rule only, surfaced so the CLI can tell the user
    whether a cancellation is still within the window.
    """
    now = now or datetime.now()
    delta = (slot_start - now).days
    return delta >= CANCEL_LEAD_DAYS


def working_days(start: date, count: int = VISIBLE_WORKING_DAYS) -> list[date]:
    """The next ``count`` working days (Mon–Fri) starting at ``start`` inclusive."""
    out: list[date] = []
    cursor = start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def special_request_template(
    topic: str = "",
    preferred_time: str = "",
    materials: str = "",
    name: str = "",
    student_id: str = "",
) -> dict[str, str]:
    """Pre-filled email for 特别专项指导, which is email-only.

    Returned as ``{"to", "subject", "body", "mailto"}`` so a CLI can print
    it or hand the user a ready ``mailto:`` link. The user sends it; this
    module never mails anything.
    """
    from urllib.parse import quote

    subject = "特别专项指导预约申请" + (f" — {topic}" if topic else "")
    lines = [
        "语言中心：",
        "",
        "你好，我想申请一次特别专项指导（周四 10:00 之后的场次）。",
        "",
    ]
    if topic:
        lines.append(f"指导主题：{topic}")
    if preferred_time:
        lines.append(f"期望时间：{preferred_time}")
    if name or student_id:
        lines.append(f"姓名/学号：{name} {student_id}".strip())
    lines += [
        "",
        f"材料准备：{materials or '可提前提供，篇幅在 500 字以内。'}",
        "",
        "谢谢！",
    ]
    body = "\n".join(lines)
    return {
        "to": SPECIAL_EMAIL,
        "subject": subject,
        "body": body,
        "mailto": f"mailto:{SPECIAL_EMAIL}?subject={quote(subject)}&body={quote(body)}",
    }
