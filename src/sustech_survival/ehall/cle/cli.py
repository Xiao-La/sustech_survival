"""ehall.cle CLI — 语言中心语言指导服务 (CLE) tutoring reservations.

Reads are free to run. ``book`` is the only write and follows the
destructive-op pattern (iron law #7): ``--dry-run`` by default, ``--commit``
to post, with an interactive confirm unless ``--yes`` is given.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime

import click

from .client import CleClient, CleError
from .policy import POLICY_TEXT, special_request_template
from .reservation import (
    CleCancelError,
    PreflightResult,
    build_cancel_request,
    build_payload,
    cancel,
    cancel_blockers,
    cancellation_note,
    preflight,
    submit,
)
from .reservation.submit import CleReservationError


@click.group(name="cle")
def cli() -> None:
    """语言中心语言指导服务 (Center for Language Education, ehall)."""


def _echo_json(obj) -> None:
    click.echo(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _client() -> CleClient:
    return CleClient()


# ── semester + catalogs ────────────────────────────────────────────────

@cli.command("semester", help="Active term, service window, week and quota.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def semester_cmd(as_json: bool) -> None:
    client = _client()
    try:
        semester = client.semester()
        payload = semester.to_dict()
        payload["quota"] = client.quota().to_dict()
        if as_json:
            _echo_json(payload)
            return
        click.echo(f"{semester.label or '(no label)'}")
        click.echo(
            f"  window : {semester.service_open} → {semester.service_close}"
            f"  [{semester.window_state}]"
        )
        click.echo(
            f"  term   : week {semester.current_week} of {semester.weeks}"
            f"  (week 1 starts {semester.starts_on})"
        )
        click.echo(f"  quota  : {client.quota()}")
        click.echo(f"  slot   : {semester.slot_capacity} student per 25-minute slot")
    finally:
        client.close()


@cli.command("configs", help="Raw service-configuration rows.")
def configs_cmd() -> None:
    client = _client()
    try:
        for row in client.configs():
            _print_row(row)
    finally:
        client.close()


@cli.command("types", help="Service types (指导范围) offered this semester.")
@click.option("--json", "as_json", is_flag=True)
def types_cmd(as_json: bool) -> None:
    client = _client()
    try:
        types = client.service_types()
        if as_json:
            _echo_json([t.__dict__ for t in types])
            return
        for service in types:
            click.echo(f"  {service}")
    finally:
        client.close()


@cli.command("teachers", help="Service resources (teacher × service type × room).")
@click.option("--type", "service", default=None, help="Filter by service type (code or name).")
@click.option("--teacher", default=None, help="Filter by teacher name or staff number.")
@click.option("--json", "as_json", is_flag=True)
def teachers_cmd(service: str | None, teacher: str | None, as_json: bool) -> None:
    client = _client()
    try:
        rows = client.teachers(service=service, teacher=teacher)
        if as_json:
            _echo_json(rows)
            return
        for row in rows:
            click.echo(
                f"  {row.get('FWLX_DISPLAY', '')} {row.get('JSXM', '')} "
                f"({row.get('JSGH', '')})  {row.get('DD', '')}"
            )
    finally:
        client.close()


@cli.command("buckets", help="Fixed 25-minute time buckets.")
def buckets_cmd() -> None:
    client = _client()
    try:
        for key, span in sorted(client.buckets().items(), key=lambda kv: int(kv[0])):
            click.echo(f"  {key:>2}  {span}")
    finally:
        client.close()


@cli.command("schedule", help="Teacher grid for one week (default: current week).")
@click.option("--week", type=int, default=None, help="Teaching week (DJZ). Default: current week.")
@click.option("--json", "as_json", is_flag=True)
def schedule_cmd(week: int | None, as_json: bool) -> None:
    client = _client()
    try:
        if week is None:
            week = client.semester().current_week or 1
            click.echo(f"# week {week} (current)", err=True)
        rows = client.grid(week)
        if as_json:
            _echo_json(rows)
            return
        for row in rows:
            click.echo(
                f"  {row.get('DJT_DISPLAY', '')} 第{row.get('DJJK', '?')}节 "
                f"{row.get('FWLX_DISPLAY', '')} {row.get('JSXM', '')} "
                f"{row.get('SJSZ_WID', '')[:8]}…"
            )
    finally:
        client.close()


# ── availability ───────────────────────────────────────────────────────

@cli.command("slots", help="Free slots for the next working days.")
@click.option("--date", "on_date", default=None, help="One calendar date (YYYY-MM-DD).")
@click.option("--days", type=int, default=5, show_default=True,
              help="How many working days ahead to scan (default: 5).")
@click.option("--type", "service", default=None, help="Service type (code or name, e.g. 写作).")
@click.option("--teacher", default=None, help="Teacher name or staff number.")
@click.option("--all", "show_all", is_flag=True, help="Include taken and offline-only slots.")
@click.option("--json", "as_json", is_flag=True)
def slots_cmd(on_date: str | None, days: int, service: str | None, teacher: str | None,
              show_all: bool, as_json: bool) -> None:
    client = _client()
    try:
        day = _parse_day(on_date)
        slots = client.slots(
            day=day, days=days, service=service, teacher=teacher,
            include_taken=show_all, include_blocked=show_all,
        )
        if as_json:
            _echo_json([s.to_dict() for s in slots])
            return
        if not slots:
            click.echo("no slots matched")
            return
        current = None
        for slot in slots:
            if slot.day != current:
                current = slot.day
                click.echo(f"\n{slot.day}  {slot.weekday_name}")
            click.echo(f"  {slot}")
        click.echo(
            f"\n{len(slots)} slot(s). Book one with: "
            f"sustech cle book --slot <slot_id> (dry-run first)"
        )
    finally:
        client.close()


@cli.command("walkin", help="Free slots left today (walk-in candidates, no quota used).")
@click.option("--type", "service", default=None, help="Service type (code or name).")
@click.option("--json", "as_json", is_flag=True)
def walkin_cmd(service: str | None, as_json: bool) -> None:
    client = _client()
    try:
        slots = client.walkin(service=service)
        if as_json:
            _echo_json([s.to_dict() for s in slots])
            return
        if not slots:
            click.echo("no free slots left today")
            return
        for slot in slots:
            click.echo(f"  {slot}")
    finally:
        client.close()


# ── my data ────────────────────────────────────────────────────────────

@cli.command("mine", help="My reservations.")
@click.option("--all", "show_all", is_flag=True,
              help="Include cancelled (取消) and no-show (缺席) reservations.")
@click.option("--json", "as_json", is_flag=True)
def mine_cmd(show_all: bool, as_json: bool) -> None:
    client = _client()
    try:
        rows = client.my_reservations(include_inactive=show_all)
        if as_json:
            _echo_json([r.to_dict() for r in rows])
            return
        if not rows:
            click.echo("no reservations (cancelled and no-show ones are filtered out"
                       " — use --all to see them)")
        for reservation in rows:
            click.echo(
                f"  {reservation.sksj}  {reservation.status}"
                f"  wid={reservation.wid}"
            )
        click.echo("")
        click.echo(cancellation_note())
    finally:
        client.close()


@cli.command("cancel", help="Cancel a reservation via 我的预约 (dry-run by default).")
@click.option("--reservation", "wid", required=True, help="Reservation WID (or prefix) from `cle mine`.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Print the wire payload and stop (this is the default behaviour).")
@click.option("--commit", "commit", is_flag=True,
              help="Actually send the cancel status write (prompts unless --yes).")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt (with --commit).")
@click.option("--json", "as_json", is_flag=True)
def cancel_cmd(wid: str, dry_run: bool, commit: bool, yes: bool, as_json: bool) -> None:
    """Set the reservation's status to 取消 (YYZT=4) — the app's own cancel path.

    The app refuses inside the 2-day window; those cases must go to the Center.
    """
    dry_run = dry_run or not commit
    client = _client()
    try:
        reservation = client.find_reservation(wid)
        request = build_cancel_request(reservation.wid)
        blockers = cancel_blockers(reservation)

        click.echo(f"reservation: {reservation.sksj}  {reservation.status}")
        click.echo(f"wid        : {reservation.wid}")
        click.echo("")
        click.echo(f"POST {cancel_endpoint()}")
        click.echo(json.dumps(request, ensure_ascii=False, indent=2))
        if blockers:
            click.echo("")
            click.echo("the app would refuse:", err=True)
            for blocker in blockers:
                click.echo(f"  - {blocker}", err=True)
            raise click.ClickException("cancel blocked — see above")

        if dry_run:
            click.echo("")
            click.echo("Dry run — nothing was sent. Re-run with --commit to cancel.")
            return
        if not yes:
            answer = click.prompt("Type 'yes' to send the cancel to eHall").strip()
            if answer != "yes":
                raise click.ClickException("aborted")
        result = cancel(client, reservation.wid)
        if as_json:
            _echo_json(result)
        else:
            click.echo("")
            click.echo(f"cancelled: {reservation.sksj}")
            click.echo("verify in eHall → 我的预约 (the row leaves the default list)")
    except CleCancelError as exc:
        raise click.ClickException(str(exc))
    except CleError as exc:
        raise click.ClickException(str(exc))
    finally:
        client.close()


@cli.command("quota", help="Reservations used / remaining this semester.")
@click.option("--json", "as_json", is_flag=True)
def quota_cmd(as_json: bool) -> None:
    client = _client()
    try:
        quota = client.quota()
        if as_json:
            _echo_json(quota.to_dict())
            return
        click.echo(str(quota))
        click.echo("  same-day walk-in slots do not count toward the limit")
        click.echo("  2 un-cancelled no-shows → semester booking ban")
    finally:
        client.close()


# ── write path ─────────────────────────────────────────────────────────

@cli.command("book", help="Reserve a slot (dry-run by default).")
@click.option("--slot", "slot_id", required=True, help="slot_id from `cle slots` (SJSZ_WID).")
@click.option("--topic", "note", required=True,
              help="预约说明 (YYSM) — required by the server; the session topic (≤500 chars).")
@click.option("--remark", default="", help="备注 (BZ) — optional free text.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Print the wire payload and stop (this is the default behaviour).")
@click.option("--commit", "commit", is_flag=True,
              help="Actually POST the reservation (prompts unless --yes).")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt (with --commit).")
@click.option("--json", "as_json", is_flag=True)
def book_cmd(slot_id: str, note: str, remark: str, dry_run: bool, commit: bool,
             yes: bool, as_json: bool) -> None:
    """Preview or submit one reservation.

    The payload is built the way the app builds it (see
    ``ehall.cle.reservation.wire``); the preview prints it verbatim.
    ``--topic`` fills the required 预约说明 field — putting IELTS/exam prep
    there is allowed, the form caps it at 500 characters.
    """
    dry_run = dry_run or not commit
    client = _client()
    try:
        slot = client.find_slot(slot_id)
        semester = client.semester()
        checks = preflight(client, slot)
        payload = build_payload(slot, semester.config_id, client.identity(),
                                note=note, remark=remark)

        if as_json and dry_run:
            _echo_json({"slot": slot.to_dict(), "preflight": checks.to_dict(),
                        "payload": payload})
            return

        _print_preflight(slot, checks, payload)
        if note:
            click.echo(f"topic      : {note}")

        if not checks.ok:
            raise click.ClickException(
                "the server would refuse this reservation — see the blockers above"
            )
        if dry_run:
            click.echo("")
            click.echo("Dry run — nothing was sent. Re-run with --commit to reserve.")
            return

        if not yes:
            answer = click.prompt(
                f"Type 'yes' to POST this reservation to eHall"
            ).strip()
            if answer != "yes":
                raise click.ClickException("aborted")

        result = submit(client, payload)
        if as_json:
            _echo_json(result)
        else:
            click.echo("")
            click.echo(f"reserved: {slot.sksj}  ({slot.service} / {slot.teacher})")
            click.echo("verify in eHall → 网上服务大厅 → 语言中心语言指导服务 → 我的预约")
            click.echo(cancellation_note(slot))
    except CleReservationError as exc:
        raise click.ClickException(str(exc))
    except CleError as exc:
        raise click.ClickException(str(exc))
    finally:
        client.close()


# ── rules + email-only path ────────────────────────────────────────────

@cli.command("policy", help="Booking rules and the email-only special sessions.")
def policy_cmd() -> None:
    click.echo(POLICY_TEXT)


@cli.command("email", help="Pre-filled email for 特别专项指导 (email-only booking).")
@click.option("--topic", default="", help="What you want help with.")
@click.option("--time", "preferred_time", default="", help="Preferred time (Thursday ≥10:00).")
@click.option("--materials", default="", help="What you will bring (≤500 字 for writing).")
@click.option("--json", "as_json", is_flag=True)
def email_cmd(topic: str, preferred_time: str, materials: str, as_json: bool) -> None:
    client = _client()
    try:
        identity = client.identity()
    except CleError:
        identity = {"name": "", "student_id": ""}
    finally:
        client.close()
    template = special_request_template(
        topic=topic,
        preferred_time=preferred_time,
        materials=materials,
        name=identity.get("name", ""),
        student_id=identity.get("student_id", ""),
    )
    if as_json:
        _echo_json(template)
        return
    click.echo(f"to      : {template['to']}")
    click.echo(f"subject : {template['subject']}")
    click.echo("")
    click.echo(template["body"])
    click.echo("")
    click.echo(f"open it: {template['mailto']}")


# ── helpers ────────────────────────────────────────────────────────────

def _parse_day(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise click.BadParameter(f"expected YYYY-MM-DD, got {text!r}")


def _print_preflight(slot, checks: PreflightResult, payload: dict) -> None:
    click.echo(f"slot       : {slot.sksj}  ({slot.weekday_name}, 第{slot.bucket}节)")
    click.echo(f"service    : {slot.service or slot.service_code}")
    click.echo(f"teacher    : {slot.teacher or slot.teacher_no}")
    click.echo(f"room       : {slot.room}")
    click.echo(f"quota left : {checks.quota_remaining}")
    click.echo("")
    click.echo(f"POST {submit_endpoint()}")
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    if checks.blockers:
        click.echo("")
        click.echo("blockers:", err=True)
        for blocker in checks.blockers:
            click.echo(f"  {blocker}", err=True)
    for note in checks.notes:
        click.echo(f"note: {note}")


def submit_endpoint() -> str:
    from .reservation.wire import SAVE_PATH

    return "https://ehall.sustech.edu.cn" + SAVE_PATH


def cancel_endpoint() -> str:
    from .reservation.cancel import CANCEL_PATH

    return "https://ehall.sustech.edu.cn" + CANCEL_PATH


def _print_row(row: dict) -> None:
    text = "  " + " · ".join(f"{k}={v}" for k, v in row.items())
    click.echo(text[:400])


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
