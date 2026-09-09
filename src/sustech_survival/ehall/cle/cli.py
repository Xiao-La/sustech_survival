"""ehall.cle — Language Help Service CLI."""
from __future__ import annotations

import json

import click

from .client import CleClient, CleError


@click.group(name="cle")
def cli() -> None:
    """语言中心语言指导服务 (Center for Language Education, ehall)."""


@cli.command("semester", help="Active term + service window summary.")
def semester_cmd() -> None:
    client = CleClient()
    try:
        rows = client.service_configs()
        if rows:
            r = rows[0]
            click.echo(
                json.dumps(
                    {
                        "semester": r.get("PZMC") or f"{r.get('XN_DISPLAY','').strip()} {r.get('XQ_DISPLAY','')}".strip(),
                        "service_open": f"{r.get('FWKSRQ')} → {r.get('FWJSRQ')}",
                        "reservations_per_semester": r.get("ZDYYCS"),
                        "slot_headcount": r.get("SJDXZRS"),
                        "config_id": r.get("WID"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            click.echo("no active service configuration rows returned")
    finally:
        client.session.close()


@cli.command("configs", help="Active semester service configurations.")
def configs_cmd() -> None:
    client = CleClient()
    try:
        for row in client.service_configs():
            _print_row(row)
    finally:
        client.session.close()


@cli.command("teachers", help="Service resources (teachers × service types).")
def teachers_cmd() -> None:
    client = CleClient()
    try:
        for row in client.teachers():
            _print_row(row)
    finally:
        client.session.close()


@cli.command("buckets", help="Fixed 25-minute time buckets.")
def buckets_cmd() -> None:
    client = CleClient()
    try:
        for row in client.time_buckets():
            click.echo(f"  {row.get('KS', '?'):>2}  {row.get('SJD', '?')}")
    finally:
        client.session.close()


@cli.command("schedule", help="Weekly course grid (hqxzkcb).")
@click.option("--week", type=int, default=None, help="Week (DJZ) to filter.")
def schedule_cmd(week: int | None) -> None:
    client = CleClient()
    try:
        for row in client.schedule(week=week):
            _print_row(row)
    finally:
        client.session.close()


@cli.command("mine", help="My reservations.")
def mine_cmd() -> None:
    client = CleClient()
    try:
        for row in client.my_reservations():
            _print_row(row)
    finally:
        client.session.close()


@cli.command("book", help="Reserve a slot. PASS --apply with the exact slot fields.")
@click.option("--sksj", required=True, help='Slot datetime range, e.g. "2026-09-11 11:05-11:30".')
@click.option("--sjsz-wid", required=True, help="SJSZ_WID of the slot (from schedule).")
@click.option("--fwzy-id", default=None, help="FWZY_ID of the service resource, if required.")
@click.option("--apply", "do_apply", is_flag=True, help="Actually submit (consequence-rich).")
def book_cmd(sksj: str, sjsz_wid: str, fwzy_id: str | None, do_apply: bool) -> None:
    """Reserve a 25-min slot. Preview prints the payload; --apply submits it."""
    client = CleClient()
    try:
        slot = {"SKSJ": sksj, "SJSZ_WID": sjsz_wid}
        if fwzy_id:
            slot["FWZY_ID"] = fwzy_id
        if do_apply:
            click.echo("Submitting reservation …")
            result = client.reserve(slot)
            click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            click.echo(json.dumps(client.reserve_preview(slot), ensure_ascii=False, indent=2))
            click.echo("(preview only — pass --apply to submit; 3/semester quota, cancel ≥2 days ahead)")
    except CleError as exc:
        raise click.ClickException(str(exc))
    finally:
        client.session.close()


def _print_row(row: dict) -> None:
    text = "  " + " · ".join(f"{k}={v}" for k, v in row.items())
    click.echo(text[:400])
