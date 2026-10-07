# Web UI

Unified Flask web UI — the active skin serves the full TIS course selector (5-step: search → pick → conflict-free schedule → compare → bid & sync), the campus transit map, and NCES evals on a single port. Two skins ship with the package: `default` (English) and `default_zh` (Chinese).

**Extras:** `[webui]` extra installs Flask.

---

## CLI

```bash
sustech webui serve                    # start on default port 20129
sustech webui serve -p 8080 -H 0.0.0.0 # custom port/host
sustech webui serve --skin default_zh   # serve the Chinese skin
sustech webui skin set my-skin           # persist the default skin
sustech webui skin delete my-skin        # delete a user-installed skin

sustech webui open                     # open in default browser
```

If the port is already in use (typically another `sustech webui serve`
still running), `serve` exits with a clear message and exit code 1 instead
of a raw bind-error traceback.

---

## Python API

```python
from sustech_survival.webui.app import create_app, run

app = create_app()           # Flask app for WSGI servers
run(port=20129, host="127.0.0.1", debug=False)
```

### Routes

| Route | Description |
|------|-------------|
| `/` | Landing page |
| `/tis` | TIS course selector + schedule grid |
| `/transit` | Campus map + bus navigation |
| `/api/tis/ical` | iCal export of picked schedule |
| `/api/nces/*` | NCES evaluation data endpoints |
| `/api/transit/*` | Transit data endpoints |

The Web UI binds to `127.0.0.1` by default for use on the same computer. Pass `--host 0.0.0.0` explicitly to share it on a trusted network. The transit proxy serves only PMTiles and glyph files from the SUSTech map-mirror directory; upstream redirects are reported as an error.
