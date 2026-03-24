#!/usr/bin/env python3
"""
Test login Garmin SSO (stesso flusso di POST /garmin/connect, senza Firestore).

Uso (PowerShell):
  cd garmin-sync-server
  $env:GARMIN_TEST_EMAIL = "tuo@email.com"
  $env:GARMIN_TEST_PASSWORD = "tua_password"
  python scripts/test_garmin_sso_login.py

Opzionale: $env:GARMIN_TEST_SKIP_FIRESTORE = "1" (default; non usa Firebase).

Stesso flusso SSO di POST /garmin/connect (senza passare dall’API HTTP).
Utile per diagnosticare 401/429 lato Garmin.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

# Root repo (parent di scripts/)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Log debug session (workspace FitAI Analyzer, path fisso richiesto da Cursor debug mode)
_DEBUG_LOG = os.path.abspath(
    os.path.join(ROOT, "..", "FitAI Analyzer", "debug-b685dc.log")
)


# region agent log
def _agent_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict | None = None,
) -> None:
    payload = {
        "sessionId": "b685dc",
        "timestamp": int(time.time() * 1000),
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "runId": os.environ.get("GARMIN_DEBUG_RUN_ID", "run1"),
    }
    try:
        parent = os.path.dirname(_DEBUG_LOG)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(_DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


# endregion
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from garmin_env import unset_garth_home_if_incomplete

unset_garth_home_if_incomplete()

_agent_log(
    "H2",
    "test_garmin_sso_login.py:after_unset_garth",
    "post_load_dotenv_env_snapshot",
    {
        "garth_home_set": bool((os.environ.get("GARTH_HOME") or "").strip()),
        "garmintokens_set": "GARMINTOKENS" in os.environ,
        "cwd": os.getcwd(),
    },
)


def main() -> int:
    email = (os.environ.get("cristi.perciun@gmail.com") or "").strip()
    password = os.environ.get("b#Vt25+Ns'ZE#%g")
    if password is not None:
        password = str(password)
    if not email or not password:
        _agent_log(
            "H2",
            "test_garmin_sso_login.py:main",
            "missing_env_credentials",
            {"has_email": bool(email), "has_password": bool(password)},
        )
        print(
            "Imposta GARMIN_TEST_EMAIL e GARMIN_TEST_PASSWORD nell'ambiente.\n"
            "Esempio PowerShell:\n"
            '  $env:GARMIN_TEST_EMAIL = "user@example.com"\n'
            '  $env:GARMIN_TEST_PASSWORD = "..."\n'
            "  python scripts/test_garmin_sso_login.py",
            file=sys.stderr,
        )
        return 2

    # Come connect_garmin: non usare token da path/env per il primo collegamento
    old_tokens = os.environ.pop("GARMINTOKENS", None)
    try:
        from garminconnect import (
            Garmin,
            GarminConnectAuthenticationError,
            GarminConnectConnectionError,
            GarminConnectTooManyRequestsError,
        )
        from garth.exc import GarthHTTPError
    except ImportError as e:
        _agent_log("H3", "test_garmin_sso_login.py:import", "import_failed", {"error": str(e)[:200]})
        print(f"Import garminconnect fallito: {e}", file=sys.stderr)
        if old_tokens is not None:
            os.environ["GARMINTOKENS"] = old_tokens
        return 1

    try:
        import importlib.metadata as _imd

        _gc_ver = _imd.version("garminconnect")
        _garth_ver = _imd.version("garth")
    except Exception as _meta_e:
        _gc_ver = _garth_ver = f"meta_err:{_meta_e!s}"[:80]
    _agent_log(
        "H3",
        "test_garmin_sso_login.py:versions",
        "package_versions",
        {"garminconnect": _gc_ver, "garth": _garth_ver},
    )

    def _looks_sso_429(msg: str) -> bool:
        low = (msg or "").lower()
        if "too many requests" in low or "rate limit" in low:
            return True
        return "429" in low and "client error" in low

    def _agent_log_http_chain(root: BaseException, tag: str) -> None:
        """Estrae status/retry-after dalla catena __cause__ (no body/PII)."""
        chain: list[dict] = []
        cur: BaseException | None = root
        seen: set[int] = set()
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            resp = getattr(cur, "response", None)
            if resp is None and hasattr(cur, "error"):
                resp = getattr(getattr(cur, "error", None), "response", None)
            st = getattr(resp, "status_code", None) if resp is not None else None
            ra = None
            if resp is not None:
                try:
                    ra = resp.headers.get("Retry-After")
                except Exception:
                    ra = None
            chain.append({"exc_type": type(cur).__name__, "http_status": st, "retry_after": ra})
            cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
        _agent_log("H1", "test_garmin_sso_login.py:http_chain", tag, {"chain": chain})

    lock = threading.Lock()
    print(f"garminconnect + garth SSO: email={email!r} host={email.split('@')[-1]!r}")
    _agent_log(
        "H2",
        "test_garmin_sso_login.py:before_garmin",
        "about_to_construct_garmin",
        {
            "email_host": email.split("@")[-1].lower() if "@" in email else "?",
            "garmintokens_popped": old_tokens is not None,
            "garth_home_set": bool((os.environ.get("GARTH_HOME") or "").strip()),
        },
    )
    try:
        with lock:
            client = Garmin(email, password)
            _agent_log(
                "H2",
                "test_garmin_sso_login.py:after_construct",
                "garmin_construct_ok",
                {},
            )
            client.login()
    except GarminConnectTooManyRequestsError as e:
        _agent_log(
            "H1",
            "test_garmin_sso_login.py:exc",
            "too_many_requests_exc",
            {"exc_type": type(e).__name__, "snippet": str(e)[:300]},
        )
        print("RISULTATO: 429 rate limit Garmin SSO (troppi tentativi). Attendi 15-60 min.")
        print(str(e)[:800])
        return 3
    except GarminConnectAuthenticationError as e:
        _agent_log(
            "H5",
            "test_garmin_sso_login.py:exc",
            "auth_error",
            {"exc_type": type(e).__name__, "snippet": str(e)[:300]},
        )
        print("RISULTATO: autenticazione rifiutata (password/MFA/profilo).")
        print(str(e)[:800])
        return 4
    except GarminConnectConnectionError as e:
        detail = str(e)
        if _looks_sso_429(detail):
            _agent_log_http_chain(e, "connection_429_chain")
            _agent_log(
                "H1",
                "test_garmin_sso_login.py:exc",
                "connection_error_429_text",
                {"snippet": detail[:300]},
            )
            print("RISULTATO: 429 rate limit Garmin SSO (come ConnectionError). Attendi 15-60 min.")
            print(detail[:800])
            return 3
        _agent_log(
            "H4",
            "test_garmin_sso_login.py:exc",
            "connection_error_non429",
            {"snippet": detail[:300]},
        )
        print("RISULTATO: errore connessione / HTTP durante login.")
        print(detail[:800])
        return 5
    except GarthHTTPError as e:
        detail = str(e)
        resp = getattr(getattr(e, "error", None), "response", None)
        st = getattr(resp, "status_code", None)
        retry_after = None
        if resp is not None:
            try:
                retry_after = resp.headers.get("Retry-After")
            except Exception:
                retry_after = None
        _agent_log(
            "H1" if (st == 429 or _looks_sso_429(detail)) else "H4",
            "test_garmin_sso_login.py:exc",
            "garth_http",
            {
                "http_status": st,
                "retry_after": retry_after,
                "snippet": detail[:300],
            },
        )
        if st == 429 or _looks_sso_429(detail):
            print("RISULTATO: 429 rate limit Garmin SSO. Attendi 15-60 min.")
            print(detail[:800])
            return 3
        print("RISULTATO: GarthHTTPError durante login.")
        print(detail[:800])
        return 5
    except Exception as e:
        _agent_log(
            "H4",
            "test_garmin_sso_login.py:exc",
            "unexpected_exception",
            {"exc_type": type(e).__name__, "snippet": str(e)[:300]},
        )
        print(f"RISULTATO: {type(e).__name__}: {e}"[:800])
        return 6
    finally:
        if old_tokens is not None:
            os.environ["GARMINTOKENS"] = old_tokens

    token_b64 = client.garth.dumps()
    _agent_log(
        "H0",
        "test_garmin_sso_login.py:success",
        "login_ok",
        {
            "display_name_len": len(client.display_name or ""),
            "token_b64_len": len(token_b64),
        },
    )
    print("RISULTATO: login OK")
    print(f"  displayName={client.display_name!r} fullName={client.full_name!r}")
    print(f"  token_b64 length={len(token_b64)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
