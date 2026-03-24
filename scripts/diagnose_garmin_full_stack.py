#!/usr/bin/env python3
"""
Diagnosi end-to-end: versioni vs requirements.txt, import senza crash, env (GARTH_HOME),
costruzione Garmin come /garmin/connect, login SSO opzionale.

Credenziali SSO (in ordine): GARMIN_TEST_EMAIL / GARMIN_TEST_PASSWORD (shell),
altrimenti GARMIN_EMAIL / GARMIN_PASSWORD da garmin-sync-server/.env dopo load_dotenv.
Non loggare mai password in file NDJSON.

Scrive NDJSON su ../FitAI Analyzer/debug-b685dc.log (sessione debug Cursor).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEBUG_LOG = os.path.abspath(
    os.path.join(ROOT, "..", "FitAI Analyzer", "debug-b685dc.log")
)
_REQ = os.path.join(ROOT, "requirements.txt")


# region agent log
def _agent_log(hid: str, loc: str, msg: str, data: dict | None = None) -> None:
    payload = {
        "sessionId": "b685dc",
        "timestamp": int(time.time() * 1000),
        "hypothesisId": hid,
        "location": loc,
        "message": msg,
        "data": data or {},
        "runId": os.environ.get("GARMIN_DEBUG_RUN_ID", "diagnose_stack"),
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


def _ver_tuple(s: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", s.split("+")[0].split("-")[0])
    return tuple(int(p) for p in parts) if parts else (0,)


def _read_req_mins() -> dict[str, str]:
    out: dict[str, str] = {}
    if not os.path.isfile(_REQ):
        return out
    with open(_REQ, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"(garminconnect|garth)\s*>=\s*([\d.]+)", line, re.I)
            if m:
                out[m.group(1).lower()] = m.group(2)
    return out


def _installed_version(dist: str) -> str | None:
    try:
        import importlib.metadata as md

        return md.version(dist)
    except Exception:
        return None


def main() -> int:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    print("=== 1) Python ===")
    py = sys.version.split()[0]
    print(f"  {sys.version}")
    _agent_log("H_PY", "diagnose:python", "interpreter", {"version": py})

    print("=== 2) Librerie vs requirements.txt ===")
    mins = _read_req_mins()
    gc_min = mins.get("garminconnect", "?")
    garth_min = mins.get("garth", "?")
    gc_v = _installed_version("garminconnect")
    garth_v = _installed_version("garth")
    gc_ok = gc_v and _ver_tuple(gc_v) >= _ver_tuple(gc_min) if gc_min != "?" else None
    garth_ok = garth_v and _ver_tuple(garth_v) >= _ver_tuple(garth_min) if garth_min != "?" else None
    print(f"  garminconnect: installata={gc_v!r} minimo_file={gc_min!r} ok={gc_ok}")
    print(f"  garth:         installata={garth_v!r} minimo_file={garth_min!r} ok={garth_ok}")
    _agent_log(
        "H_LIB",
        "diagnose:versions",
        "pypi_vs_requirements",
        {
            "garminconnect_installed": gc_v,
            "garminconnect_min_from_req": gc_min,
            "garminconnect_meets_min": gc_ok,
            "garth_installed": garth_v,
            "garth_min_from_req": garth_min,
            "garth_meets_min": garth_ok,
        },
    )

    print("=== 3) dotenv + garmin_env (procedura server) ===")
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
    gh_before = bool((os.environ.get("GARTH_HOME") or "").strip())
    from garmin_env import unset_garth_home_if_incomplete

    unset_garth_home_if_incomplete()
    gh_after = bool((os.environ.get("GARTH_HOME") or "").strip())
    print(f"  GARTH_HOME prima unset: {gh_before}, dopo: {gh_after}")
    _agent_log(
        "H_ENV",
        "diagnose:garmin_env",
        "garth_home_sanitized",
        {"garth_home_before": gh_before, "garth_home_after": gh_after},
    )

    print("=== 4) Import main.py (no uvicorn) ===")
    try:
        import main as main_mod  # noqa: F401

        print("  import main: OK")
        _agent_log("H_IMP", "diagnose:import_main", "ok", {})
    except Exception as e:
        print(f"  import main: CRASH {type(e).__name__}: {e}")
        _agent_log(
            "H_IMP",
            "diagnose:import_main",
            "crash",
            {"exc_type": type(e).__name__, "snippet": str(e)[:400]},
        )
        return 1

    print("=== 5) Import garminconnect + costruzione Garmin (dummy creds) ===")
    try:
        from garminconnect import Garmin

        g = Garmin("probe@example.com", "probe-password-not-used-for-network")
        print("  Garmin(...): OK (garth.Client inizializzato)")
        _agent_log("H_FLOW", "diagnose:garmin_construct", "ok", {})
    except Exception as e:
        print(f"  Garmin(...): CRASH {type(e).__name__}: {e}")
        _agent_log(
            "H_FLOW",
            "diagnose:garmin_construct",
            "crash",
            {"exc_type": type(e).__name__, "snippet": str(e)[:400]},
        )
        return 2

    print("=== 6) Procedura come POST /garmin/connect (pop GARMINTOKENS) + login reale ===")
    te = bool((os.environ.get("GARMIN_TEST_EMAIL") or "").strip())
    tp = bool(os.environ.get("GARMIN_TEST_PASSWORD"))
    ge = bool((os.environ.get("GARMIN_EMAIL") or "").strip())
    gp = bool(os.environ.get("GARMIN_PASSWORD"))
    _agent_log(
        "H_ENV",
        "diagnose:sso_env_probe",
        "credential_sources_present",
        {
            "has_garmin_test_email": te,
            "has_garmin_test_password": tp,
            "has_garmin_email_from_dotenv": ge,
            "has_garmin_password_from_dotenv": gp,
        },
    )

    email = (os.environ.get("GARMIN_TEST_EMAIL") or "").strip()
    pw_raw = os.environ.get("GARMIN_TEST_PASSWORD")
    creds_source = "GARMIN_TEST_*"
    if not email or pw_raw is None or str(pw_raw).strip() == "":
        email = (os.environ.get("GARMIN_EMAIL") or "").strip()
        pw_raw = os.environ.get("GARMIN_PASSWORD")
        creds_source = "GARMIN_EMAIL/GARMIN_PASSWORD(.env)"
    pw = str(pw_raw).strip() if pw_raw is not None else ""

    if not email or not pw:
        print(
            "  (saltato: nessuna credenziale — usa GARMIN_TEST_EMAIL/PASSWORD in shell "
            "oppure GARMIN_EMAIL/GARMIN_PASSWORD in garmin-sync-server/.env; nota: alcuni ambienti "
            "non passano variabili *PASSWORD* al processo Python.)"
        )
        _agent_log(
            "H_FLOW",
            "diagnose:sso",
            "skipped_no_env_creds",
            {"creds_source": "none"},
        )
        return 0

    if creds_source.startswith("GARMIN_EMAIL"):
        print("  (credenziali SSO da .env: GARMIN_EMAIL / GARMIN_PASSWORD)")

    def _looks_sso_429(msg: str) -> bool:
        low = (msg or "").lower()
        if "too many requests" in low or "rate limit" in low:
            return True
        return "429" in low and "client error" in low

    sso_rc = 0
    old = os.environ.pop("GARMINTOKENS", None)
    try:
        from garminconnect import (
            Garmin,
            GarminConnectAuthenticationError,
            GarminConnectConnectionError,
            GarminConnectTooManyRequestsError,
        )
        from garth.exc import GarthHTTPError

        client = Garmin(email, pw)
        client.login()
        tok = len(client.garth.dumps())
        print(f"  login: OK token_b64_len={tok}")
        _agent_log(
            "H_FLOW",
            "diagnose:sso",
            "login_ok",
            {"token_b64_len": tok, "email_host": email.split("@")[-1].lower()},
        )
    except GarminConnectTooManyRequestsError as e:
        print(f"  login: 429 (TooManyRequests) {e!s}"[:500])
        _agent_log(
            "H_GARMIN",
            "diagnose:sso",
            "rate_limit_library",
            {"snippet": str(e)[:300]},
        )
        sso_rc = 3
    except GarminConnectConnectionError as e:
        low = str(e).lower()
        is_429 = "429" in low and "too many" in low
        print(f"  login: ConnectionError (429_text={is_429}) {e!s}"[:500])
        _agent_log(
            "H_GARMIN",
            "diagnose:sso",
            "connection_error",
            {"looks_like_429": is_429, "snippet": str(e)[:300]},
        )
        sso_rc = 3 if is_429 else 5
    except GarminConnectAuthenticationError as e:
        print(f"  login: auth {e!s}"[:500])
        _agent_log(
            "H_GARMIN",
            "diagnose:sso",
            "auth_error",
            {"snippet": str(e)[:300]},
        )
        sso_rc = 4
    except GarthHTTPError as e:
        resp = getattr(getattr(e, "error", None), "response", None)
        st = getattr(resp, "status_code", None)
        detail = str(e)
        print(f"  login: GarthHTTPError status={st} {e!s}"[:500])
        _agent_log(
            "H_GARMIN",
            "diagnose:sso",
            "garth_http",
            {"http_status": st, "snippet": detail[:300]},
        )
        sso_rc = 3 if (st == 429 or _looks_sso_429(detail)) else 5
    except Exception as e:
        print(f"  login: {type(e).__name__} {e!s}"[:500])
        _agent_log(
            "H_FLOW",
            "diagnose:sso",
            "unexpected",
            {"exc_type": type(e).__name__, "snippet": str(e)[:300]},
        )
        sso_rc = 6
    finally:
        if old is not None:
            os.environ["GARMINTOKENS"] = old

    _agent_log(
        "H_FLOW",
        "diagnose:exit",
        "sso_exit_code",
        {"sso_rc": sso_rc},
    )
    return sso_rc


if __name__ == "__main__":
    raise SystemExit(main())
