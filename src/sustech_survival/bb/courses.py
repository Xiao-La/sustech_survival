from .. import _net
# Courses — BB course data loading and discovery via REST API
"""
Course loading, listing, finding via REST API (no Playwright).

REST-only flow:
  1. /users/me                    → current user ID
  2. /users/{uid}/courses        → enrollment records with courseId
  3. /courses/{courseId}         → course name + details
"""

import json
import re
import sys
import time
from pathlib import Path
from typing import List, Dict

BB_BASE = "https://bb.sustech.edu.cn"


from sustech_survival.exceptions import SessionExpired as _SessionExpired
from .query import _core_id

BB_DIR = Path(__file__).resolve().parent
COURSES_FILE = BB_DIR / "courses.json"

SKIP_COURSE_NAMES = {
    '大学物理', '高等数学', 'college physics', 'higher mathematics',
    '微积分', '线性代数', 'calculus', 'linear algebra',
}


# -- REST-based course discovery ------------------------------------------------

def session():
    """Return an authenticated requests.Session from the SSO BBAuth layer."""
    from sustech_survival.sso import BBAuth
    bb_auth = BBAuth()
    ok, reason = bb_auth.ensure()
    if not ok:
        raise _SessionExpired(f"BB auth failed: {reason}")
    return bb_auth.session


_session = session  # alias: api()'s `session` param shadows the module factory


def api(path, session=None):
    """GET BB REST endpoint. Returns JSON. Dies on auth error."""
    if session is None:
        session = _session()
    r = session.get(BB_BASE + path, timeout=_net.service_timeout("bb"))
    if r.status_code == 401:
        raise _SessionExpired("BB session expired. Run `bb.py login` to refresh.")
    r.raise_for_status()
    return r.json()


def scrape_enrolled_courses() -> List[Dict[str, str]]:
    """
    Complete enrolled-course list via ONE REST call.

    ``/users/{uid}/courses?expand=course`` inlines the course object, so names
    come back with the enrollment — no per-course follow-up calls (the old
    loop cost 1 + N requests) and nothing is filtered out (the old
    ``SKIP_COURSE_NAMES`` pass silently hid real courses from the list).

    Returns list of dicts:
        [{"id": "_8343_1", "name": "Physical Chemistry...", "href": ""}, ...]
    """
    me = api("/learn/api/public/v1/users/me")
    uid = me["id"]

    enrollments = api(f"/learn/api/public/v1/users/{uid}/courses?expand=course&limit=200")
    seen_ids = set()
    term_cache: Dict[str, str] = {}
    courses = []

    for enrollment in enrollments.get("results", []):
        course_id = enrollment.get("courseId", "")
        if not course_id or course_id in seen_ids:
            continue
        course_obj = enrollment.get("course") or {}
        name = course_obj.get("name", "")
        if not name:
            try:
                name = api(f"/learn/api/public/v1/courses/{course_id}").get("name", "")
            except Exception:
                name = ""
        term_id = course_obj.get("termId", "") or ""
        term_name = ""
        if term_id:
            if term_id not in term_cache:
                try:
                    term_cache[term_id] = api(f"/learn/api/public/v1/terms/{term_id}").get("name", "")
                except Exception:
                    term_cache[term_id] = ""
            term_name = term_cache[term_id]
        seen_ids.add(course_id)
        courses.append({
            "id": course_id,
            "name": name or "(unnamed course)",
            "href": "",
            "term_id": term_id,
            "term": term_name,          # e.g. "2026秋（Fall 2026）"
            "last_accessed": (enrollment.get("lastAccessed") or "")[:10],
        })

    return courses


def refresh_courses_json(quiet: bool = False) -> List[Dict[str, str]]:
    """Fetch the complete enrolled-course list live and update courses.json.

    Also stores the site's own term table (``/terms``) alongside the courses,
    which is what `--semester` resolves against.
    """
    courses = scrape_enrolled_courses()
    terms = fetch_site_terms()
    COURSES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COURSES_FILE, 'w') as f:
        json.dump({"courses": courses, "terms": terms,
                   "ts": __import__('time').time()}, f, indent=2, ensure_ascii=False)
    if not quiet:
        print(f"Updated {COURSES_FILE} with {len(courses)} courses "
              f"({len(terms)} terms).")
        for c in courses:
            print(f"  - {c['name']} ({c['id']})")
    return courses


# -- Course Data ----------------------------------------------------------------

def refresh_if_stale(max_age_hours=24):
    """Refresh courses.json if it is older than max_age_hours or missing."""
    if COURSES_FILE.exists():
        try:
            with open(COURSES_FILE) as f:
                data = json.load(f)
            age = (time.time() - data.get("ts", 0)) / 3600
            if age < max_age_hours:
                return  # fresh enough
        except Exception:
            pass
    refresh_courses_json(quiet=True)


def load_courses():
    """
    Load course list from courses.json. Auto-refreshes if stale (>24h).
    Returns list of course dicts.
    """
    refresh_if_stale()
    if not COURSES_FILE.exists():
        return []
    with open(COURSES_FILE) as f:
        data = json.load(f)
    return data.get("courses", [])


def get_course_numeric_id(course_id_str):
    """Extract numeric part from '_8343_1' → '8343'."""
    m = re.search(r"_(\d+)_", course_id_str)
    return m.group(1) if m else course_id_str


def extract_code(name: str) -> list:
    """Extract course codes from a name string (e.g. 'MSE202', 'MSE002-003')."""
    return re.findall(r'[A-Z]{2,6}[-_]?\d{3,4}[A-Z]?', name, re.IGNORECASE)


def codes_normalized(codes: list) -> set:
    """Normalize codes for fuzzy comparison: strip non-alpha prefixes and trailing letter suffixes."""
    normalized = set()
    for code in codes:
        base = re.sub(r'^([A-Z]+)', r'\1', code, flags=re.IGNORECASE).lower()
        digits = re.sub(r'[^0-9]', '', base)
        if digits:
            normalized.add(digits)
    return normalized


def find_course(query):
    """
    Find courses matching query.

    Accepts any form a user or agent is likely to copy:
      • the numeric ID      '8613'      (what `bb courses` prints)
      • the BB form         '_8613_1'
      • a name substring    'thermochromic', '材料学综合'

    Returns list of (course_id_str, name) tuples; [] when nothing matches.
    """
    courses = load_courses()
    q = query.lower().strip()
    q_numeric = re.sub(r"[^0-9]", "", q)
    results = []

    for c in courses:
        cid = c["id"]
        name = c.get("name", "")
        if (q == cid.lower()
                or (q_numeric and q_numeric == get_course_numeric_id(cid))
                or q in name.lower()):
            results.append((cid, name))

    if results:
        return results

    # No local match -- try live fetch across all enrolled courses
    try:
        live = api("/learn/api/public/v1/users/me")
        uid = live["id"]
        enrollments = api(f"/learn/api/public/v1/users/{uid}/courses?expand=course&limit=200")
        for e in enrollments.get("results", []):
            cid = e.get("courseId", "")
            if not cid:
                continue
            name = (e.get("course") or {}).get("name", "")
            if (q in name.lower()
                    or q == cid.lower()
                    or (q_numeric and q_numeric == get_course_numeric_id(cid))):
                results.append((cid, name))
    except Exception:
        pass

    return results


def list_courses():
    """Return all courses as (course_id_str, name) tuples."""
    return [(c["id"], c.get("name", "Unknown")) for c in load_courses()]


# -- Semester / freshness helpers ---------------------------------------------

def course_list_meta() -> Dict:
    """Freshness of the cached course list: {"ts": float, "count": int}."""
    if not COURSES_FILE.exists():
        return {"ts": 0.0, "count": 0}
    try:
        with open(COURSES_FILE) as f:
            data = json.load(f)
        return {"ts": float(data.get("ts", 0) or 0), "count": len(data.get("courses", []))}
    except Exception:
        return {"ts": 0.0, "count": 0}


def semester_labels() -> List[str]:
    """Distinct term names present in the cached course list (order preserved)."""
    seen, out = set(), []
    for c in load_courses():
        t = c.get("term") or ""
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def fetch_site_terms() -> List[Dict]:
    """Authoritative term list from the BB site: [{id, name, start, end}].

    ``/learn/api/public/v1/terms`` is the site's own semester table (every
    term, with its date range) — the basis for `--semester`, not text
    scavenged from course descriptions.
    """
    try:
        data = api("/learn/api/public/v1/terms?limit=200")
    except Exception:
        return []
    out = []
    for t in data.get("results", []):
        dur = (t.get("availability") or {}).get("duration") or {}
        out.append({
            "id": t.get("id", ""),
            "name": t.get("name", ""),
            "start": (dur.get("start") or "")[:10],
            "end": (dur.get("end") or "")[:10],
        })
    return out


def term_code(term: Dict) -> str:
    """SUSTech-style academic code for a term: '2026-2027-1' (fall) / '-2'
    (spring) / '-3' (summer).

    Derived from the site's term NAME (2026秋 / 2026春 / 2026夏), not from its
    start date — BB's term dates are irregular (2025春 starts 2024-12-31, and
    summer terms start in June), so dates cannot encode the academic year.
    """
    name = term.get("name") or ""
    m = re.search(r"(20\d\d)", name)
    if not m:
        return ""
    year = int(m.group(1))
    low = name.lower()
    if "秋" in name or "fall" in low:
        acad, seq = year, 1
    elif "春" in name or "spring" in low:
        acad, seq = year - 1, 2
    elif "夏" in name or "summer" in low:
        acad, seq = year - 1, 3
    else:
        return ""
    return f"{acad}-{acad + 1}-{seq}"


def site_terms() -> List[Dict]:
    """The site term table cached with the course list (empty if never fetched)."""
    if not COURSES_FILE.exists():
        return []
    try:
        with open(COURSES_FILE) as f:
            return json.load(f).get("terms", []) or []
    except Exception:
        return []


def resolve_semester(query: str) -> List[Dict]:
    """Resolve a semester argument to entries of the site term table.

    Accepted forms (whichever the user or an agent copies off the site):
      • term id        '_58_1'
      • term name      '2026秋', 'fall 2026', 'Spring 2026'
      • academic code  '2026-2027-1' / '2026-2027-2'
      • bare year      '2026'  (every term of that year)
    """
    q = (query or "").strip()
    if not q:
        return []
    ql = q.lower()
    terms = site_terms()

    if q.startswith("_") or re.fullmatch(r"\d+", q):
        hits = [t for t in terms if t["id"].strip("_") == q.strip("_")
                or t["id"] == f"_{q}_1" or t["id"] == q]
        if hits:
            return hits

    if re.fullmatch(r"\d{4}-\d{4}-\d", q):
        return [t for t in terms if term_code(t) == q]

    hits = [t for t in terms if ql in (t.get("name") or "").lower()]
    if hits:
        return hits

    year = re.search(r"(20\d\d)", ql)
    if year:
        by_year = [t for t in terms if year.group(1) in (t.get("name") or "")]
        if by_year:
            return by_year
    return []


def courses_in_semester(semester: str) -> List[str]:
    """Ids (BB form, as in courses.json) of enrolled courses in `semester`.

    Resolution goes through :func:`resolve_semester` (site term table), so
    the filter is b'y the site's own semester list. Unresolvable input →
    [] so callers can print the available terms.
    """
    if not semester:
        return [c["id"] for c in load_courses()]
    term_ids = {t["id"] for t in resolve_semester(semester)}
    if not term_ids:
        return []
    return [c["id"] for c in load_courses() if c.get("term_id") in term_ids]


# -- Assignment Discovery (REST-only) -----------------------------------------

def discover_assignments_for_course(course_id_str):
    """
    Discover all BB assignment slots for a course via gradebook REST API.

    Returns list of (assignment_content_id, title) tuples.
    No Playwright needed -- contentId from gradebook columns is the content ID
    used in uploadAssignment URLs.
    """
    numeric_match = re.search(r"_(\d+)_1", course_id_str)
    if not numeric_match:
        numeric_match = re.search(r"^_?(\d+)_?1?$", course_id_str)
    numeric_cid = numeric_match.group(1) if numeric_match else course_id_str

    bid = f"_{numeric_cid}_1"
    try:
        cols = api(
            f"/learn/api/public/v1/courses/{bid}/gradebook/columns"
            f"?_fields=id,name,contentId,grading",
        )
    except Exception:
        return []

    results = []
    for col in cols.get("results", []):
        content_id = col.get("contentId", "")
        if not content_id:
            continue
        cid_numeric = _core_id(content_id)
        name = col.get("name", "") or f"Assignment {cid_numeric}"
        results.append((cid_numeric, name))

    return results