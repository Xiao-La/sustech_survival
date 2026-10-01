"""Inject an in-memory RSC authorizer session into a browser context."""
import sys

from ... import _net

def load_rsc_session(cookie_path: str = None) -> list:
    """Return authorizer cookies for Playwright without reading session files."""
    if cookie_path is not None:
        raise ValueError("Disk-backed RSC sessions are no longer supported")

    try:
        from sustech_survival.sso.authlib.rsc import RSCAuthorizer
        auth = RSCAuthorizer()
        if auth._session_cache:
            cookies = []
            for name, val in auth._session_cache.items():
                if isinstance(val, dict):
                    cookies.append({"name": name, **val})
                else:
                    cookies.append({"name": name, "value": val})
            return cookies
        if getattr(auth, "page", None) is not None:
            return auth.page.context.cookies()
    except ImportError:
        return []
    return []

def inject_into_context(ctx, cookies: list):
    """Inject cookies into a Playwright browser context."""
    ctx.add_cookies(cookies)

def test_with_playwright(cookie_path: str = None) -> bool:
    """Test: load cookies, inject into fresh Playwright browser, verify RSC is authenticated."""
    from playwright.sync_api import sync_playwright

    cookies = load_rsc_session(cookie_path)
    print(f"Loaded {len(cookies)} RSC cookies", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies(cookies)

        page = ctx.new_page()
        page.goto("https://pubs.rsc.org/", timeout=_net.page_timeout_ms("rsc"), wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        url = page.url
        title = page.title()
        body_text = page.inner_text("body")

        print(f"URL: {url}", flush=True)
        print(f"Title: {title}", flush=True)

        if "Log in or register" in body_text:
            print("❌ NOT logged in — cookie injection failed", flush=True)
            browser.close()
            return False
        else:
            print("✅ Logged in — cookie injection works!", flush=True)

            # Test search
            page.goto(
                "https://pubs.rsc.org/en/search?q=machine+learning+catalysis",
                timeout=_net.page_timeout_ms("rsc"),
                wait_until="networkidle"
            )
            print(f"Search URL: {page.url}", flush=True)

            # Extract article links
            links = page.locator("a[href*='/en/content/articlehtml/']").all()
            print(f"Article links: {len(links)}", flush=True)
            for link in links[:5]:
                try:
                    href = link.get_attribute("href")
                    text = link.inner_text()[:80].strip()
                    print(f"  {text} -> {href}", flush=True)
                except:
                    pass

            browser.close()
            return True


if __name__ == "__main__":
    success = test_with_playwright()
    sys.exit(0 if success else 1)