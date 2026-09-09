"""Shared eHall browser session (Playwright-backed).

Why a browser: eHall's ``amp-auth-adapter`` completes authentication
server-side only for requests initiated by portal JavaScript. After a
valid CAS + ticket chain, plain HTTP still receives 403 from app APIs
(``/dxggyw/sys/<app>/**``). Loading the app index once in a real
Chromium lets the EMAP bootstrap run; afterwards the browser context's
request API (which shares the cookie jar) can serve JSON endpoints.

Wire facts verified 2026-09-09 (see sustech-dev records):
- Login chain: ``/amp-auth-adapter/login?service=<url>`` → CAS login
  (execution token) → POST credentials → ``loginSuccess?sessionToken=
  …&ticket=ST-…`` → redirect to ``<service>?ticket=<uuid>``.
- Cookies afterwards: ``route``, ``CASTGC`` (ehall host), ``TGC``
  (cas host); the amp session cookie is set by portal JS on page load.
- This module never holds credentials: sid/password come from the
  shared credentials file (``$SUSTECH_CREDENTIALS_FILE`` or
  ``~/.sustech_survival/credentials.txt``, first ``sid:password`` line).
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote

try:  # playwright is an optional dependency (needed for eHall)
    from playwright.sync_api import BrowserContext, Page, sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None  # type: ignore[assignment]

from ..exceptions import APIError as SUSTechError

EHALL_BASE = "https://ehall.sustech.edu.cn"
CREDENTIALS_ENV = "SUSTECH_CREDENTIALS_FILE"


class EhallError(SUSTechError):
    """Base error for eHall operations."""


class EhallAuthError(EhallError):
    """Login failed (CAS reject, captcha wall, or session not established)."""


def _load_credentials() -> "tuple[str, str]":
    path = os.environ.get(CREDENTIALS_ENV) or os.path.expanduser(
        "~/.sustech_survival/credentials.txt"
    )
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                sid, _, pwd = line.partition(":")
                if sid and pwd:
                    return sid, pwd
    except OSError as exc:  # pragma: no cover
        raise EhallAuthError(f"cannot read credentials file {path}: {exc}") from exc
    raise EhallAuthError(f"no sid:password line found in {path}")


def _find_chromium() -> Optional[str]:
    """Locate a usable chromium binary for Playwright."""
    candidates = [
        os.environ.get("SUSTECH_EHALL_CHROMIUM", ""),
        os.path.expanduser(
            "~/Library/Caches/ms-playwright/chromium_headless_shell-1228/"
            "chrome-headless-shell-mac-arm64/chrome-headless-shell"
        ),
        os.path.expanduser(
            "~/Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/"
            "Chromium.app/Contents/MacOS/Chromium"
        ),
    ]
    for cand in candidates:
        if cand and os.path.exists(cand):
            return cand
    return None


@dataclass
class _BrowserState:
    p: Any  # sync_playwright instance
    context: BrowserContext
    page: Page


class EhallSession:
    """One authenticated browser session against eHall.

    ``ensure()`` logs in lazily and bootstraps the app index page so the
    EMAP session is live. Data calls then go through ``get_json`` /
    ``post_form`` which use the Playwright request API (shared jar).
    """

    CLE_INDEX = EHALL_BASE + "/dxggyw/sys/yyzxyy/*default/index.do"
    LOGIN_PROBE = EHALL_BASE + "/jsonp/userInfo.json"

    def __init__(self, headless: bool = True, executable: Optional[str] = None):
        if sync_playwright is None:  # pragma: no cover
            raise EhallError(
                "eHall needs Playwright: pip install 'sustech-survival[playwright]'"
            )
        self.headless = headless
        self.executable = executable or _find_chromium()
        self._lock = threading.Lock()
        self._state: Optional[_BrowserState] = None

    # -- lifecycle --------------------------------------------------------

    def ensure(self) -> "_BrowserState":
        with self._lock:
            if self._state is None:
                self._state = self._open()
                self._login()
            return self._state

    def close(self) -> None:
        with self._lock:
            if self._state is not None:
                try:
                    self._state.p.stop()
                finally:
                    self._state = None

    def _open(self) -> _BrowserState:
        if sync_playwright is None:  # pragma: no cover
            raise EhallError("playwright is not installed")
        p = sync_playwright().start()
        try:
            if self.executable:
                browser = p.chromium.launch(
                    headless=self.headless, executable_path=self.executable
                )
            else:
                browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/151.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
            )
            return _BrowserState(p=p, context=context, page=context.new_page())
        except Exception:
            p.stop()
            raise

    def _login(self) -> None:
        """CAS + amp chain, then bootstrap the CLE app index page."""
        assert self._state is not None
        req = self._state.context.request
        sid, pwd = _load_credentials()
        # 1) amp gate → CAS login page (request API follows redirects)
        r1 = req.get(
            EHALL_BASE + "/amp-auth-adapter/login?service=" + quote(self.LOGIN_PROBE, safe=""),
            timeout=45_000,
        )
        m = re.search(r'name=["\']execution["\']\s+value=["\']([^"\']+)["\']', r1.text())
        if not m:
            raise EhallAuthError("CAS login page did not include an execution token")
        # 2) credential POST → loginSuccess → ticket replay (auto-followed)
        r2 = req.post(
            r1.url,
            form={
                "username": sid,
                "password": pwd,
                "execution": m.group(1),
                "_eventId": "submit",
                "submit": "登录",
            },
            timeout=60_000,
        )
        if not r2.ok:
            raise EhallAuthError(f"CAS POST failed: HTTP {r2.status}")
        # 3) bootstrap the app page so EMAP JS establishes the session
        self._state.page.goto(self.CLE_INDEX, timeout=60_000, wait_until="domcontentloaded")
        self._wait_app_ready()

    def _wait_app_ready(self, attempts: int = 6) -> None:
        assert self._state is not None
        import time

        for _ in range(attempts):
            time.sleep(3)
            try:
                text = self._state.page.evaluate("document.body ? document.body.innerText : ''")
                if "预约" in text:
                    return
            except Exception:
                pass
        # Not fatal: callers retry their query once against a fresh load.

    # -- data plane -------------------------------------------------------

    def _request_headers(self) -> Dict[str, str]:
        return {"x-requested-with": "XMLHttpRequest", "accept": "application/json, */*"}

    def get_json(self, path_or_url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = self.ensure()
        url = path_or_url if path_or_url.startswith("http") else EHALL_BASE + path_or_url
        query = dict(params or {})
        resp = state.context.request.get(url, params=query, headers=self._request_headers(), timeout=60_000)
        if resp.status != 200:
            raise EhallError(f"GET {url.split('?')[0]} failed: HTTP {resp.status}")
        payload = resp.json()
        if not isinstance(payload, dict):
            raise EhallError(f"GET {url.split('?')[0]} returned non-object JSON")
        return payload

    def post_form(self, path_or_url: str, data: Dict[str, Any]) -> Dict[str, Any]:
        state = self.ensure()
        url = path_or_url if path_or_url.startswith("http") else EHALL_BASE + path_or_url
        resp = state.context.request.post(
            url,
            form={str(k): str(v) for k, v in data.items()},
            headers=self._request_headers(),
            timeout=60_000,
        )
        if resp.status != 200:
            raise EhallError(f"POST {url.split('?')[0]} failed: HTTP {resp.status}")
        payload = resp.json()
        if not isinstance(payload, dict):
            raise EhallError(f"POST {url.split('?')[0]} returned non-object JSON")
        return payload

    def rows(self, payload: Dict[str, Any], model: str) -> List[Dict[str, Any]]:
        """Extract the row list from an EMAP {datas:{<model>:{rows}}} envelope."""
        datas = payload.get("datas") or {}
        block = datas.get(model) or datas.get("pageAction") or {}
        return list(block.get("rows") or [])
