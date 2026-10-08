"""CLI views of the shared, read-only assessment report."""

from __future__ import annotations

import contextlib
import json
import sys
from datetime import datetime

import click

from .assessments import query


def _bound(value: str | None, option: str) -> datetime | None:
    if value is None:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result
    except ValueError as exc:
        raise click.BadParameter(
            "use an ISO timestamp with a time zone", param_hint=option
        ) from exc


def _render(report: dict) -> None:
    scope = report["scope"]
    terms = ", ".join(t["name"] or t["id"] for t in scope["terms"])
    click.echo(f"Checked: {report['checked_at']} ({report['timezone']})")
    click.echo(f"Scope: {terms or 'explicit enrolled courses'}; {len(scope['courses'])} course(s)")
    if scope["due_from"] or scope["due_until"]:
        click.echo(
            f"Due window: {scope['due_from'] or 'unbounded'} .. "
            f"{scope['due_until'] or 'unbounded'} (undated excluded)"
        )
    else:
        click.echo("Due window: all dates, including overdue and undated assessments")
    click.echo(f"{len(report['items'])} confirmed {report['mode']} assessment(s)")
    for row in report["items"]:
        click.echo(f"\n{row['course']} | {row['name']} [{row['kind']}]")
        click.echo(f"  Due: {row['due'] or 'not provided'}")
        if report["mode"] == "pending":
            late = "; overdue" if row["overdue"] else ""
            click.echo(f"  {row['state']}{late}")
        else:
            possible = row["possible"] if row["possible"] is not None else "?"
            for attempt in row["attempts"]:
                if attempt["grading_pending"]:
                    grade = "awaiting grading"
                    if attempt["score"] is not None:
                        grade += f" (reported score {attempt['score']}/{possible})"
                elif attempt["score"] is not None:
                    grade = f"{attempt['score']}/{possible}"
                elif attempt["display_score"] is not None:
                    grade = str(attempt["display_score"])
                else:
                    grade = "score not provided"
                exempt = "; exempt attempt" if attempt["exempt"] else ""
                click.echo(f"  Attempt {attempt['id']}: {attempt['status']}; {grade}{exempt}")
            if row["grade"] and row["grade"]["score"] is not None:
                click.echo(
                    f"  BB column grade: {row['grade']['score']}/{possible} (reported by BB)"
                )
    for row in report["excluded"]:
        click.echo(f"Exempt: {row['course']} | {row['name']}")
    for row in report["unknown"]:
        click.echo(
            f"Unknown: {row.get('course', row.get('course_id', 'scope'))} | "
            f"{row.get('name', '')}: {row['reason']}"
        )
    for error in report["errors"]:
        click.echo(
            f"Read error: {error['stage']}; {error['reason']}; "
            f"HTTP {error['http_status'] or '?'}; {error['endpoint'] or ''}",
            err=True,
        )
    click.echo(
        "Coverage: complete" if report["complete"] else "Coverage: incomplete (see unknown/errors)"
    )


def _run(
    mode: str,
    semester: str | None,
    courses: tuple[str, ...],
    json_output: bool,
    due_from: str | None = None,
    due_until: str | None = None,
) -> None:
    lower, upper = _bound(due_from, "--due-from"), _bound(due_until, "--due-until")
    try:
        # Existing authentication diagnostics must not contaminate stdout JSON.
        with contextlib.redirect_stdout(sys.stderr):
            report = query(
                mode, semester=semester, course_ids=courses, due_from=lower, due_until=upper
            )
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    if json_output:
        click.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _render(report)
    if not report["complete"]:
        raise click.exceptions.Exit(1)


@click.command(name="pending")
@click.option(
    "-s",
    "--semester",
    "--term",
    default=None,
    help="BB term ID, name or academic code; defaults to current unless --course is given.",
)
@click.option(
    "-c",
    "--course",
    "courses",
    multiple=True,
    help="Enrolled numeric/BB course ID. Repeat for an explicit cross-course scope.",
)
@click.option(
    "--due-from",
    help="Inclusive ISO due-time lower bound with a time zone; excludes undated tasks.",
)
@click.option(
    "--due-until",
    help="Inclusive ISO due-time upper bound with a time zone; excludes undated tasks.",
)
@click.option(
    "--json", "json_output", is_flag=True, help="Versioned JSON; exit 1 on incomplete coverage."
)
def pending_cmd(semester, courses, due_from, due_until, json_output):
    """Unsubmitted assessments and drafts, including overdue and undated work."""
    _run("pending", semester, courses, json_output, due_from, due_until)


@click.command(name="grades")
@click.option(
    "-s",
    "--semester",
    "--term",
    default=None,
    help="BB term ID, name or academic code; defaults to current unless --course is given.",
)
@click.option(
    "-c",
    "--course",
    "courses",
    multiple=True,
    help="Enrolled numeric/BB course ID. Repeat for an explicit cross-course scope.",
)
@click.option(
    "--json", "json_output", is_flag=True, help="Versioned JSON; exit 1 on incomplete coverage."
)
def grades_cmd(semester, courses, json_output):
    """Own submitted assessment attempts, actual possible points and pending grades."""
    _run("grades", semester, courses, json_output)


def add_commands(group: click.Group) -> None:
    group.add_command(pending_cmd)
    group.add_command(grades_cmd)
