#!/usr/bin/env python3
"""
Diagnostica end-to-end: Firestore (token) + Garmin Connect + (opz.) HTTP locale.

Esegui SUL Raspberry Pi (stessa venv del servizio), con .env già configurato:

  cd ~/garmin-sync-server
  source venv/bin/activate
  python3 deploy/rpi/verify_garmin_pipeline.py --uid TUO_FIREBASE_UID

Opzionale: testa anche l'endpoint HTTP del servizio in ascolto:

  python3 deploy/rpi/verify_garmin_pipeline.py --uid TUO_FIREBASE_UID --http-test

Utile quando l'app Flutter va in timeout o mostra dati parziali: distingue
problemi token/Garmin da problemi rete verso il mini-server.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica token Firestore + API Garmin (+ HTTP sync-vitals)")
    parser.add_argument("--uid", type=str, default=os.getenv("GARMIN_TEST_UID", "").strip(), help="Firebase Auth uid (o env GARMIN_TEST_UID)")
    parser.add_argument("--repo", type=Path, default=_repo_root(), help="Directory clone garmin-sync-server")
    parser.add_argument("--env-file", type=Path, default=None, help="File .env (default: REPO/.env)")
    parser.add_argument(
        "--http-test",
        action="store_true",
        help="POST /garmin/sync-vitals su http://127.0.0.1:8080 (servizio attivo)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=os.getenv("GARMIN_VERIFY_HTTP_URL", "http://127.0.0.1:8080").rstrip("/"),
        help="Base URL per --http-test",
    )
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    env_path = args.env_file.expanduser().resolve() if args.env_file else repo / ".env"

    if not args.uid:
        print("[FAIL] Serve --uid oppure variabile d'ambiente GARMIN_TEST_UID (Firebase Auth uid).")
        return 1

    if not env_path.is_file():
        print(f"[FAIL] File .env assente: {env_path}")
        return 1

    os.chdir(repo)
    sys.path.insert(0, str(repo))

    from dotenv import load_dotenv

    load_dotenv(env_path, override=True)

    print("=== Verifica pipeline Garmin (Firestore → token → Garmin API) ===\n")
    print(f"uid: {args.uid[:12]}… (len={len(args.uid)})")

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        from firebase_credentials import certificate_from_environment
    except Exception as e:
        print(f"[FAIL] Import: {e}")
        return 1

    to = float(os.getenv("FIRESTORE_TIMEOUT_SEC", "120"))

    try:
        if not firebase_admin._apps:
            firebase_admin.initialize_app(certificate_from_environment())
        db = firestore.client()
    except Exception as e:
        print(f"[FAIL] Firebase init: {e}")
        return 1

    t0 = time.perf_counter()
    try:
        doc = db.collection("garmin_tokens").document(args.uid).get(timeout=to)
    except Exception as e:
        print(f"[FAIL] Lettura garmin_tokens/{args.uid}: {type(e).__name__}: {e}")
        return 1
    t_fire = time.perf_counter() - t0
    data = doc.to_dict() or {}
    token_b64 = (data.get("token_b64") or "").strip()
    if not token_b64:
        print(f"[FAIL] Nessun token_b64 in garmin_tokens (lettura Firestore in {t_fire:.2f}s). Collega Garmin dall'app.")
        return 1
    print(f"[OK] Token presente in Firestore (lettura {t_fire:.2f}s, token_b64 ~{len(token_b64)} char)")

    try:
        from garminconnect import Garmin, GarminConnectConnectionError, GarminConnectAuthenticationError
    except ImportError as e:
        print(f"[FAIL] garminconnect non installato: {e}")
        return 1

    print("\n--- Login Garmin con token salvato (nessuna password qui) ---")
    t1 = time.perf_counter()
    try:
        client = Garmin()
        client.login(tokenstore=token_b64)
    except (GarminConnectConnectionError, GarminConnectAuthenticationError, OSError) as e:
        print(f"[FAIL] Login/sessione Garmin: {type(e).__name__}: {e}")
        print("        → SSL/proxy: vedi RPI_DEPLOY.md (certificato self-signed).")
        print("        → Token scaduto: ricollega da FitAI.")
        return 1
    except Exception as e:
        print(f"[FAIL] Login Garmin: {type(e).__name__}: {e}")
        return 1
    t_login = time.perf_counter() - t1
    print(f"[OK] Sessione Garmin attiva (login token in {t_login:.2f}s)")

    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n--- Campioni API (data {today}) ---")
    for name, fn in (
        ("get_stats", lambda: client.get_stats(today)),
        ("get_activities(0,3)", lambda: client.get_activities(0, 3)),
    ):
        t_a = time.perf_counter()
        try:
            out = fn()
            dt = time.perf_counter() - t_a
            if isinstance(out, dict):
                keys = list(out.keys())[:8]
                extra = f"keys={keys!r}…" if keys else "dict vuoto"
            elif isinstance(out, list):
                extra = f"n={len(out)}"
            else:
                extra = type(out).__name__
            print(f"[OK] {name} in {dt:.2f}s ({extra})")
        except Exception as e:
            print(f"[WARN] {name}: {type(e).__name__}: {e}")

    if args.http_test:
        print(f"\n--- HTTP POST {args.base_url}/garmin/sync-vitals ---")
        body = json.dumps({"uid": args.uid}).encode("utf-8")
        req = urllib.request.Request(
            f"{args.base_url}/garmin/sync-vitals",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t_h = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                dt = time.perf_counter() - t_h
                print(f"HTTP {resp.status} in {dt:.2f}s")
                try:
                    j = json.loads(raw)
                    print(json.dumps(j, indent=2, ensure_ascii=False)[:2000])
                except json.JSONDecodeError:
                    print(raw[:800])
        except urllib.error.HTTPError as e:
            dt = time.perf_counter() - t_h
            err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            print(f"[FAIL] HTTP {e.code} dopo {dt:.2f}s: {err_body[:600]}")
            return 1
        except Exception as e:
            print(f"[FAIL] Richiesta HTTP: {type(e).__name__}: {e}")
            return 1

    print("\n=== Riepilogo: pipeline token + Garmin OK ===")
    print("Se qui è tutto OK ma l'app va in timeout, controlla URL raggiungibile dal telefono")
    print("e timeout client (FitAI usa ~180s per sync-vitals dopo l’ultimo aggiornamento).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
