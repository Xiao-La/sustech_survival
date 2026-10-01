"""One central timeout for every network layer, and a guard that keeps it that way.

2026-09-10: ``tis/grades.py`` and ``tis/courses.py`` hardcoded ``timeout=15``,
bypassing the config tree. ``sustech tis grades`` therefore died whenever CAS
refresh + fetch exceeded 15 s (looked like a hang). The fix was to resolve every
request through :mod:`sustech_survival._net`.

The rules this file enforces, and why each one exists:

1. **A request timeout must come from ``_net``.** ``service_timeout("tis")`` (and
   friends) are the only way an HTTP budget may be written; a literal is a
   regression because it ignores ``config.json`` and the operator's timing.
2. **A browser *default* must come from ``_net`` too.** ``page.set_default_timeout``
   sets the budget for everything that follows; a literal there silently caps the
   whole flow.
3. **A browser per-call wait may be a literal only under a reasoned exemption.**
   Short probes ("is the cookie banner there?") are deliberately shorter than any
   config budget, and each sits in its own ``try``/``except``. Those exemptions are
   listed in ``BROWSER_WAIT_EXEMPT`` with a reason, and kept honest by
   ``test_browser_wait_exemptions_are_live``. ``timeout=0`` is Playwright's
   "wait indefinitely" sentinel, not a budget.
4. **A local process wait is not a network budget.** ``subprocess.run(...,
   timeout=5)`` bounds a local command; it never reaches the network.

Classification is by *enclosing call*, not by line: the callee can sit on an
earlier line of a multi-line expression, and a rule that only looked at the line
holding the literal used to flag ``page.goto(\n timeout=30000)`` while excusing
``page.goto(..., timeout=30000)`` — the same defect, two different verdicts.
"""
from __future__ import annotations

import pathlib
import re

from sustech_survival import _net

SRC = pathlib.Path(__file__).resolve().parents[1] / "sustech_survival"

LITERAL = re.compile(r"timeout\s*=\s*(\d+(?:\.\d+)?)")
# ``set_default_timeout(30000)`` is positional — no ``timeout=`` to match, which
# is exactly the form that silently caps a whole Playwright flow.
POSITIONAL_DEFAULT = re.compile(r"set_default_(?:navigation_)?timeout\s*\(\s*(\d+(?:\.\d+)?)\s*\)")

HTTP_CALL = (
    "requests.", "httpx.", "urlopen", "URLopener", "http.client",
    "session.get", "session.post", "session.put", "session.patch",
    "sess.get", "sess.post", "sess.put", "sess.patch",
    "put(", "patch(",
)
BROWSER_DEFAULT = ("set_default_timeout(", "set_default_navigation_timeout(")
BROWSER_CALL = (
    "page.", ".goto(", ".click(", ".fill(", ".locator(", ".press(", ".hover(",
    ".check(", ".select_option(", ".inner_text(", ".inner_html(", ".screenshot(",
    "wait_for_selector", "wait_for_url", "wait_for_load_state", "wait_for_function",
    "wait_for_response", "new_page(", "new_context(", "expect(", "frame.",
    "browser_context", "goto(",
)
# A fixed pause is not a budget: ``page.wait_for_timeout(8000)`` sleeps, it does
# not bound a wait, so it is allowed to stay a literal.
SLEEP_CALL = ("wait_for_timeout(",)
PROCESS_CALL = (
    "subprocess", "Popen", "capture_output", "check_output", ".communicate(",
)

# Per-call browser waits that are deliberately hand-tuned rather than a config
# budget. Every entry needs a reason; test_browser_wait_exemptions_are_live makes
# a stale entry fail, so a rename cannot whitelist a new file by accident.
BROWSER_WAIT_EXEMPT: dict[str, str] = {
    "papers/wos_integration.py": (
        "Soft-deprecated WoS flow: 3-20 s waits on optional cookie banners and "
        "CAS redirects, each inside its own try/except, so a miss degrades "
        "instead of hanging for the full page budget"
    ),
}


def _rel(path: pathlib.Path) -> str:
    return str(path.relative_to(SRC)).replace("\\", "/")


# Any call that names a service: _net.service_timeout("tis"), the bound method
# form _net.timeouts().service_timeout("tis"), and the page equivalents.
SERVICE_CALL = re.compile(
    r'_net\.(?:timeouts\(\)\.)?(?:service_timeout|service_login_timeout'
    r'|page_timeout_ms|page_timeout)\("([A-Za-z_-]+)"\)'
)
# Section keys. In the service slot they resolve to the section default, so a
# per-service override never applies.
SECTION_KEYS = frozenset({"http", "login", "page", "default", "timeouts", "services"})


def _documented_services() -> set[str]:
    """Service names from the "Services in use:" block of _net's docstring."""
    doc = _net.__doc__ or ""
    marker = "Services in use:"
    assert marker in doc, "sustech_survival._net lost its 'Services in use:' doc block"
    block = doc.split(marker, 1)[1].split("\n\n", 1)[0]
    return {name for name in re.split(r"[,\s]+", block) if name}


def _enclosing_call(text: str, offset: int) -> str:
    """Text of the call head whose parentheses enclose ``offset``.

    Scans *backwards from the literal*, so it does not matter whether the opening
    or the closing parenthesis comes first on the page: everything after the
    literal (including a trailing ``)``) is never seen.
    """
    depth = 0
    i = offset
    while i >= 0:
        char = text[i]
        if char == ")":
            depth += 1
        elif char == "(":
            if depth == 0:
                start = text.rfind("\n", 0, i) + 1
                return text[start: i + 1]
            depth -= 1
        i -= 1
    return ""


def _classify(call: str) -> str:
    if any(hint in call for hint in SLEEP_CALL):
        return "sleep"
    if any(hint in call for hint in PROCESS_CALL):
        return "process-wait"
    if any(hint in call for hint in BROWSER_DEFAULT):
        return "browser-default"
    if any(hint in call for hint in BROWSER_CALL):
        # Playwright's "no timeout" sentinel is not a budget.
        return "browser-wait"
    return "http"  # every other callee: treat as a budget, a human decides


def _scan(path: pathlib.Path, *, ignore_exemptions: bool = False) -> list[tuple[str, int, str]]:
    """Return [(kind, lineno, line_text)] for timeout literals outside ``_net``.

    Kinds: ``sleep`` and ``process-wait`` (allowed), ``browser-default``,
    ``browser-wait``, ``http`` (must be routed through ``_net``, unless the file
    is exempt for ``browser-wait``).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = _rel(path)
    exempt = rel in BROWSER_WAIT_EXEMPT and not ignore_exemptions
    hits: list[tuple[str, int, str]] = []

    matches = list(LITERAL.finditer(text)) + list(POSITIONAL_DEFAULT.finditer(text))
    for match in sorted(matches, key=lambda m: m.start()):
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.start())
        line = text[line_start: line_end if line_end != -1 else len(text)]
        if "_net" in line or "PAGE_TIMEOUT" in line:
            continue  # already routed through the config tree

        call = _enclosing_call(text, match.start()) or line
        kind = _classify(call)

        if kind in ("sleep", "process-wait"):
            continue
        if kind == "browser-wait" and (exempt or match.group(1) == "0"):
            continue
        hits.append((kind, text.count("\n", 0, match.start()) + 1, line.strip()))
    return hits


def test_defaults_are_180_seconds():
    assert _net.HTTP_DEFAULT == 180.0
    assert _net.LOGIN_DEFAULT == 180.0
    assert _net.PAGE_DEFAULT == 180.0

    snapshot = _net.NetworkTimeouts.from_config({})
    assert snapshot.service_timeout("tis") == 180.0
    assert snapshot.service_login_timeout("bb") == 180.0
    # An unknown service still lands on the section default, not on 0/None.
    assert snapshot.service_timeout("no-such-service") == 180.0
    # The browser layer is a config leaf too, in seconds, exposed in ms.
    assert snapshot.page_timeout() == 180.0
    assert snapshot.page_timeout("no-such-service") == 180.0
    assert snapshot.page_timeout_ms() == 180_000


def test_config_json_overrides_win():
    snapshot = _net.NetworkTimeouts.from_config(
        {
            "http": {"default": 45},
            "login": {"default": 50},
            "page": {"default": 300},
            "services": {"tis": {"http": 90, "login": 90, "page": 240}},
        }
    )
    assert snapshot.service_timeout("bb") == 45.0
    assert snapshot.service_timeout("tis") == 90.0
    assert snapshot.service_login_timeout("tis") == 90.0
    assert snapshot.service_login_timeout("bb") == 50.0
    # per-service page override wins over the page section default
    assert snapshot.page_timeout("tis") == 240.0
    assert snapshot.page_timeout_ms("tis") == 240_000
    assert snapshot.page_timeout("bb") == 300.0


def test_shipped_config_file_carries_the_tree():
    """The operator-editable file must exist and actually govern resolution."""
    from sustech_survival import _cache

    path = _cache.config_file()
    if not path.exists():
        return  # a fresh machine: code defaults apply, tree is optional
    tree = _cache.load_config().get("timeouts") or {}
    assert tree, "config.json exists but carries no timeouts tree"
    snapshot = _net.timeouts()
    assert snapshot.http.default == float(tree["http"]["default"])
    if "page" in tree:
        assert snapshot.page_timeout() == float(tree["page"]["default"])


def test_no_module_hardcodes_a_request_timeout():
    """A literal timeout outside the exemptions is a regression, not a choice."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "_net.py":
            continue
        for kind, lineno, text in _scan(path):
            offenders.append(f"{kind}: {_rel(path)}:{lineno}: {text[:90]}")
    assert not offenders, "hardcoded timeouts:\n" + "\n".join(offenders)


def test_no_section_key_is_used_as_a_service_name():
    """`service_timeout("http")` silently means "section default".

    A section key in the service slot makes ``services.<name>`` unreachable —
    the operator sets ``services.tis.http = 90`` and the call still uses the
    180 s section default.
    """
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        for match in SERVICE_CALL.finditer(path.read_text(encoding="utf-8", errors="replace")):
            if match.group(1) in SECTION_KEYS:
                offenders.append(f"{_rel(path)}: {match.group(1)}")
    assert not offenders, (
        "a section key was passed where a service name belongs:\n" + "\n".join(offenders)
    )


def test_service_vocabulary_matches_the_doc():
    """Every service name in use is documented in _net, and vice versa.

    The doc block is the vocabulary an operator reads before writing
    ``services.<name>``; a name that only exists in code is a name they cannot
    discover.
    """
    used: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        for match in SERVICE_CALL.finditer(path.read_text(encoding="utf-8", errors="replace")):
            used.add(match.group(1))

    documented = _documented_services()
    undocumented = sorted(used - documented)
    unused = sorted(documented - used)
    assert not undocumented, f"service names missing from _net's doc list: {undocumented}"
    assert not unused, f"documented service names nothing uses: {unused}"


def test_browser_wait_exemptions_are_live():
    """An exemption that no longer excuses anything hides the next regression."""
    for rel, reason in BROWSER_WAIT_EXEMPT.items():
        path = SRC / rel
        assert path.exists(), f"exemption points at a missing file: {rel}"
        assert reason.strip(), f"exemption without a reason: {rel}"
        hits = [h for h in _scan(path, ignore_exemptions=True) if h[0] == "browser-wait"]
        assert hits, (
            f"stale exemption: {rel} contains no per-call browser timeout literal "
            f"any more — remove it from BROWSER_WAIT_EXEMPT (reason on file: {reason!r})"
        )


def test_exemptions_do_not_hide_other_kinds():
    """An exemption may only ever hide ``browser-wait`` hits, never another kind."""
    for rel in BROWSER_WAIT_EXEMPT:
        path = SRC / rel
        with_exemptions = _scan(path)
        without = _scan(path, ignore_exemptions=True)
        hidden = [hit for hit in without if hit not in with_exemptions]
        assert all(kind == "browser-wait" for kind, _n, _t in hidden), (
            f"{rel}: exemptions hid something other than a per-call "
            f"browser wait: {hidden}"
        )
