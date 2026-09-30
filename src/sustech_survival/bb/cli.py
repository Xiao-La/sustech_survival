#!/usr/bin/env python3
"""
BB CLI — SUSTech Blackboard (courses, materials, assignments, submissions).

Mounted under the unified CLI as `sustech bb`. Canonical surface:

  sustech bb courses [QUERY] [--refresh]   enrolled courses (complete list) /
                                           search by ID or name
  sustech bb page <course_id>              the course's whole item tree
  sustech bb page <content_id>             one item + its 📎 attachments
  sustech bb download <content_id>         fetch a material (PDF/docx) to disk
  sustech bb search -c <course> -s <text>  search items (title/type/attachment)
  sustech bb types -c <course>             item-type statistics
  sustech bb course <course> assignments   assignment list + attempt status
  sustech bb submit <content_id> <file>    submit to an assignment (REST)

IDs: `bb courses` prints the numeric course id (8613); content ids come from
`bb page` rows. Both forms are accepted everywhere (`8613` or `_8613_1`).
"""
import sys, os, json
from pathlib import Path

import click
from sustech_survival.sso import BBAuth
from sustech_survival.sso.authorizer import AuthorizerError
from .courses import (list_courses, find_course, get_course_numeric_id,
                      discover_assignments_for_course, load_courses,
                      course_list_meta, semester_labels, courses_in_semester,
                      site_terms, resolve_semester, term_code)
from .download import discover_attempt_ids, scrape_attempt_details, download_file, slugify
from .pages import preview_page
from .query import (
    discover_all_items, type_stats_items, print_stats,
    format_item, ContentNotAccessible,
)

# -- Helpers ------------------------------------------------------------

def em(s):
    return click.style(s, bold=True)

def ok_s(s):
    return click.style(s, fg="green")
def err_s(s):
    return click.style(s, fg="red")


bb_auth = BBAuth()


def load_session_or_exit():
    """Ensure session is valid and return session cookies list."""
    try:
        ok, reason = bb_auth.ensure()
        if not ok:
            click.secho(f"\n❌  Session invalid: {reason}", fg="red")
            sys.exit(1)
        # Get cookies from the Authorizer's session
        session = bb_auth.session
        return [{"name": c.name, "value": c.value, "domain": ".bb.sustech.edu.cn", "path": "/"}
                for c in session.cookies if c.value]
    except AuthorizerError as e:
        click.secho(f"\n❌  Session error: {e}", fg="red")
        sys.exit(1)
    except FileNotFoundError:
        click.secho("\n❌  No credentials found - configure them with `sustech sso creds set`.", fg="red")
        sys.exit(1)


# -- Safe wrappers ------------------------------------------------------

def safe_attempts(ctx, numeric_cid, content_id):
    try:
        return discover_attempt_ids(ctx, numeric_cid, content_id)
    except Exception:
        return []


# -- Assignment list command --------------------------------------------

def list_assignments(ctx, numeric_cid, course_name, assignments):
    """List all assignments with attempt counts."""
    click.secho(f"\n📋  Assignments - {course_name}\n", fg="cyan", bold=True)
    for cid, title in assignments:
        atts = safe_attempts(ctx, numeric_cid, cid)
        status = f"{len(atts)} attempt(s)" if atts else err_s("not submitted")
        click.secho(f"  [{cid}] {title[:44]}", fg="white")
        click.echo(f"       {status}")
        for aid, (anum, ts) in atts:
            click.echo(f"         {em(f'Attempt {anum}')}  {ts[:25]}")
        print()


# -- All-status command -------------------------------------------------

def all_status(ctx, numeric_cid, course_name, assignments):
    """Show submitted/not submitted for all assignments."""
    click.secho(f"\n📋  Submission Status - {course_name}\n", fg="cyan", bold=True)
    for cid, title in assignments:
        atts = safe_attempts(ctx, numeric_cid, cid)
        status = ok_s(f"submitted ({len(atts)} attempt(s))") if atts else err_s("not submitted")
        click.secho(f"  [{cid}] {title[:44]}", fg="white")
        click.echo(f"       {status}")
    print()


# -- Single assignment command ------------------------------------------

def single_assignment(ctx, session_cookies, numeric_cid,
                       content_id, attempt_arg, download_flag, output_dir):
    """Handle: assignment <id> [status|attempt|N]"""
    att_keyword = str(attempt_arg).lower() if attempt_arg else None

    if att_keyword == "status":
        # Status of one specific assignment
        atts = safe_attempts(ctx, numeric_cid, content_id)
        status = ok_s(f"submitted ({len(atts)} attempt(s))") if atts else err_s("not submitted")
        click.secho(f"\n📋  Assignment {content_id}\n", fg="cyan", bold=True)
        click.echo(f"  Status: {status}")
        if atts:
            for aid, (anum, ts) in atts:
                click.echo(f"  {em(f'Attempt {anum}')}  {ts[:25]}")
        print()
        return

    if att_keyword == "attempts" or attempt_arg is None:
        # All attempts for this assignment
        atts = safe_attempts(ctx, numeric_cid, content_id)
        click.secho(f"\n🔍  Attempts - {content_id}\n", fg="cyan", bold=True)
        if not atts:
            click.secho("  No attempts found.", fg="yellow")
        else:
            for aid, (anum, ts) in atts:
                try:
                    det = scrape_attempt_details(ctx, numeric_cid, content_id, aid)
                except Exception:
                    det = {"files": [], "graded": False, "score": ""}
                score = det.get("score", "")
                grade_str = f" {score}/100" if score else (" (ungraded)" if not det.get("graded") else "")
                click.echo(f"  {em(f'Attempt {anum}')}  {ts[:25]}{grade_str}")
                click.echo(f"           {len(det.get('files', []))} file(s)")
                print()
        return

    # Must be an attempt number
    try:
        anum = int(attempt_arg)
    except ValueError:
        click.secho(f"❌  Invalid: {attempt_arg}", fg="red")
        sys.exit(1)

    # Details of specific attempt
    atts = safe_attempts(ctx, numeric_cid, content_id)
    att_map = {a: aid for aid, (a, _) in atts}
    if anum not in att_map:
        click.secho(f"❌  Attempt {anum} not found. Available: {list(att_map.keys())}", fg="red")
        sys.exit(1)

    aid = att_map[anum]
    try:
        details = scrape_attempt_details(ctx, numeric_cid, content_id, aid)
    except Exception as e:
        click.secho(f"  ⚠ Error loading details: {e}", fg="yellow")
        details = {"created": "", "files": [], "graded": False,
                   "score": "", "feedback": ""}

    ts = details.get("created", "")
    files = details.get("files", [])
    graded = details.get("graded", False)
    score = details.get("score", "")
    feedback = details.get("feedback", "")

    click.secho(f"\n  Assignment {content_id} - Attempt {anum}\n", fg="cyan", bold=True)
    click.echo(f"  Timestamp:  {ts or 'unknown'}")
    click.echo(f"  Status:     {'graded' if graded else 'ungraded'}")
    if score:
        click.echo(f"  Grade:      {score}/100")
    if feedback:
        click.echo(f"  Feedback:   {feedback[:100]}")
    click.echo(f"  Files:      {len(files)}")
    for fname, href in files:
        click.echo(f"    • {fname[:60]}")

    if download_flag:
        out_dir = Path(output_dir) / slugify(f"assignment_{content_id}")
        out_dir.mkdir(parents=True, exist_ok=True)
        click.echo(f"\n  Downloading to {out_dir}...")
        cookie_dict = {c["name"]: c["value"] for c in session_cookies if c.get("value")}
        for fname, href in files:
            stem, ext = os.path.splitext(fname)
            out_path = out_dir / f"attempt{anum}_{stem}{ext}"
            if out_path.exists():
                click.echo(f"    (exists: {out_path.name})")
                continue
            try:
                download_file(out_path, href, cookie_dict)
                size = out_path.stat().st_size
                click.secho(f"    ✓ {out_path.name} ({size:,})", fg="green")
            except Exception as e:
                click.secho(f"    ✗ {fname}: {e}", fg="red")


# -- CLI group + commands -----------------------------------------------

@click.group()
@click.pass_context
def cli(ctx):
    """BB CLI - SUSTech Blackboard Assignment Automation"""
    ctx.ensure_object(dict)


@cli.command(name="courses")
@click.argument("query", default="", required=False)
@click.option("--refresh", is_flag=True, default=False,
              help="Re-fetch the course list from BB (bypasses the 24h cache).")
@click.option("-s", "--semester", "semester", default=None,
              help="Only courses in this term, e.g. 'fall 2026', '2026秋', 'spring'.")
def courses_cmd(query, refresh, semester):
    """List all enrolled courses (or search by ID / name / semester)."""
    if refresh:
        from .courses import refresh_courses_json
        refresh_courses_json(quiet=True)
    all_courses = list_courses()
    if not all_courses:
        click.secho("❌  No courses found. Try `sustech bb courses --refresh`.", fg="red")
        return

    meta = course_list_meta()
    # A cache written before the site term table existed cannot be filtered by
    # semester — refetch once instead of reporting "no semester matches".
    if semester and not site_terms():
        from .courses import refresh_courses_json
        refresh_courses_json(quiet=True)
        all_courses = list_courses()

    resolved = resolve_semester(semester) if semester else []
    if semester and not resolved:
        click.secho(f"\n❌  No semester on the site matches '{semester}'.", fg="red")
        code_by_id = {t["id"]: term_code(t) for t in site_terms()}
        click.echo("   Semesters you have courses in:")
        seen = set()
        for c in load_courses():
            if c.get("term") and c["term_id"] not in seen:
                seen.add(c["term_id"])
                code = code_by_id.get(c["term_id"], "")
                click.echo(f"     {c['term']}   [{code or c['term_id']}]")
        return

    results = find_course(query) if query else sorted(all_courses, key=lambda x: x[1])
    if resolved:
        term_ids = {t["id"] for t in resolved}
        allowed = {c["id"] for c in load_courses() if c.get("term_id") in term_ids}
        results = [(c, n) for c, n in results if c in allowed]
        if not results:
            click.secho(f"\n❌  No enrolled course in "
                        f"{', '.join(t['name'] for t in resolved)}.", fg="red")
            return
    if query and not results:
        click.secho(f"\n❌  No course matches '{query}'.", fg="red")
        click.echo("   `sustech bb courses` lists every enrolled course + its ID.")
        return

    click.secho("\n📚  Courses", fg="cyan", bold=True)
    label = ""
    if resolved:
        label = "  ·  " + ", ".join(t["name"] for t in resolved)
    if query:
        click.secho(f"   search '{query}'{label} → {len(results)} match(es)", fg="white")
    else:
        click.secho(f"   {len(results)} enrolled course(s){label}", fg="white")

    # Freshness: the list refreshes itself once it is over a day old, so this
    # is information, not a nudge to re-fetch.
    from datetime import datetime
    if meta["ts"]:
        refreshed = datetime.fromtimestamp(meta["ts"])
        click.secho(f"   list refreshed {refreshed:%Y-%m-%d %H:%M} "
                    f"(auto-refreshes after 24 h)", fg="white")
    else:
        click.secho("   list not fetched yet (fetches on first use)", fg="white")
    click.echo()

    term_by_id = {c["id"]: c.get("term", "") for c in load_courses()}
    if resolved:
        for cid, name in results:
            click.echo(f"  {get_course_numeric_id(cid):<6}  {name}")
    else:
        # Default view: grouped by semester, newest first (the site's own term
        # dates decide the order), courses alphabetical inside a group.
        order = {}
        for i, t in enumerate(sorted(site_terms(),
                                     key=lambda t: (t.get("start") or ""),
                                     reverse=True)):
            order[t["id"]] = (i, t)
        groups = {}
        for cid, name in results:
            entry = next((c for c in load_courses() if c["id"] == cid), {})
            tid = entry.get("term_id", "")
            groups.setdefault(tid, []).append((cid, name))
        for tid in sorted(groups, key=lambda k: order.get(k, (999, {}))[0]):
            tname = term_by_id.get(next((c for c, _ in groups[tid]), ""), "")
            code = term_code(order[tid][1]) if tid in order else tid
            head = f"{tname}   [{code}]" if code else (tname or "(no term)")
            click.secho(f"  {head}", fg="cyan")
            for cid, name in sorted(groups[tid], key=lambda x: x[1]):
                click.echo(f"    {get_course_numeric_id(cid):<6}  {name}")
    click.echo()
    click.echo("  Filter by semester:     sustech bb courses --semester 'fall 2026'")
    click.echo("  Item tree of a course:  sustech bb page <course_id>")
    click.echo("")


@cli.command(name="search")
@click.option("-c", "--course", help="Course ID or name substring (e.g. 8613 or 'Thermochromic')")
@click.option("-t", "--type", "type_filter", multiple=True,
              help="Item type: file | folder | inline | homework | video | link | text")
@click.option("-s", "--text", help="Search in item titles (substring match)")
@click.option("-C", "--content", "content_text", help="Search in item content text")
@click.option("-a", "--has-attachments", "has_attachments", is_flag=True,
              help="Only show items with file attachments")
@click.option("--hide", "hide_types", multiple=True, help="Hide items of this type (same vocabulary as -t)")
@click.option("--show", "show_types", multiple=True, help="Show ONLY items of this type (same vocabulary as -t)")
@click.option("--sort", "sort_by", default="course", type=click.Choice(["course", "type", "title"]),
              help="Sort order (default: course)")
@click.option("-o", "--output", "output_fmt", default="text", type=click.Choice(["text", "json"]),
              help="Output format")
@click.option("-v", "--verbose", is_flag=True, help="Show more detail per item")
@click.option("-r", "--refresh", is_flag=True, help="Bust cache and rescrape BB")
@click.option("--semester", "semester", default=None,
              help="Restrict to one term, e.g. 'fall 2026', '2026秋', 'spring'.")
def search_cmd(course, type_filter, text, content_text, has_attachments,
               hide_types, show_types, sort_by, output_fmt, verbose, refresh,
               semester):
    """Search and filter BB items (fully dynamic - live scrape)."""
    load_session_or_exit()
    from .query import discover_all_items, format_item
    from . import _cache

    course_ids = None
    if semester:
        resolved = resolve_semester(semester)
        if not resolved:
            click.secho(f"❌  No semester matches '{semester}'.", fg="red", err=True)
            for c in load_courses():
                if c.get("term"):
                    click.echo(f"    {c['term']}  [{c['term_id']}]", err=True)
            return
        term_ids = {t["id"] for t in resolved}
        course_ids = [get_course_numeric_id(c["id"]) for c in load_courses()
                      if c.get("term_id") in term_ids]
        if not course_ids:
            click.secho(f"❌  No enrolled course in "
                        f"{', '.join(t['name'] for t in resolved)}.",
                        fg="red", err=True)
            return
        click.secho(f"  semester: {', '.join(t['name'] for t in resolved)} "
                    f"→ {len(course_ids)} course(s)", fg="cyan", err=True)

    if refresh:
        if course:
            # Invalidate only pages for the matching course(s)
            from .courses import list_courses, find_course
            courses = find_course(course) if course else list_courses()
            for cid, _ in courses:
                _cache.invalidate_all(f"discover_pages_{cid}")
                _cache.invalidate_all(f"page_items_")
        else:
            _cache.invalidate_all()
        click.secho("  Cache cleared.", fg="yellow", err=True)

    def progress(done, total):
        if total > 3:
            click.secho(f"  Scanning pages: {done}/{total}", fg="cyan", err=True)

    results_list = discover_all_items(
        course_filter=course,
        text_filter=text,
        content_text=content_text,
        type_filter=list(type_filter) if type_filter else None,
        has_attachments=has_attachments,
        hide_types=list(hide_types) if hide_types else None,
        show_types=list(show_types) if show_types else None,
        progress=progress,
        refresh=refresh,
        course_ids=course_ids,
    )

    # Sort
    if sort_by == "type":
        results_list.sort(key=lambda u: (u.get("type", ""), u["course"], u["title"]))
    elif sort_by == "title":
        results_list.sort(key=lambda u: u["title"].lower())
    else:  # course (default)
        results_list.sort(key=lambda u: (u["course"], u.get("type", ""), u["title"]))

    if output_fmt == "json":
        print(json.dumps(results_list, ensure_ascii=False, indent=2))
    else:
        if not results_list:
            click.secho("No items match the search criteria.", fg="yellow")
            _suggest_near_misses(course, text, content_text,
                                 list(type_filter), course_ids)
            return
        click.secho(f"\n🔍 Search Results ({len(results_list)} item(s))\n", fg="cyan", bold=True)
        click.echo(f"  {'course':<7} {'id':<7}   item")
        for u in results_list:
            format_item(u, verbose=verbose)


def _list_course_files(course_id):
    """Every item in a course, WITH its 📎 files — the fallback when a
    keyword search fails (typo, unknown wording, or the teacher just wrote
    'week 1 report'). One pass over the course; the scan is cached."""
    from .query import discover_all_items
    numeric = get_course_numeric_id(str(course_id))
    click.secho(f"\n📚  {course_id} — every item, with files\n", fg="cyan", bold=True)
    items = discover_all_items(course_ids=[numeric])
    if not items:
        click.secho("  No items returned (course empty, or BB refuses "
                    "per-item detail — see the ⚠ line above).", fg="yellow")
        return
    last_section = None
    for u in sorted(items, key=lambda u: (u.get("title", "").lower())):
        section = u.get("course_name") or ""
        if section != last_section:
            last_section = section
        files = u.get("files") or []
        icon = {"file": "📄", "inline": "📄", "folder": "📁",
                "homework": "📝", "video": "🎬"}.get(u.get("type", ""), "•")
        click.echo(f"  {icon} [{u.get('id','')}] {u.get('title','')}")
        for name, _u in files:
            click.echo(f"        📎 {name[:78]}")
    click.echo()


def _suggest_near_misses(course, text, content_text, type_filter, course_ids):
    """A keyword is a guess: titles get misspelled, abbreviated, or replaced
    by 'week N'. When the search comes back empty, offer the closest titles
    from the same scope instead of a dead end."""
    import difflib
    from .query import discover_all_items
    needle = (text or content_text or "").strip().lower()
    if not needle:
        return
    try:
        everything = discover_all_items(course_filter=course, course_ids=course_ids)
    except Exception:
        return
    if not everything:
        return

    def norm(s):
        return "".join(ch for ch in s.lower() if ch.isalnum())

    target = norm(needle)
    scored = []
    for u in everything:
        title = u.get("title", "")
        score = difflib.SequenceMatcher(None, target, norm(title)).ratio()
        if target and (target in norm(title) or norm(title) and norm(title) in target):
            score = max(score, 0.9)
        scored.append((score, u))
    scored.sort(key=lambda x: -x[0])

    click.secho(f"\n  Nothing contains '{text or content_text}'.", fg="yellow")
    click.echo("  Closest titles in scope (titles may be misspelled, "
               "abbreviated, or just 'week N'):")
    for score, u in scored[:6]:
        if score < 0.3:
            continue
        files = u.get("files") or []
        att = f"  📎 {len(files)}" if files else ""
        click.echo(f"    {u.get('course',''):<7} {u.get('id',''):<7} "
                   f"{u.get('title','')[:48]}{att}")
    click.echo("\n  Scan everything instead:  sustech bb page <course_id> --files")


@cli.command(name="types")
@click.option("-c", "--course", "course_filter", help="Course ID or name substring (e.g. 8613)")
@click.option("-s", "--semester", "semester", default=None,
              help="Restrict to one term, e.g. 'fall 2026', '2026秋'.")
@click.option("-r", "--refresh", is_flag=True, help="Bust cache and rescrape BB")
def types_cmd(course_filter, semester, refresh):
    """Show BB item type statistics (fully dynamic - live scrape)."""
    load_session_or_exit()
    from .query import type_stats_items, print_stats
    from . import _cache

    course_ids = None
    if semester:
        resolved = resolve_semester(semester)
        if not resolved:
            click.secho(f"❌  No semester matches '{semester}'.", fg="red", err=True)
            for c in load_courses():
                if c.get("term"):
                    click.echo(f"    {c['term']}  [{c['term_id']}]", err=True)
            return
        term_ids = {t["id"] for t in resolved}
        course_ids = [get_course_numeric_id(c["id"]) for c in load_courses()
                      if c.get("term_id") in term_ids]
        if not course_ids:
            click.secho(f"❌  No enrolled course in "
                        f"{', '.join(t['name'] for t in resolved)}.",
                        fg="red", err=True)
            return
        click.secho(f"  semester: {', '.join(t['name'] for t in resolved)} "
                    f"→ {len(course_ids)} course(s)", fg="cyan", err=True)

    if refresh:
        if course_filter:
            from .courses import find_course, list_courses
            courses = find_course(course_filter) if course_filter else list_courses()
            for cid, _ in courses:
                _cache.invalidate_all(f"discover_pages_{cid}")
                _cache.invalidate_all(f"page_items_")
        else:
            _cache.invalidate_all()
        click.secho("  Cache cleared.", fg="yellow", err=True)

    def progress(done, total):
        if total > 3:
            click.secho(f"  Scanning pages: {done}/{total}", fg="cyan", err=True)

    stats = type_stats_items(course_filter=course_filter, progress=progress,
                             course_ids=course_ids)
    print_stats(stats)


@cli.command(name="page")
@click.argument("content_id")
@click.option("-c", "--course", "course_id", help="Course ID (numeric). Auto-resolved if omitted.")
@click.option("-v", "--verbose", is_flag=True, help="Show full description and video URL")
@click.option("-f", "--files", "with_files", is_flag=True, default=False,
              help="With a course id: every item WITH its 📎 files "
                   "(one extra pass — use when a keyword search came up empty)")
def page_cmd(content_id, course_id, verbose, with_files):
    """Show a content item (with its files), or a course's whole item tree.

    With a CONTENT id: that item, its 📎 attachments, and the download command.
    With a COURSE id: the entire content tree of that course.

    Example:
      bb.py page 8613              # every item in course 8613 (tree)
      bb.py page 645443            # one item + its attachments
      bb.py page 645443 -c 8613    # with explicit course ID
      bb.py page 645443 -v         # verbose (description, BB URL)
    """
    load_session_or_exit()
    click.secho(f"\n📄 Page {content_id}\n", fg="cyan", bold=True)

    # A COURSE id (8613) is not a content id — /contents/<course_id> always
    # 403s. Detect it up front and list the course tree instead.
    numeric = get_course_numeric_id(content_id)
    is_course = any(get_course_numeric_id(c) == numeric
                    for c, _ in find_course(content_id)) if numeric else False

    items = []
    if not is_course:
        try:
            items = preview_page(content_id, course_id)
        except ContentNotAccessible as e:
            click.secho(f"❌  {e}", fg="red")
            click.secho("   The course tree still lists (bb page <course_id>), "
                        "but BB refuses per-item detail here.", fg="yellow")
            return
        except Exception:
            items = []   # resolved below: the id may be a COURSE id

    if not items:
        # Fall back: the "content id" may actually be a COURSE id the user
        # passed to list the course's contents (a course id is not a content
        # id, and /contents/<course_id> does not exist).
        cand = course_id or content_id
        if with_files:
            _list_course_files(cand)
            return
        from .query import discover_pages
        rows = []
        try:
            rows = discover_pages(cand)
        except Exception:
            rows = []
        if rows:
            click.secho(f"  📚 Contents of course {cand} "
                        f"(passed as content id):\n", fg="cyan")
            for cid, title, section in rows:
                icon = "📁" if title.startswith("--") else " •"
                where = f"  [{section}]" if section else ""
                click.echo(f"  {icon} [{cid}] {title}{where}")
            click.echo("\n  Show one item's files:  sustech bb page <content_id>")
            return
        click.secho("  No items found on this page.", fg="yellow")
        return

    for item in items:
        type_icon = {
            "file": "📄", "video": "🎬", "homework": "📝",
            "folder": "📁", "inline": "📄", "link": "🔗",
            "text": "📃", "unknown": "❓",
        }.get(item.TYPE, "?")
        type_label = {"inline": "document"}.get(item.TYPE, item.TYPE)
        click.echo(f"  {type_icon} [{item.sub_id}] {item.title}")
        click.echo(f"       type={type_label}")

        # Attachments are the payload an agent is after (manual PDF, slide
        # deck, template docx) — always show them, not only with -v.
        atts = getattr(item, "files", None) or []
        for name, _url in atts:
            click.echo(f"       📎 {name}")
        if atts:
            click.echo(f"       ⤓ download: sustech bb download {item.sub_id}")

        if verbose:
            if getattr(item, "inline_imgs", None):
                click.echo(f"       🖼 {len(item.inline_imgs)} inline image(s)")
            if hasattr(item, "video_url") and item.video_url:
                click.echo(f"       🎬 video: {item.video_url}")
            if hasattr(item, "bb_url") and item.bb_url:
                click.echo(f"       📁 → {item.bb_url}")
            if item.description:
                click.echo(f"       💬 {item.description[:120]}")
            if hasattr(item, "deadline") and item.deadline:
                click.echo(f"       ⏰ deadline: {item.deadline}")
            if hasattr(item, "submission_count") and item.submission_count > 0:
                click.echo(f"       ✅ {item.submission_count} submission(s)")
        print()


@cli.command(name="course")
@click.argument("course_id")
@click.argument("sub", default=None, required=False)
@click.argument("content_id", default=None, required=False)
@click.argument("attempt_arg", default=None, required=False)
@click.option("--download", "download_flag", is_flag=True, default=False,
              help="Download the specified attempt")
@click.option("-o", "--output", "output_dir", default="./downloads",
              help="Output directory")
def course_cmd(course_id, sub, content_id, attempt_arg, download_flag, output_dir):
    """
    View and manage course assignments and submission attempts.

    Examples:
      bb_cli.py course 8053 assignments                    # list all + attempts overview
      bb_cli.py course 8053 assignment status             # all: submitted/not submitted
      bb_cli.py course 8053 assignment 619093              # all attempts for that assignment
      bb_cli.py course 8053 assignment 619093 status       # status of specific assignment
      bb_cli.py course 8053 assignment 619093 attempts     # all attempts (explicit)
      bb_cli.py course 8053 assignment 619093 2            # details of attempt 2
      bb_cli.py course 8053 assignment 619093 2 --download # download attempt 2
    """
    cookies = load_session_or_exit()

    results = find_course(course_id)
    if not results:
        click.secho(f"❌  No course found for '{course_id}'", fg="red")
        sys.exit(1)
    course_id_str, course_name = results[0]
    numeric_cid = get_course_numeric_id(course_id_str)

    needs_assignments = (
        sub in ("assignments", None)
        or (sub == "assignment" and content_id == "status")
    )
    all_assignments = (
        discover_assignments_for_course(course_id_str) if needs_assignments else []
    )

    # Pure REST: the discovery/attempt helpers accept ctx only for API
    # compatibility with the old Playwright-based callers — they ignore it.
    ctx = None
    if sub == "assignments" or sub is None:
        list_assignments(ctx, numeric_cid, course_name, all_assignments)
    elif sub == "assignment":
        if content_id is None:
            click.secho("❌  assignment needs a content_id or 'status'", fg="red")
            sys.exit(1)
        if content_id == "status":
            all_status(ctx, numeric_cid, course_name, all_assignments)
        else:
            single_assignment(ctx, cookies, numeric_cid,
                               content_id, attempt_arg, download_flag, output_dir)
    else:
        click.secho(f"❌  Unknown: {sub}", fg="red")
        sys.exit(1)


@cli.command(name="submit", help="Submit a file to a BB assignment (pure REST, no browser).")
@click.argument("content_id", required=False)
@click.argument("arg2", required=False)
@click.argument("arg3", required=False)
@click.option("-c", "--course", "course_id", help="Course ID (numeric). Auto-resolved if omitted.")
@click.option("--comment", "comment", default=None, help="Optional comment text (posted after the file lands).")
@click.option("--expected-sha256", "expected_sha256", default=None,
              help="apply only: the SHA-256 the file had when you reviewed it (from `submit preview`).")
@click.option("--expected-size", "expected_size", type=int, default=None,
              help="apply only: the byte size shown by `submit preview`.")
@click.option("--confirm", is_flag=True, default=False,
              help="apply only: required — acknowledges a real submission.")
def submit_cmd(content_id, arg2, arg3, course_id, comment, expected_sha256,
               expected_size, confirm):
    """
    Submit a file to a BB assignment (pure REST, no browser).

    Three forms:

      bb.py submit 637897 report.pdf                  # one-shot (legacy)
      bb.py submit preview 637897 report.pdf          # describe it, uploads nothing
      bb.py submit apply 637897 report.pdf --expected-sha256 HEX --confirm
    """
    from pathlib import Path as _Path
    from sustech_survival.bb.submit import (
        FileChangedSinceReviewError,
        SubmissionNotConfirmedError,
        apply_submission,
        preview_submission,
        submit_file,
    )

    mode = None
    if content_id in ("preview", "apply"):
        mode, content_id, arg2 = content_id, arg2, arg3

    if not content_id or not arg2:
        click.secho("✗  Usage: bb submit [preview|apply] CONTENT_ID FILE_PATH", fg="yellow")
        click.echo("   preview  describe the submission without uploading")
        click.echo("   apply    submit for real (needs --expected-sha256 and --confirm)")
        sys.exit(2)

    file_path = arg2
    if not _Path(file_path).is_file():
        click.secho(f"❌  Not a file: {file_path}", fg="red")
        sys.exit(1)

    load_session_or_exit()

    if mode == "preview":
        try:
            plan = preview_submission(content_id, file_path, course_id=course_id,
                                      comment=comment)
        except Exception as e:
            click.secho(f"❌  Preview failed: {e}", fg="red")
            sys.exit(1)
        click.secho("\n📋 Submission preview — nothing uploaded", fg="cyan", bold=True)
        click.echo(f"   assignment    {plan['assignment_name'] or '(unnamed)'}"
                   f"   [{plan['content_id']}]  course {plan['course_id']}")
        click.echo(f"   action        {plan['action']}  ({plan['attempts_so_far']} so far)")
        click.echo(f"   file          {plan['file_name']}  ({plan['size_bytes']:,} bytes)")
        click.echo(f"   sha256        {plan['sha256']}")
        if plan["comment"]:
            click.echo(f"   comment       yes → field {plan['comment_field']}")
        click.echo("\n   Review the file, then submit these exact bytes:")
        click.secho(f"   sustech bb submit apply {content_id} {file_path} \\\n"
                    f"     --expected-sha256 {plan['sha256']} "
                    f"--expected-size {plan['size_bytes']} --confirm", fg="green")
        return

    if mode == "apply":
        try:
            result = apply_submission(content_id, file_path, course_id=course_id,
                                      submitted_name=None,
                                      expected_sha256=expected_sha256,
                                      expected_size=expected_size,
                                      confirm=confirm, comment=comment)
        except FileChangedSinceReviewError as e:
            click.secho(f"❌  {e}", fg="red")
            sys.exit(1)
        except SubmissionNotConfirmedError as e:
            click.secho(f"❌  {e}", fg="red")
            sys.exit(2)
        except Exception as e:
            click.secho(f"❌  Submission failed: {e}", fg="red")
            sys.exit(1)
        ok, msg = result.to_tuple() if hasattr(result, "to_tuple") else (bool(result), str(result))
        if ok:
            click.secho(f"✓  Submitted {_Path(file_path).name} (hash verified): {msg}", fg="green")
        else:
            click.secho(f"⚠  {msg}", fg="yellow")
            sys.exit(1)
        return

    # Legacy one-shot form: no review step, no hash check.
    try:
        ok, msg = submit_file(content_id, file_path, course_id=course_id)
        if ok:
            click.secho(f"✓  Submission successful! {msg}", fg="green")
        else:
            click.secho(f"⚠  {msg}", fg="yellow")
    except Exception as e:
        click.secho(f"❌  Submission failed: {e}", fg="red")
        sys.exit(1)


@cli.command(name="download", help="Download BB content files (PDF, slides, attachments).")
@click.argument("content_id", required=False)
@click.option("--content", "content_opt", default=None,
              help="Legacy alias for the CONTENT_ID argument.")
@click.option("-c", "--course", "course_id", default=None,
              help="Course ID (numeric). Auto-resolved from CONTENT_ID if omitted.")
@click.option("--output", "output_dir", default="./downloads", show_default=True,
              help="Output directory (created if missing).")
def download_cmd(content_id, content_opt, course_id, output_dir) -> None:
    """Download BB course-content files to a local directory.

    Examples:
        sustech bb download 610821
        sustech bb download 610821 --output ~/Documents/BB
        sustech bb download 610821 -c 8534     # explicit course
    """
    from pathlib import Path
    from .download import download_content
    content_id = content_id or content_opt
    load_session_or_exit()
    if not content_id:
        click.secho("(no content id given) ×", fg="yellow")
        click.echo("  Find one with: sustech bb page <course_id>  "
                   "(lists every item + attachment)")
        return
    out = Path(output_dir)
    try:
        saved = download_content(content_id, out, course_id=course_id)
    except Exception as e:
        click.secho(f"❌  Download failed: {e}", fg="red", err=True)
        raise SystemExit(1)
    if saved:
        click.secho(f"✅  {len(saved)} file(s) → {out}", fg="green")
    else:
        click.secho(f"⚠  No downloadable files for content {content_id}.", fg="yellow")


if __name__ == "__main__":
    cli()
