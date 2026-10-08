"""Electron backend bootstrap: private stdin credentials, in-memory SSO only."""
from __future__ import annotations

import argparse
import json
import sys
from typing import TextIO

from sustech_survival.sso import AuthorizerError, cred_clear, cred_set
from sustech_survival.webui.app import DEFAULT_PORT, run


def main(argv: list[str] | None = None, stream: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--skin", default=None)
    args = parser.parse_args(argv)
    try:
        credentials = json.load(stream if stream is not None else sys.stdin)
        if not isinstance(credentials, dict):
            raise ValueError("Expected credentials object")
        if credentials:
            sid, password = credentials.get("sid"), credentials.get("password")
            if not isinstance(sid, str) or not isinstance(password, str):
                raise ValueError("Expected credential strings")
            cred_set(sid, password)
    except (ValueError, TypeError, AuthorizerError):
        print("Desktop credentials could not be loaded; reopen Settings.", file=sys.stderr)
        return 2
    try:
        return run(port=args.port, host="127.0.0.1", skin=args.skin)
    finally:
        cred_clear()


if __name__ == "__main__":
    raise SystemExit(main())
