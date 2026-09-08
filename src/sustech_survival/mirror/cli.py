"""
sustech_survival.mirror.cli — Click group for `sustech mirror ...`.

Subcommand layout (deliberately namespaced so the umbrella doesn't
get cluttered as we add content categories):

  mirror syllabus <sub>...    — course syllabi (教学大纲)
      get <CODE>...           download PDF(s)
      extract <CODE>...       download + extract plain text
      text <CODE>...          alias for extract
      exists <CODE>...        HEAD probe
      url <CODE>...           print URL(s)
      open <CODE>...          open in browser
      list-departments        parse 教学大纲汇总/<dept> subdirs
      batch                   bulk-fetch from your TIS history

  mirror program <sub>...     — 本科人才培养方案 (per-major plans)
      get <major> [--year Y]  download a training-program PDF
      years                   list years available

  mirror handbook <sub>...    — 书院/迎新 handbooks
      get <kind>              download a handbook PDF

  mirror map <sub>...         — campus map
      get                     download current map PDF

  mirror list <subpath>       — directory listing (best-effort HTML parse)

All mirror endpoints are unauthenticated; no TIS/CAS session is needed
for any of these. Use TIS as a fallback when mirror data is missing.
"""
from __future__ import annotations

import sys
from typing import Optional

import click

from sustech_survival import _cache
from sustech_survival.mirror import syllabus as _syllabus
from sustech_survival.mirror import tis_fallback as _tis_fallback
from sustech_survival.mirror.syllabus import (
    SyllabusError,
    SyllabusFetchError,
    SyllabusNotFound,
)


# -- umbrella group ----------------------------------------------------------


@click.group(name="mirror",
             help="SUSTech CRA open-source mirror (mirrors.sustech.edu.cn) — no login required.")
def mirror_cmd() -> None:
    """SUSTech CRA open-source mirror. All subcommands are unauthenticated.

    The mirror is the canonical home for course syllabi, training
    programs, freshman guides, and the campus map. Use TIS
    (sustech tis ...) for live data; use the mirror for archived PDFs.
    """
    pass


# -- syllabus subcommand -----------------------------------------------------


@mirror_cmd.group(name="syllabus",
                  help="Course syllabi (教学大纲) — download, extract, browse.")
def syllabus_cmd() -> None:
    pass


@syllabus_cmd.command(name="get", help="Download syllabus PDF(s) to disk.")
@click.argument("codes", nargs=-1, required=True)
@click.option("-o", "--output", "out_dir", default=None,
              help="Destination directory. Default: ~/.sustech_survival/downloads/syllabus")
@click.option("--overwrite", is_flag=True,
              help="Replace an existing file at the destination.")
@click.option("-q", "--quiet", is_flag=True, help="Suppress per-file progress output.")
def syllabus_get(codes, out_dir, overwrite, quiet):
    """Download one or more syllabi."""
    _bulk_download(codes, out_dir=out_dir, overwrite=overwrite, quiet=quiet,
                   fetch_fn=_syllabus.fetch,
                   write_fn=lambda c, d: _syllabus.download(c, out_dir=d, overwrite=overwrite))


@syllabus_cmd.command(name="extract",
                      help="Download + extract syllabus text. Writes <CODE>.txt next to the PDF.")
@click.argument("codes", nargs=-1, required=True)
@click.option("-o", "--output", "out_dir", default=None,
              help="Destination directory. Default: ~/.sustech_survival/downloads/syllabus")
@click.option("--overwrite", is_flag=True,
              help="Replace existing .pdf and .txt files.")
@click.option("-q", "--quiet", is_flag=True, help="Suppress per-file progress output.")
def syllabus_extract(codes, out_dir, overwrite, quiet):
    """Download + extract plain text from each PDF.

    Writes both <CODE>.pdf (raw) and <CODE>.txt (extracted) so the
    extraction is cacheable — re-running the command without --overwrite
    skips already-extracted syllabi.
    """
    target_dir = out_dir or str(_syllabus._default_out_dir())
    Path_target = _syllabus._default_out_dir  # local import alias
    from pathlib import Path
    pdir = Path(out_dir).expanduser() if out_dir else _syllabus._default_out_dir()
    pdir.mkdir(parents=True, exist_ok=True)

    failures = []
    successes = []
    for code in codes:
        code = _syllabus._normalize_code(code)
        txt = pdir / f"{code}.txt"
        pdf = pdir / f"{code}.pdf"
        if txt.exists() and not overwrite:
            if not quiet:
                click.secho(f"  ⏭  {code}: .txt already exists", fg="yellow")
            successes.append(code)
            continue
        try:
            body = _syllabus.fetch(code)
        except SyllabusNotFound as e:
            failures.append((code, f"not found: {e}"))
            if not quiet:
                click.secho(f"  ❌ {code}: {e}", fg="red")
            continue
        except SyllabusFetchError as e:
            failures.append((code, f"fetch error: {e}"))
            if not quiet:
                click.secho(f"  ❌ {code}: {e}", fg="red")
            continue
        if not pdf.exists() or overwrite:
            pdf.write_bytes(body)
        text = _syllabus.extract_text(body)
        txt.write_text(text, encoding="utf-8")
        successes.append(code)
        if not quiet:
            click.secho(f"  ✅ {code} → {txt} ({len(text):,} chars)", fg="green")
    if not quiet:
        click.echo(f"\n{len(successes)} extracted, {len(failures)} failed.")
    if failures:
        sys.exit(1)


@syllabus_cmd.command(name="text", help="Alias for `extract` (matches `sustech tis grades --json` style).")
@click.argument("codes", nargs=-1, required=True)
@click.option("-o", "--output", "out_dir", default=None)
@click.option("--overwrite", is_flag=True)
@click.option("-q", "--quiet", is_flag=True)
def syllabus_text(codes, out_dir, overwrite, quiet):
    """Same as `mirror syllabus extract`."""
    ctx = click.get_current_context()
    ctx.forward(syllabus_extract)


@syllabus_cmd.command(name="exists",
                      help="Check if a syllabus exists on the mirror (HEAD probe).")
@click.argument("codes", nargs=-1, required=True)
@click.option("-q", "--quiet", is_flag=True,
              help="Don't print; just set exit code (0 = all exist, 1 = any missing).")
def syllabus_exists_cmd(codes, quiet):
    """Exit 0 iff every CODE has a syllabus on the mirror."""
    missing = []
    for code in codes:
        try:
            ok = _syllabus.exists(code)
        except SyllabusFetchError as e:
            click.secho(f"  ❌ {code}: probe failed: {e}", fg="red")
            missing.append(code)
            continue
        if ok:
            if not quiet:
                click.secho(f"  ✅ {code}", fg="green")
        else:
            if not quiet:
                click.secho(f"  ❌ {code}: 404 on mirror", fg="red")
            missing.append(code)
    if missing:
        sys.exit(1)


@syllabus_cmd.command(name="url", help="Print the mirror URL(s); no network call.")
@click.argument("codes", nargs=-1, required=True)
def syllabus_url_cmd(codes):
    """Pipe-friendly URL printer."""
    for code in codes:
        click.echo(_syllabus.syllabus_url(code))


@syllabus_cmd.command(name="open",
                      help="Open the syllabus URL in your default browser.")
@click.argument("codes", nargs=-1, required=True)
def syllabus_open_cmd(codes):
    """Open each CODE's syllabus URL via webbrowser.open(). No 404 check."""
    for code in codes:
        url = _syllabus.syllabus_url(code)
        if _syllabus.open_in_browser(code):
            click.secho(f"  🌐 {code} → {url}", fg="cyan")
        else:
            click.secho(f"  ❌ {code}: failed to launch browser", fg="red")
            sys.exit(1)


@syllabus_cmd.command(name="list-departments",
                      help="List departments under 教学大纲汇总/ (aggregate syllabus view).")
def syllabus_list_departments():
    """Parse the aggregate directory to discover per-department subfolders.

    The aggregate view (教学大纲汇总/<dept>/<code>_<name>.pdf) is the
    same data as the flat /syllabus/ directory, but with descriptive
    filenames. Useful when you want a 'browse by department' workflow.
    """
    depts = _syllabus.list_departments()
    if not depts:
        click.secho(
            "Could not list 教学大纲汇总/ (403 or parse failure). "
            "The directory-listing endpoint is more aggressively rate-"
            "limited than file fetches; try again in a minute or browse "
            "directly: https://mirrors.sustech.edu.cn/courses/教学大纲汇总/",
            fg="yellow",
        )
        sys.exit(1)
    click.echo(f"Found {len(depts)} department(s):")
    for d in depts:
        click.echo(f"  {d}")


@syllabus_cmd.command(name="batch",
                      help="Bulk-download every syllabus for a TIS grade history.")
@click.option("--semester", default=None,
              help="Restrict to one semester (e.g. '2025-2026-1' or '2025秋季'). "
                   "Default: every semester you've taken.")
@click.option("-o", "--output", "out_dir", default=None)
@click.option("--overwrite", is_flag=True)
@click.option("--limit", type=int, default=None)
@click.option("--dry-run", is_flag=True)
@click.option("--extract", is_flag=True,
              help="Also write .txt sidecars (like `mirror syllabus extract`).")
def syllabus_batch(semester, out_dir, overwrite, limit, dry_run, extract):
    """Bulk-fetch every unique course from your TIS history.

    Requires valid TIS credentials (auth runs automatically via TISAuth.ensure()).
    Without auth, run with --dry-run against a TIS-free environment to
    preview the course code list (currently unsupported; --dry-run still
    requires auth because it reads your grade history).
    """
    from sustech_survival.tis.courses import get_courses
    from sustech_survival.sso import TISAuth

    try:
        auth = TISAuth()
        ok, reason = auth.ensure()
    except Exception as e:  # noqa: BLE001
        click.secho(f"❌ TISAuth setup failed: {e}", fg="red")
        sys.exit(1)
    if not ok:
        click.secho(
            f"❌ TIS auth failed for --batch (mirror has no per-student "
            f"history; re-login was automatic).\n   reason: {reason}\n"
            f"   diagnose: `sustech sso check`",
            fg="red",
        )
        sys.exit(1)

    try:
        rows = get_courses(auth.session, semester=semester)
    except Exception as e:  # noqa: BLE001
        click.secho(f"❌ Failed to fetch your course history: {e}", fg="red")
        sys.exit(1)

    codes = sorted({(r.get("kcdm") or "").strip().upper() for r in rows if r.get("kcdm")})
    if not codes:
        click.secho("No courses found in your TIS history.", fg="yellow")
        return

    click.echo(f"Found {len(codes)} unique course(s)" +
               (f" in {semester!r}" if semester else "") + ".")
    if dry_run:
        for c in codes:
            click.echo(f"  would fetch  {c}  →  {_syllabus.syllabus_url(c)}")
        return
    if limit:
        codes = codes[:limit]
        click.echo(f"Limiting to first {limit}.")

    successes, failures = [], []
    for code in codes:
        try:
            body = _syllabus.fetch(code)
        except SyllabusNotFound:
            failures.append((code, "404"))
            click.secho(f"  ❌ {code}: no syllabus on mirror", fg="red")
            continue
        except SyllabusFetchError as e:
            failures.append((code, str(e)))
            click.secho(f"  ❌ {code}: {e}", fg="red")
            continue
        from pathlib import Path
        target_dir = Path(out_dir).expanduser() if out_dir else _syllabus._default_out_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        pdf = target_dir / f"{code}.pdf"
        if not pdf.exists() or overwrite:
            pdf.write_bytes(body)
        if extract:
            txt = target_dir / f"{code}.txt"
            if not txt.exists() or overwrite:
                txt.write_text(_syllabus.extract_text(body), encoding="utf-8")
        successes.append(code)
        click.secho(f"  ✅ {code} → {pdf}", fg="green")

    click.echo(f"\n{len(successes)} downloaded, {len(failures)} failed.")
    if failures:
        sys.exit(1)


# -- program subcommand (本科人才培养方案) ------------------------------------


PROGRAM_PREFIX = "/courses/本科人才培养方案"


@mirror_cmd.group(name="program",
                  help="Undergrad training programs (本科人才培养方案).")
def program_cmd() -> None:
    pass


@program_cmd.command(name="years", help="List years for which a training plan exists.")
def program_years():
    """Probe well-known year paths and report which exist."""
    # Most years are subdirs; 2025 is a single PDF. 2018 has two subdirs
    # for 'used at year-1-end' vs 'year-2-end'.
    candidates = [
        "2018级本科人才培养方案（适用于第一学年结束时，申请进入专业）/",
        "2018级本科人才培养方案（适用于第二学年结束时，申请进入专业）/",
        "2019级本科人才培养方案/",
        "2020级本科人才培养方案/",
        "2021级本科人才培养方案/",
        "2022级本科人才培养方案/",
        "2023级本科人才培养方案/",
        "2024级本科人才培养方案/",
        "2025级本科人才培养方案.pdf",
        "2026级本科人才培养方案.pdf",
    ]
    found = []
    for c in candidates:
        url = f"{_syllabus.MIRROR_BASE}{PROGRAM_PREFIX}/{c}"
        try:
            r = _syllabus._session().head(url, allow_redirects=True, timeout=10)
            if r.status_code == 200:
                found.append(c.rstrip("/"))
        except Exception:  # noqa: BLE001
            continue
    if not found:
        click.secho("No training-plan years found (mirror may be down).", fg="yellow")
        sys.exit(1)
    click.echo("Available training-plan years:")
    for f in found:
        click.echo(f"  {f}")


@program_cmd.command(name="get",
                     help="Download a training-plan PDF. <year> like '2024级'.")
@click.argument("year")
@click.option("-o", "--output", "out_dir", default=None,
              help="Destination directory. Default: ~/.sustech_survival/downloads/program")
@click.option("--overwrite", is_flag=True)
def program_get(year, out_dir, overwrite):
    """Download a single per-year training-program compendium PDF."""
    from pathlib import Path
    target_dir = Path(out_dir).expanduser() if out_dir else (
        _cache.config_root() / "downloads" / "program"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{year}本科人才培养方案.pdf"
    if target.exists() and not overwrite:
        click.secho(f"  ⏭  {target} already exists (pass --overwrite)", fg="yellow")
        return
    url = f"{_syllabus.MIRROR_BASE}{PROGRAM_PREFIX}/{year}本科人才培养方案.pdf"
    try:
        r = _syllabus._session().get(url, allow_redirects=True, timeout=20)
    except Exception as e:  # noqa: BLE001
        click.secho(f"  ❌ fetch error: {e}", fg="red")
        sys.exit(1)
    if r.status_code == 404:
        click.secho(f"  ❌ 404 at {url}", fg="red")
        click.secho("  Try `sustech mirror program years` to see available years.", fg="yellow")
        sys.exit(1)
    if r.status_code != 200:
        click.secho(f"  ❌ mirror returned {r.status_code}", fg="red")
        sys.exit(1)
    target.write_bytes(r.content)
    click.secho(f"  ✅ {target} ({len(r.content):,} bytes)", fg="green")


# -- handbook + map placeholders --------------------------------------------


@mirror_cmd.group(name="handbook", help="Freshman / college handbooks (best-effort).")
def handbook_cmd() -> None:
    pass


@handbook_cmd.command(name="get", help="Download a handbook PDF by name (e.g. 'freshman-2022').")
@click.argument("kind")
@click.option("-o", "--output", "out_dir", default=None)
@click.option("--overwrite", is_flag=True)
def handbook_get(kind, out_dir, overwrite):
    """Best-effort downloader for known handbook URLs.

    Supported kinds (the canonical paths on the mirror; add more here
    as we discover them):
      - freshman-2022  → /site/sustech-online/documents/freshman-handbook/2022.pdf
    """
    from pathlib import Path
    known = {
        "freshman-2022": "/site/sustech-online/documents/freshman-handbook/2022.pdf",
    }
    if kind not in known:
        click.secho(f"  ❌ unknown handbook kind: {kind!r}. Known: {list(known)}", fg="red")
        sys.exit(1)
    target_dir = Path(out_dir).expanduser() if out_dir else (
        _cache.config_root() / "downloads" / "handbook"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{kind}.pdf"
    if target.exists() and not overwrite:
        click.secho(f"  ⏭  {target} already exists (pass --overwrite)", fg="yellow")
        return
    url = f"{_syllabus.MIRROR_BASE}{known[kind]}"
    try:
        r = _syllabus._session().get(url, allow_redirects=True, timeout=20)
    except Exception as e:  # noqa: BLE001
        click.secho(f"  ❌ fetch error: {e}", fg="red")
        sys.exit(1)
    if r.status_code != 200:
        click.secho(f"  ❌ mirror returned {r.status_code}", fg="red")
        sys.exit(1)
    target.write_bytes(r.content)
    click.secho(f"  ✅ {target} ({len(r.content):,} bytes)", fg="green")


@mirror_cmd.group(name="map", help="Campus map PDF.")
def map_cmd() -> None:
    pass


@map_cmd.command(name="get", help="Download the current campus map PDF.")
@click.option("-o", "--output", "out_dir", default=None)
@click.option("--overwrite", is_flag=True)
def map_get(out_dir, overwrite):
    """Fetch the latest campus map from the sustech.online team."""
    from pathlib import Path
    target_dir = Path(out_dir).expanduser() if out_dir else (
        _cache.config_root() / "downloads" / "map"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "sustech-campus-map.pdf"
    if target.exists() and not overwrite:
        click.secho(f"  ⏭  {target} already exists (pass --overwrite)", fg="yellow")
        return
    # The exact filename changes with each map release (v4-1, v4-2, ...).
    # Probe the directory index to find the latest; fall back to v4-1.
    candidates = []
    index_url = f"{_syllabus.MIRROR_BASE}/site/sustech-online/documents/campus-map/"
    try:
        r = _syllabus._session().get(index_url, timeout=10)
        if r.status_code == 200:
            import re
            for m in re.finditer(r'href="([^"]+\.pdf)"', r.text):
                candidates.append(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    if not candidates:
        candidates = ["site/sustech-online/documents/campus-map/南方科技大学校园地图-v4-1.pdf"]

    last_err = None
    for path in candidates:
        url = f"{_syllabus.MIRROR_BASE}/{path}"
        try:
            r = _syllabus._session().get(url, allow_redirects=True, timeout=20)
            if r.status_code == 200:
                target.write_bytes(r.content)
                click.secho(f"  ✅ {target} ({len(r.content):,} bytes from {path})", fg="green")
                return
            last_err = f"{r.status_code}"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
    click.secho(f"  ❌ could not fetch any campus map (last error: {last_err})", fg="red")
    sys.exit(1)


# -- TIS-backed machine-readable course info (no PDF parsing) ----------------


@mirror_cmd.command(name="course",
                    help="TIS-backed machine-readable course info — no PDF parsing.")
@click.argument("code")
@click.option("--text", "as_text", is_flag=True,
              help="Human-readable text instead of JSON.")
@click.option("--include-raw", is_flag=True,
              help="Include the raw TIS row in the JSON output (large).")
def course_cmd(code, as_text, include_raw):
    """Look up a course by code from TIS (your grades or the public catalog).

    When the mirror can't help (no PDF, scanned image, parsing failure),
    TIS returns clean structured data: name, department, credits, type,
    score, rank, class size. TIS is more reliable for these fields;
    use ``sustech mirror syllabus text`` when you need the prose syllabus
    (description, weekly outline, assessment).

    Requires valid TIS credentials (auth runs automatically).
    Returns the data as JSON (or --text for a one-line-per-field view).
    """
    import json as _json
    data = _tis_fallback.fetch_course_json(code)
    if data is None:
        click.secho(
            f"❌ {code!r} not found in your TIS history and not in the "
            f"current catalog. If it's a course you've taken, the catalog "
            f"endpoint may not have it (selection round closed). Try "
            f"`sustech mirror syllabus exists {code}` to check the mirror.",
            fg="red",
        )
        sys.exit(1)
    if not include_raw:
        data = {k: v for k, v in data.items() if k != "raw"}
    if as_text:
        click.echo(_tis_fallback.fetch_course_text(code))
    else:
        click.echo(_json.dumps(data, ensure_ascii=False, indent=2))


# -- directory listing -------------------------------------------------------


@mirror_cmd.command(name="list", help="Best-effort directory listing for a mirror subpath.")
@click.argument("subpath")
def mirror_list(subpath):
    """Parse the Nginx autoindex page for ``subpath`` (under the mirror root).

    Example:
      sustech mirror list /courses
      sustech mirror list /courses/syllabus
    """
    url = f"{_syllabus.MIRROR_BASE}/{subpath.strip('/')}/"
    try:
        r = _syllabus._session().get(url, timeout=10)
    except Exception as e:  # noqa: BLE001
        click.secho(f"  ❌ fetch error: {e}", fg="red")
        sys.exit(1)
    if r.status_code == 403:
        click.secho("  ❌ 403 Forbidden — directory listing disabled for this path.", fg="red")
        sys.exit(1)
    if r.status_code != 200:
        click.secho(f"  ❌ mirror returned {r.status_code}", fg="red")
        sys.exit(1)
    import re
    rows = re.findall(r'<a\s+href="([^"]+)"[^>]*>([^<]+)</a>', r.text)
    for href, title in rows:
        if href in ("../", "/", "") or href.startswith("?"):
            continue
        marker = "/" if href.endswith("/") else " "
        click.echo(f"  {marker} {href}")


# -- shared helper -----------------------------------------------------------


def _bulk_download(codes, *, out_dir, overwrite, quiet, fetch_fn, write_fn):
    successes, failures = [], []
    for code in codes:
        try:
            path = write_fn(code, out_dir)
        except FileExistsError as e:
            failures.append((code, f"exists: {e}"))
            if not quiet:
                click.secho(f"  ⏭  {code}: {e}", fg="yellow")
            continue
        except SyllabusNotFound as e:
            failures.append((code, f"not found: {e}"))
            if not quiet:
                click.secho(f"  ❌ {code}: {e}", fg="red")
            continue
        except SyllabusFetchError as e:
            failures.append((code, f"fetch error: {e}"))
            if not quiet:
                click.secho(f"  ❌ {code}: {e}", fg="red")
            continue
        successes.append(code)
        if not quiet:
            click.secho(f"  ✅ {code} → {path}", fg="green")
    if not quiet:
        click.echo(f"\n{len(successes)} downloaded, {len(failures)} failed.")
    if failures:
        sys.exit(1)


__all__ = ["mirror_cmd"]
