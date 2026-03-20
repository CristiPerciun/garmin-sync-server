#!/usr/bin/env python3
"""
Verifica che le credenziali Firebase nel .env siano nel formato corretto e che Firestore risponda.

Esegui SUL Raspberry Pi (o stessa macchina del servizio), nella venv del progetto:

  cd ~/garmin-sync-server
  source venv/bin/activate
  python3 deploy/rpi/verify_firebase_credentials.py

Controlla:
  - presenza e decodifica di FIREBASE_CREDENTIALS_B64 o FIREBASE_CREDENTIALS
  - campi obbligatori nel JSON (type, project_id, private_key, client_email)
  - initialize_app + una singola lettura Firestore (get su doc inesistente), con timeout.
    Evitiamo list_collection_ids: su rete lenta (hotspot) può sembrare “appeso” per minuti.

Se vedi PermissionDenied qui, il problema non è Garmin ma IAM / progetto Firebase.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _firestore_timeout() -> float:
    try:
        return float(os.getenv("FIRESTORE_TIMEOUT_SEC", "120"))
    except ValueError:
        return 120.0


def _firestore_retry_deadline(rpc_timeout: float) -> float:
    """Il client Google usa un Retry con deadline default 60s: va allineato al timeout RPC + margine."""
    return max(180.0, rpc_timeout * 2.5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica FIREBASE_* nel .env e accesso Firestore")
    default_repo = Path.home() / "garmin-sync-server"
    parser.add_argument("--repo", type=Path, default=default_repo, help="Directory clone")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="File .env (default: REPO/.env)",
    )
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    env_path = args.env_file.expanduser().resolve() if args.env_file else repo / ".env"

    print("=== Verifica credenziali Firebase (formato + Firestore) ===\n")

    if not env_path.is_file():
        print(f"[FAIL] File .env assente: {env_path}")
        return 1

    os.chdir(repo)
    sys.path.insert(0, str(repo))

    from dotenv import load_dotenv

    # Come uvicorn+systemd: variabili già in env hanno priorità; per test forziamo il .env del repo
    load_dotenv(env_path, override=True)

    has_json = bool(os.getenv("FIREBASE_CREDENTIALS", "").strip())
    has_b64 = bool(os.getenv("FIREBASE_CREDENTIALS_B64", "").strip())
    print(f"Variabili: FIREBASE_CREDENTIALS={'sì' if has_json else 'no'}, FIREBASE_CREDENTIALS_B64={'sì' if has_b64 else 'no'}")

    if has_json and has_b64:
        print("[INFO] Entrambe impostate: ha precedenza FIREBASE_CREDENTIALS (come in firebase_credentials.py).")

    pid = "?"
    email = "?"

    try:
        import json

        import firebase_admin
        from firebase_admin import firestore
        from google.api_core import retry as api_retry

        import firebase_credentials as fc

        cert = fc.certificate_from_environment()
    except ValueError as e:
        print(f"[FAIL] Caricamento credenziali: {e}")
        return 1
    except Exception as e:
        print(f"[FAIL] Errore imprevisto durante caricamento: {type(e).__name__}: {e}")
        return 1

    try:
        if has_b64 and not has_json:
            meta = fc.decode_firebase_b64(os.environ["FIREBASE_CREDENTIALS_B64"])
        elif has_json:
            meta = json.loads(os.environ["FIREBASE_CREDENTIALS"].strip().lstrip("\ufeff"))
        else:
            meta = {}
        pid = meta.get("project_id", "?")
        email = meta.get("client_email", "?")
        print(f"[OK] JSON service account valido: project_id={pid!r}, client_email={email!r}")
    except Exception as e:
        print(f"[WARN] Impossibile rileggere metadati per display: {e}")

    verify_name = "verify_firebase_credentials_tmp"
    try:
        firebase_admin.delete_app(firebase_admin.get_app(verify_name))
    except ValueError:
        pass

    to = _firestore_timeout()
    rdeadline = _firestore_retry_deadline(to)
    retry_policy = api_retry.Retry(deadline=rdeadline)
    print(
        f"Round-trip Firestore: GET doc di test (timeout RPC {to:.0f}s, retry deadline {rdeadline:.0f}s). "
        "Su hotspot aspetta…"
    )
    try:
        firebase_admin.initialize_app(cert, name=verify_name)
        db = firestore.client(firebase_admin.get_app(verify_name))
        db.collection("garmin_tokens").document("__verify_connectivity__").get(
            timeout=to,
            retry=retry_policy,
        )
        print("[OK] Firestore ha risposto (lettura OK; IAM e rete OK per questo test).")
    except Exception as e:
        err_s = str(e).lower()
        print(f"[FAIL] Firestore: {type(e).__name__}: {e}")
        if "permission" in err_s or "403" in err_s:
            print(
                f"        → IAM: sul progetto {pid!r} assegna al service account ruoli Firestore "
                "(es. Cloud Datastore User). Vedi RPI_DEPLOY.md."
            )
        elif "deadline" in err_s or "504" in err_s or "timeout" in err_s or "retryerror" in err_s:
            print(
                "        → Rete: Pi→Google troppo lenta o instabile (hotspot). "
                "Prova Wi‑Fi cablato/router; sul .env aumenta FIRESTORE_TIMEOUT_SEC=240 e riprova."
            )
        else:
            print(f"        Progetto: {pid!r}")
        return 1
    finally:
        try:
            firebase_admin.delete_app(firebase_admin.get_app(verify_name))
        except ValueError:
            pass

    print("\n=== Riepilogo: credenziali OK e Firestore raggiungibile ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
