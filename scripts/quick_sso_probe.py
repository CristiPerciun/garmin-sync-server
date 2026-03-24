#!/usr/bin/env python3
"""
Prova di rete verso sso.garmin.com senza credenziali (solo GET/HEAD).
Utile per vedere se il Pi riceve 200/302/403/429 prima ancora del login libreria.

Uso sul Raspberry:
  python3 scripts/quick_sso_probe.py
"""
from __future__ import annotations

import ssl
import sys
import urllib.error
import urllib.request

URL = "https://sso.garmin.com/sso/signin"


def main() -> int:
    req = urllib.request.Request(
        URL,
        method="GET",
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FitAI-GarminProbe/1.0)",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        },
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            print(f"OK status={r.status} final_url={r.geturl()}")
            return 0
    except urllib.error.HTTPError as e:
        print(f"HTTPError code={e.code} reason={e.reason!r} url={e.url}")
        return 0 if e.code in (200, 301, 302, 303, 307, 308) else 1
    except OSError as e:
        print(f"Network/SSL error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
