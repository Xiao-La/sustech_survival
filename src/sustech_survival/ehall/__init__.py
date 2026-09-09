"""sustech_survival.ehall — eHall (网上办事大厅) services.

eHall apps are CAS-gated and require a browser-established amp session
(server-side constraint: pure HTTP gets 403 on app APIs even after a
valid CAS ticket dance). The session in ``_session`` therefore uses a
headless Chromium (Playwright) to complete the login once, then all
data-plane calls run through the browser context's request API.

Submodules (nesting per iron law #6/#30):
- ``cle`` — Center for Language Education (语言中心语言指导服务)
- ``leave`` — student leave requests (请假)  [endpoint capture pending]
"""
from . import cle  # noqa: F401
