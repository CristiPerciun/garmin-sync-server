import json
import os
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore

try:
    from google.cloud.firestore_v1.base_query import FieldFilter
except ImportError:
    FieldFilter = None  # type: ignore[misc, assignment]
try:
    from garminconnect import (
        Garmin,
        GarminConnectConnectionError,
        GarminConnectAuthenticationError,
    )
except ImportError:
    from garminconnect import Garmin, GarminConnectConnectionError
    GarminConnectAuthenticationError = GarminConnectConnectionError  # fallback

try:
    from garminconnect import GarminConnectTooManyRequestsError
except ImportError:

    class GarminConnectTooManyRequestsError(Exception):
        """Fallback se garminconnect è vecchio e non espone questa eccezione."""

        pass
import garth
try:
    from garth.exc import GarthException, GarthHTTPError
except ImportError:
    class _DummyGarthException(Exception):
        pass
    GarthException = _DummyGarthException
    GarthHTTPError = _DummyGarthException
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Annotated

import strava_sync

load_dotenv()

# Incrementa manualmente a ogni push che vuoi tracciare sul Pi (GET / → campo `version`).
SERVER_VERSION = "1.0.1"

# Firestore client; valorizzato in lifespan (evita crash all'import se manca .env → systemd può avviare uvicorn)
db = None

scheduler = BackgroundScheduler()


def _run_scheduled_sync():
    """Esegue sync per tutti gli utenti garmin_linked. Usato da scheduler e endpoint."""
    if db is None:
        return
    try:
        scheduled_sync()
    except Exception as e:
        logger.error(f"Scheduled sync fallito: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Carica Firebase + scheduler se .env ok; altrimenti API resta su (health + /docs)."""
    global db
    try:
        cred = _load_firebase_cred()
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("Firebase / Firestore inizializzati")
    except Exception as e:
        logger.error(f"Firebase non disponibile: {e}")
        db = None

    if db is not None:
        scheduler.add_job(_run_scheduled_sync, "interval", minutes=45, id="garmin_sync")
        scheduler.start()
        logger.info("⏰ Scheduler avviato: sync ogni 45 min per utenti garmin_linked")

        def run_after_delay():
            time.sleep(120)
            _run_scheduled_sync()

        threading.Thread(target=run_after_delay, daemon=True).start()
        logger.info("📅 Prima sync programmata tra 2 minuti")
    else:
        logger.warning("Modalità ridotta: nessuno scheduler senza credenziali Firebase in .env")

    yield
    try:
        if getattr(scheduler, "running", False):
            scheduler.shutdown(wait=False)
    except Exception:
        pass


app = FastAPI(title="Garmin Sync - FitAI Analyzer", lifespan=lifespan)

# CORS: permette richieste da FitAI Analyzer (web/mobile)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# === FIREBASE (secret - mai nel Docker image) ===
# Logica condivisa e validazione in firebase_credentials.py (B64 tollera spazi/newline; vedi verify script).
def _load_firebase_cred():
    from firebase_credentials import certificate_from_environment

    try:
        return certificate_from_environment()
    except ValueError as e:
        logger.error(f"Credenziali Firebase da .env: {e}")
        raise ValueError(
            "Configura FIREBASE_CREDENTIALS_B64 (consigliato sul Pi) o FIREBASE_CREDENTIALS. "
            "Verifica formato: sul Pi esegui python3 deploy/rpi/verify_firebase_credentials.py"
        ) from e

def _require_db():
    if db is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Firestore non configurato. Sul Pi: ~/garmin-sync-server/.env con FIREBASE_CREDENTIALS_B64 "
                "poi sudo systemctl restart garmin-sync (vedi RPI_DEPLOY.md)."
            ),
        )


logger.add(os.path.join(BASE_DIR, "garmin.log"), rotation="10 MB", level="INFO")

LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)


def _garmin_comms_filter(record: dict) -> bool:
    """Solo righe con bind(garmin_comms=True) → file dedicato."""
    return record["extra"].get("garmin_comms") is True


logger.add(
    os.path.join(LOGS_DIR, "garmin_comms.log"),
    rotation="00:00",
    retention="1 day",
    level="DEBUG",
    encoding="utf-8",
    filter=_garmin_comms_filter,
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message}",
)


def _garmin_error_excerpt(exc: BaseException) -> str:
    """Dettaglio per log diagnostico (no password/email)."""
    parts = [type(exc).__name__, str(exc)[:500]]
    resp = getattr(exc, "response", None)
    if resp is not None:
        sc = getattr(resp, "status_code", None)
        if sc is not None:
            parts.append(f"http_status={sc}")
        text = getattr(resp, "text", None) or ""
        if text:
            parts.append(f"body[:300]={text[:300]!r}")
    return " | ".join(parts)


def _log_garmin_comms(event: str, uid: str, exc: BaseException | None = None, extra: str = "") -> None:
    """Traccia errori/risposte anomale verso Garmin Connect; file conservato ~1 giorno (loguru retention)."""
    uid_short = (uid[:8] + "…") if len(uid) > 8 else uid
    msg = f"{event} uid={uid_short}"
    if exc is not None:
        msg += f" | {_garmin_error_excerpt(exc)}"
    if extra:
        msg += f" | {extra}"
    logger.bind(garmin_comms=True).warning(msg)


def _truncate_http_detail(msg: str, max_len: int = 1600) -> str:
    msg = (msg or "").strip()
    if len(msg) > max_len:
        return msg[: max_len - 1] + "…"
    return msg


def _http_exception_for_garmin_auth_error(e: BaseException) -> HTTPException:
    """
    GarminConnectAuthenticationError non significa sempre 'password sbagliata'.
    La libreria cyberjunky/python-garminconnect distingue SSO/oauth, profilo, ecc.
    """
    msg = _truncate_http_detail(str(e))
    low = msg.lower()
    if "preauthorized" in low or "oauth-service" in low or "sso token exchange" in low:
        return HTTPException(
            status_code=503,
            detail=msg,
        )
    if (
        "failed to retrieve profile" in low
        or "failed to retrieve user settings" in low
        or "invalid profile data" in low
        or "invalid user settings" in low
    ):
        return HTTPException(status_code=503, detail=msg)
    return HTTPException(
        status_code=401,
        detail=msg
        or "Autenticazione Garmin non riuscita. Controlla email e password, o account con MFA (vedi README).",
    )


def _walk_exception_chain(root: BaseException):
    """Root, __cause__ e __context__ (senza duplicati) — utile se Firestore è wrappata."""
    seen: set[int] = set()
    stack: list[BaseException | None] = [root]
    while stack:
        exc = stack.pop()
        if exc is None or id(exc) in seen:
            continue
        seen.add(id(exc))
        yield exc
        c = getattr(exc, "__cause__", None)
        x = getattr(exc, "__context__", None)
        if c is not None:
            stack.append(c)
        if x is not None and x is not c:
            stack.append(x)


def _http_exception_if_firestore_error(e: BaseException) -> HTTPException | None:
    """
    Errori Google/Firestore durante salvataggio token o users/{uid} — non sono fallimenti password Garmin.
    """
    try:
        from google.api_core.exceptions import DeadlineExceeded, PermissionDenied
    except ImportError:
        PermissionDenied = None  # type: ignore[misc, assignment]
        DeadlineExceeded = None  # type: ignore[misc, assignment]

    parts = list(_walk_exception_chain(e))
    for part in parts:
        if PermissionDenied is not None and isinstance(part, PermissionDenied):
            return HTTPException(
                status_code=503,
                detail=(
                    "Firestore ha rifiutato l'operazione (403 permessi). "
                    "Il login Garmin può essere andato a buon fine, ma il service account sul Pi non può scrivere su Firebase. "
                    "Verifica: stesso progetto Firebase dell'app; in Google Cloud → IAM assegna al service account del JSON "
                    "(FIREBASE_CREDENTIALS_B64) un ruolo con accesso a Firestore, es. «Cloud Datastore User» o «Editor». "
                    f"Dettaglio: {_truncate_http_detail(str(part), 500)}"
                ),
            )
        if DeadlineExceeded is not None and isinstance(part, DeadlineExceeded):
            return HTTPException(
                status_code=503,
                detail=(
                    "Firestore: timeout (504). Rete lenta dal Pi verso Google o servizio sovraccarico — riprova. "
                    f"Dettaglio: {_truncate_http_detail(str(part), 400)}"
                ),
            )

    combined = " ".join(str(p) for p in parts).lower()
    tnames = " ".join(type(p).__name__.lower() for p in parts)
    if "permissiondenied" in tnames or "missing or insufficient permissions" in combined:
        return HTTPException(
            status_code=503,
            detail=(
                "Google/Firestore: permessi insufficienti (403). Aggiorna IAM del service account sul progetto Firebase. "
                + _truncate_http_detail(str(e), 600)
            ),
        )
    if "deadlineexceeded" in tnames or "deadline exceeded" in combined:
        return HTTPException(
            status_code=503,
            detail=(
                "Google/Firestore: richiesta scaduta. Controlla Internet del Pi e riprova. "
                + _truncate_http_detail(str(e), 400)
            ),
        )
    return None


# === HELPER: token Garmin su Firestore (collection garmin_tokens/{uid}, campo token_b64) ===
# Collection separata per evitare che il client legga i token (regole Firestore negano accesso).
# Validita token Garmin: ~1 anno. Se non valido, viene rimosso e l'utente deve ricollegare.
GARMIN_TOKENS_COLLECTION = "garmin_tokens"


def _firestore_timeout_sec() -> float:
    """Timeout singole RPC Firestore (get/set/…). Hotspot/rete lenta: default 120s."""
    try:
        return float(os.getenv("FIRESTORE_TIMEOUT_SEC", "120"))
    except ValueError:
        return 120.0


def _get_garmin_token_from_firestore(uid: str) -> str | None:
    """Legge token Garmin (Base64) da Firestore. Ritorna None se assente."""
    doc = db.collection(GARMIN_TOKENS_COLLECTION).document(uid).get(
        timeout=_firestore_timeout_sec(),
    )
    data = doc.to_dict() or {}
    token = data.get("token_b64")
    return str(token).strip() if token else None

def _save_garmin_token_to_firestore(uid: str, token_b64: str) -> None:
    """Salva token Garmin (Base64) su Firestore. Validita ~1 anno; se scaduto, va rimosso e utente ricollega."""
    db.collection(GARMIN_TOKENS_COLLECTION).document(uid).set(
        {"token_b64": token_b64, "updated_at": datetime.utcnow().isoformat()},
        merge=True,
        timeout=_firestore_timeout_sec(),
    )

def _delete_garmin_token_from_firestore(uid: str) -> None:
    """Rimuove token Garmin da Firestore (disconnect)."""
    db.collection(GARMIN_TOKENS_COLLECTION).document(uid).delete(
        timeout=_firestore_timeout_sec(),
    )


STRAVA_TOKENS_COLLECTION = "strava_tokens"


def _set_backfill_status(
    uid: str,
    status: str,
    *,
    progress: float | None = None,
    message: str | None = None,
    source: str | None = None,
) -> None:
    data: dict = {"status": status, "updatedAt": firestore.SERVER_TIMESTAMP}
    if progress is not None:
        data["progress"] = progress
    if message:
        data["message"] = message
    if source:
        data["source"] = source
    db.collection("users").document(uid).collection("sync_status").document("backfill").set(
        data,
        merge=True,
        timeout=_firestore_timeout_sec(),
    )


def _set_last_successful_sync(uid: str) -> None:
    db.collection("users").document(uid).set(
        {"lastSuccessfulSync": firestore.SERVER_TIMESTAMP},
        merge=True,
        timeout=_firestore_timeout_sec(),
    )


def _save_strava_tokens_to_firestore(
    uid: str, access: str, refresh: str, expires_at: datetime
) -> None:
    db.collection(STRAVA_TOKENS_COLLECTION).document(uid).set(
        {
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": expires_at,
            "updated_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
        timeout=_firestore_timeout_sec(),
    )


def _get_strava_tokens_from_firestore(uid: str) -> dict | None:
    doc = db.collection(STRAVA_TOKENS_COLLECTION).document(uid).get(
        timeout=_firestore_timeout_sec(),
    )
    return doc.to_dict() if doc.exists else None


def _delete_strava_tokens_from_firestore(uid: str) -> None:
    db.collection(STRAVA_TOKENS_COLLECTION).document(uid).delete(
        timeout=_firestore_timeout_sec(),
    )


def _strava_client_configured() -> bool:
    cid = (os.getenv("STRAVA_CLIENT_ID") or "").strip()
    sec = (os.getenv("STRAVA_CLIENT_SECRET") or "").strip()
    return bool(cid and sec)


def _ensure_strava_access_token(uid: str) -> str | None:
    if not _strava_client_configured():
        return None
    doc = _get_strava_tokens_from_firestore(uid)
    if not doc:
        return None
    access = doc.get("access_token")
    refresh = doc.get("refresh_token")
    if not access or not refresh:
        return None
    exp = strava_sync.parse_strava_expires_at(doc.get("expires_at"))
    now = datetime.now(timezone.utc)
    if exp and now < exp - timedelta(minutes=5):
        return str(access)
    cid = os.getenv("STRAVA_CLIENT_ID", "").strip()
    sec = os.getenv("STRAVA_CLIENT_SECRET", "").strip()
    try:
        data = strava_sync.strava_refresh_access_token(cid, sec, str(refresh))
        new_a = data["access_token"]
        new_r = data.get("refresh_token", refresh)
        exp_in = int(data.get("expires_in", 3600))
        new_exp = now + timedelta(seconds=exp_in)
        _save_strava_tokens_to_firestore(uid, new_a, str(new_r), new_exp)
        return new_a
    except Exception as e:
        logger.warning(f"Strava refresh fallito {uid[:8]}…: {e}")
        return None


def verify_optional_bearer(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Se GARMIN_SERVER_BEARER_TOKEN è impostato, richiede Authorization: Bearer …"""
    if request.url.path.startswith("/internal/"):
        return
    expected = (os.getenv("GARMIN_SERVER_BEARER_TOKEN") or "").strip()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if authorization[7:].strip() != expected:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


# === MODELLO PER IL LOGIN DALL'APP ===
class GarminConnectRequest(BaseModel):
    uid: str
    email: str
    password: str

class GarminSyncRequest(BaseModel):
    uid: str


class DeltaSyncRequest(BaseModel):
    uid: str
    lastSuccessfulSync: int | float | str | dict | None = None
    sources: list[str] = ["garmin", "strava"]


class StravaRegisterRequest(BaseModel):
    uid: str
    access_token: str
    refresh_token: str
    expires_at: int | float | None = None


class ActivityDetailRequest(BaseModel):
    uid: str
    garmin_activity_id: str | int | None = None
    strava_activity_id: int | None = None


# === HEALTH CHECK ===
@app.get("/")
def health():
    return {
        "status": "ok",
        "service": "garmin-sync-server",
        "firestore": db is not None,
        "version": SERVER_VERSION,
    }

def _extract_activities_list(raw) -> list:
    """get_activities puo' restituire list o dict con chiave activities/activityList."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        out = raw.get("activities") or raw.get("activityList") or []
        if not out and raw:
            logger.debug(f"get_activities ha restituito dict con chiavi: {list(raw.keys())}")
        return out if isinstance(out, list) else []
    if raw is not None:
        logger.warning(f"get_activities tipo inatteso: {type(raw)}")
    return []


def _store_sync_status(
    uid: str,
    *,
    success: bool,
    message: str | None = None,
    activities_synced: int = 0,
    health_days_synced: int = 0,
):
    db.collection("users").document(uid).set(
        {
            "garmin_last_sync_status": "ok" if success else "error",
            "garmin_last_sync_at": datetime.utcnow().isoformat(),
            "garmin_last_sync_error": None if success else (message or "Errore sconosciuto"),
            "garmin_last_activities_synced": activities_synced,
            "garmin_last_health_days_synced": health_days_synced,
        },
        merge=True,
        timeout=_firestore_timeout_sec(),
    )


def _sync_vitals_for_client(
    client: Garmin,
    uid: str,
    *,
    num_days: int = 2,
    activities_limit: int = 50,
):
    """Sync leggera usata al login e nel pull-to-refresh. Chiama API Garmin Connect."""
    logger.debug(f"sync_vitals: chiamata get_stats/get_sleep_data per {num_days} giorni...")
    health_days, health_writes = _sync_daily_health(client, uid, num_days=num_days)
    logger.debug(f"sync_vitals: chiamata get_activities(0, {activities_limit})...")
    raw_activities = client.get_activities(0, activities_limit)
    activities = _extract_activities_list(raw_activities)
    by_date: dict[str, list[dict]] = {}

    for act in activities:
        act_id = act.get("activityId") or act.get("activityID")
        if not act_id:
            continue
        start_raw = act.get("startTimeGMT") or act.get("startTime") or act.get("startTimeLocal") or ""
        dt = _parse_datetime(start_raw) or datetime.utcnow()
        date_key = _date_key(dt)
        by_date.setdefault(date_key, []).append(act)

    activity_writes = 0
    for date_key, garmin_acts in by_date.items():
        existing_docs = _load_existing_activities_for_date(uid, date_key)
        date_changed = False
        for act in garmin_acts:
            start_raw = act.get("startTimeGMT") or act.get("startTime") or act.get("startTimeLocal") or ""
            start_dt = _parse_datetime(start_raw) or datetime.utcnow()
            incoming_type = _garmin_type_key(act)
            existing = _find_matching_activity(existing_docs, start_dt, incoming_type)
            act_id = str(act.get("activityId") or act.get("activityID"))
            doc_id = existing["id"] if existing else f"garmin_{act_id}"
            merged = _build_unified_garmin_doc(
                doc_id, act, start_dt, existing, list_mode=True
            )
            if _write_activity_if_changed(uid, doc_id, merged):
                activity_writes += 1
                date_changed = True
            if existing is None:
                existing_docs.append(merged)
            else:
                idx = existing_docs.index(existing)
                existing_docs[idx] = merged
        if date_changed:
            _refresh_daily_log_index(uid, date_key)

    no_changes = health_writes == 0 and activity_writes == 0
    logger.info(
        f"Sync vitals ok per {uid} ({health_days} giorni health, {len(activities)} attivita, "
        f"writes health={health_writes} activities={activity_writes}, no_changes={no_changes})"
    )
    return {
        "success": True,
        "health_days_synced": health_days,
        "activities_synced": len(activities),
        "message": f"Aggiornati {health_days} giorni biometrici e {len(activities)} attivita.",
        "no_changes": no_changes,
    }


# === ENDPOINT LOGIN GARMIN (il tasto "Connect Garmin") ===
@app.post("/garmin/connect")
async def connect_garmin(
    req: GarminConnectRequest,
    _: None = Depends(verify_optional_bearer),
):
    _require_db()
    uid = req.uid.strip()
    uid_short = (uid[:8] + "…") if len(uid) > 8 else uid
    email_host = (
        req.email.strip().split("@")[-1].lower()
        if "@" in req.email
        else "?"
    )
    logger.info(
        f"connect_garmin: inizio login Garmin uid={uid_short} email_host={email_host}"
    )

    try:
        client = Garmin(req.email, req.password)
        # Importante: al primo login NON passare tokenstore, altrimenti la libreria prova a caricare token esistenti.
        client.login()
        token_b64 = client.garth.dumps()
        _save_garmin_token_to_firestore(uid, token_b64)

        # Marca utente come collegato su Firestore
        db.collection("users").document(uid).set(
            {
                "garmin_linked": True,
                "garmin_linked_at": datetime.utcnow().isoformat(),
                "garmin_last_email": req.email,
            },
            merge=True,
            timeout=_firestore_timeout_sec(),
        )

        _set_backfill_status(uid, "pending", progress=0.0, message="In coda", source="garmin")
        threading.Thread(
            target=_garmin_backfill_worker,
            args=(uid, token_b64),
            daemon=True,
            name=f"garmin_backfill_{uid[:8]}",
        ).start()
        logger.info(f"Garmin collegato per uid={uid_short}, backfill avviato in background")
        return {
            "success": True,
            "backfillQueued": True,
            "message": (
                "Garmin collegato. Recupero storico (circa 60 giorni) in corso sul server; "
                "puoi usare l'app subito e controllare l'avanzamento in sync_status/backfill."
            ),
        }

    except GarminConnectTooManyRequestsError as e:
        _log_garmin_comms("connect_garmin.rate_limit_exc", uid, e)
        logger.warning(f"Login fallito per {uid}: rate limit (libreria)")
        raise HTTPException(
            status_code=429,
            detail=_truncate_http_detail(str(e))
            or "Troppi tentativi di accesso a Garmin. Attendi 15-30 minuti e riprova.",
        )
    except GarminConnectAuthenticationError as e:
        _log_garmin_comms("connect_garmin.auth_error", uid, e)
        logger.warning(f"Login Garmin authentication {uid}: {e}")
        raise _http_exception_for_garmin_auth_error(e)
    except GarminConnectConnectionError as e:
        _log_garmin_comms("connect_garmin.connection", uid, e)
        logger.warning(f"Login fallito per {uid} (connessione/API Garmin): {e}")
        raise HTTPException(
            status_code=502,
            detail=_truncate_http_detail(str(e))
            or "Connessione a Garmin Connect non riuscita. Riprova tra poco.",
        )
    except GarthHTTPError as e:
        # Deve stare PRIMA di GarthException (GarthHTTPError ne è sottoclasse).
        status = getattr(getattr(e, "response", None), "status_code", None)
        _log_garmin_comms("connect_garmin.garth_http", uid, e, extra=f"mapped_status={status}")
        detail = _truncate_http_detail(str(e))
        if status in (401, 403):
            logger.warning(f"Login fallito per {uid} (HTTP {status})")
            raise HTTPException(
                status_code=401,
                detail=detail or "Risposta Garmin 401/403 durante il login.",
            )
        if status == 429:
            logger.warning(f"Login fallito per {uid}: rate limit Garmin (429)")
            raise HTTPException(
                status_code=429,
                detail=detail
                or "Troppi tentativi di accesso a Garmin. Attendi 15-30 minuti e riprova.",
            )
        logger.error(f"Errore HTTP Garmin {uid}: {status} - {e}")
        raise HTTPException(
            status_code=502,
            detail=detail or f"Errore HTTP verso Garmin (status {status}).",
        )
    except GarthException as e:
        _log_garmin_comms("connect_garmin.garth_other", uid, e)
        logger.warning(f"Login fallito per {uid} (Garth): {e}")
        raise HTTPException(
            status_code=502,
            detail=_truncate_http_detail(str(e))
            or "Errore client Garmin (Garth). Aggiorna garminconnect e garth sul server (pip install -U garminconnect garth).",
        )
    except Exception as e:
        fe = _http_exception_if_firestore_error(e)
        if fe is not None:
            _log_garmin_comms("connect_garmin.firestore", uid, e)
            logger.error(f"Firestore/Google durante connect {uid}: {e}")
            raise fe

        def _all_messages(exc):
            msgs = [str(exc)]
            if exc.__cause__:
                msgs.append(str(exc.__cause__))
            if exc.__context__:
                msgs.append(str(exc.__context__))
            return " ".join(msgs).lower()

        err_msg = _all_messages(e)
        # Evita falsi 429: la sola sottostringa "429" compare in URL/stack non legati al rate limit.
        looks_rate_limited = (
            "too many requests" in err_msg
            or "rate limit" in err_msg
            or "too many request" in err_msg
        )
        if looks_rate_limited:
            _log_garmin_comms("connect_garmin.rate_limit", uid, e)
            logger.warning(f"Login fallito per {uid}: rate limit Garmin (testo eccezione)")
            raise HTTPException(
                status_code=429,
                detail="Troppi tentativi di accesso a Garmin. Attendi 15-30 minuti e riprova.",
            )
        if "preauthorized" in err_msg or "oauth-service" in err_msg:
            _log_garmin_comms("connect_garmin.oauth_generic", uid, e)
            raise HTTPException(status_code=503, detail=_truncate_http_detail(str(e)))
        # Evita false positive: parole tipo "login" / "authentication" compaiono in molti errori Garmin non legati alla password.
        credential_markers = (
            "incorrect email",
            "wrong password",
            "invalid credentials",
            "invalid_grant",
            "invalid username",
        )
        if any(m in err_msg for m in credential_markers):
            _log_garmin_comms("connect_garmin.auth_keyword", uid, e)
            logger.warning(f"Login fallito per {uid}: {e}")
            raise HTTPException(status_code=401, detail=_truncate_http_detail(str(e)))
        _log_garmin_comms("connect_garmin.unexpected", uid, e)
        logger.error(f"Errore {uid}: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=500,
            detail=_truncate_http_detail(f"{type(e).__name__}: {e}", 800),
        )

# === ENDPOINT SYNC IMMEDIATA (pull-to-refresh / login app) ===
@app.post("/garmin/sync")
async def sync_garmin(
    req: GarminSyncRequest,
    _: None = Depends(verify_optional_bearer),
):
    _require_db()
    uid = req.uid.strip()
    sync_result = sync_user(uid)
    if not sync_result["success"]:
        detail = sync_result["message"]
        status_code = 404 if "non collegato" in detail.lower() else 500
        raise HTTPException(status_code=status_code, detail=detail)

    health_days = sync_result.get("health_days_synced", 0)
    return {
        "success": True,
        "message": f"Sync Garmin completata. Sincronizzate {sync_result['activities_synced']} attivita, {health_days} giorni di dati biometrici."
    }

# === ENDPOINT DISCONNECT (scollega account Garmin) ===
@app.post("/garmin/disconnect")
async def disconnect_garmin(
    req: GarminSyncRequest,
    _: None = Depends(verify_optional_bearer),
):
    """Elimina i token Garmin da Firestore e marca l'utente come scollegato."""
    _require_db()
    uid = req.uid.strip()

    try:
        _delete_garmin_token_from_firestore(uid)
        logger.info(f"Token Garmin eliminati per {uid}")

        # 2. Aggiorna Firestore: garmin_linked = False
        db.collection("users").document(uid).set(
            {
                "garmin_linked": False,
                "garmin_disconnected_at": datetime.utcnow().isoformat(),
            },
            merge=True,
            timeout=_firestore_timeout_sec(),
        )

        return {
            "success": True,
            "message": "Account Garmin scollegato correttamente.",
        }
    except Exception as e:
        logger.error(f"Disconnect fallito {uid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === ENDPOINT SYNC VITALS (pull-to-refresh / post-login: biometrici + attivita) ===
@app.post("/garmin/sync-vitals")
async def sync_vitals(
    req: GarminSyncRequest,
    _: None = Depends(verify_optional_bearer),
):
    """Compat: stesso comportamento di /garmin/sync-today (oggi+ieri + attività recenti)."""
    _require_db()
    uid = req.uid.strip()
    logger.info(f"📥 sync-vitals richiesta ricevuta per uid={uid[:8]}...")
    try:
        logger.info(f"🔗 Connesso a Garmin Connect per {uid[:8]}..., avvio sync...")
        return _run_garmin_sync_today(uid)
    except HTTPException:
        raise
    except (GarminConnectConnectionError, GarminConnectAuthenticationError, GarthException, GarthHTTPError) as e:
        _log_garmin_comms("sync_vitals.session_invalid", uid, e)
        _delete_garmin_token_from_firestore(uid)
        db.collection("users").document(uid).set(
            {"garmin_linked": False},
            merge=True,
            timeout=_firestore_timeout_sec(),
        )
        _store_sync_status(uid, success=False, message="Sessione Garmin scaduta.")
        logger.warning(f"Sync vitals fallito per {uid} (token non valido, rimosso)")
        raise HTTPException(status_code=401, detail="Sessione Garmin scaduta. Ricollega l'account.")
    except Exception as e:
        _log_garmin_comms("sync_vitals.error", uid, e)
        _store_sync_status(uid, success=False, message=str(e))
        logger.error(f"Sync vitals fallito {uid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _parse_last_successful_sync(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, dict) and "_seconds" in v:
        return datetime.fromtimestamp(int(v["_seconds"]), tz=timezone.utc)
    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1e12:
            x /= 1000.0
        return datetime.fromtimestamp(x, tz=timezone.utc)
    if isinstance(v, str):
        s = v.strip().replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(s)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc)
        except Exception:
            return None
    return None


def _run_garmin_sync_today(uid: str) -> dict:
    """Logica condivisa sync-today / sync-vitals + lastSuccessfulSync."""
    token_b64 = _get_garmin_token_from_firestore(uid)
    if not token_b64:
        raise HTTPException(
            status_code=404,
            detail="Account Garmin non collegato. Esegui prima il login Garmin.",
        )
    client = Garmin()
    client.login(tokenstore=token_b64)
    sync_result = _sync_vitals_for_client(client, uid, num_days=2, activities_limit=50)
    vitals_ok = sync_result.get("success", True) is not False
    _store_sync_status(
        uid,
        success=vitals_ok,
        message=sync_result.get("message"),
        activities_synced=sync_result.get("activities_synced", 0),
        health_days_synced=sync_result.get("health_days_synced", 0),
    )
    try:
        _save_garmin_token_to_firestore(uid, client.garth.dumps())
    except Exception:
        pass
    if vitals_ok:
        _set_last_successful_sync(uid)
    return sync_result


def _delta_garmin(uid: str, last: datetime | None) -> int:
    token = _get_garmin_token_from_firestore(uid)
    if not token:
        return 0
    client = Garmin()
    client.login(tokenstore=token)
    now = datetime.now(timezone.utc).date()
    if last:
        start = last.astimezone(timezone.utc).date() - timedelta(days=1)
    else:
        start = now - timedelta(days=7)
    if start > now:
        start = now - timedelta(days=1)
    max_days = int(os.getenv("BACKFILL_DAYS", "60"))
    days_span = (now - start).days + 1
    num_days = min(max(days_span, 1), max_days)
    _, hw = _sync_daily_health(client, uid, num_days=num_days)
    total_writes = hw
    cur = start
    while cur <= now:
        chunk_end = min(cur + timedelta(days=9), now)
        try:
            acts = client.get_activities_by_date(cur.isoformat(), chunk_end.isoformat())
            if acts:
                total_writes += _ingest_garmin_activity_list(uid, acts)
        except Exception as e:
            logger.warning(f"delta garmin activities {cur}-{chunk_end}: {e}")
        cur = chunk_end + timedelta(days=1)
    try:
        _save_garmin_token_to_firestore(uid, client.garth.dumps())
    except Exception:
        pass
    return total_writes


def _delta_strava(uid: str, last: datetime | None) -> int:
    access = _ensure_strava_access_token(uid)
    if not access:
        return 0
    after = int((last or (datetime.now(timezone.utc) - timedelta(days=7))).timestamp())
    page = 1
    writes = 0
    while True:
        batch = strava_sync.strava_list_activities(
            access, after_epoch=after, page=page, per_page=200
        )
        if not batch:
            break
        for raw in batch:
            try:
                if _upsert_strava_activity(uid, raw):
                    writes += 1
            except Exception as e:
                logger.debug(f"delta strava upsert: {e}")
        if len(batch) < 200:
            break
        page += 1
    recent = strava_sync.strava_list_activities(access, page=1, per_page=5)
    for raw in recent:
        aid = raw.get("id")
        if aid is None:
            continue
        try:
            det = strava_sync.strava_get_activity_detail(access, int(aid))
            if _upsert_strava_activity(uid, det):
                writes += 1
        except Exception as e:
            logger.debug(f"strava detail {aid}: {e}")
    return writes


def _strava_backfill_worker(uid: str) -> None:
    if db is None:
        return
    if not _strava_client_configured():
        _set_backfill_status(uid, "error", message="STRAVA_CLIENT_ID/SECRET mancanti", source="strava")
        return
    try:
        _set_backfill_status(uid, "processing", progress=0.05, message="Strava backfill…", source="strava")
        access = _ensure_strava_access_token(uid)
        if not access:
            _set_backfill_status(uid, "error", message="Token Strava non disponibile", source="strava")
            return
        days = int(os.getenv("BACKFILL_DAYS", "60"))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        after = int(cutoff.timestamp())
        page = 1
        while True:
            batch = strava_sync.strava_list_activities(
                access, after_epoch=after, page=page, per_page=200
            )
            if not batch:
                break
            for raw in batch:
                try:
                    _upsert_strava_activity(uid, raw)
                except Exception as e:
                    logger.debug(f"strava backfill upsert: {e}")
            if len(batch) < 200:
                break
            page += 1
            _set_backfill_status(
                uid,
                "processing",
                progress=min(0.95, 0.1 + page * 0.05),
                message=f"Strava pag. {page}",
                source="strava",
            )
        db.collection("users").document(uid).set(
            {"strava_initial_sync_done": True},
            merge=True,
            timeout=_firestore_timeout_sec(),
        )
        _set_backfill_status(uid, "completed", progress=1.0, message="Strava completato", source="strava")
        _set_last_successful_sync(uid)
        logger.success(f"Backfill Strava completato {uid[:8]}…")
    except Exception as e:
        logger.exception("strava backfill")
        _set_backfill_status(uid, "error", message=str(e)[:500], source="strava")


@app.post("/garmin/sync-today")
async def garmin_sync_today(
    req: GarminSyncRequest,
    _: None = Depends(verify_optional_bearer),
):
    _require_db()
    uid = req.uid.strip()
    try:
        return _run_garmin_sync_today(uid)
    except HTTPException:
        raise
    except (GarminConnectConnectionError, GarminConnectAuthenticationError, GarthException, GarthHTTPError) as e:
        _log_garmin_comms("sync_today.session_invalid", uid, e)
        _delete_garmin_token_from_firestore(uid)
        db.collection("users").document(uid).set(
            {"garmin_linked": False},
            merge=True,
            timeout=_firestore_timeout_sec(),
        )
        raise HTTPException(status_code=401, detail="Sessione Garmin scaduta. Ricollega l'account.")
    except Exception as e:
        logger.error(f"sync-today {uid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sync/delta")
async def sync_delta(
    req: DeltaSyncRequest,
    _: None = Depends(verify_optional_bearer),
):
    _require_db()
    uid = req.uid.strip()
    last = _parse_last_successful_sync(req.lastSuccessfulSync)
    sources = {s.lower() for s in (req.sources or ["garmin", "strava"])}
    try:
        delta_writes = 0
        if "garmin" in sources:
            try:
                delta_writes += _delta_garmin(uid, last)
            except Exception as e:
                logger.warning(f"delta garmin: {e}")
        if "strava" in sources and _strava_client_configured():
            try:
                delta_writes += _delta_strava(uid, last)
            except Exception as e:
                logger.warning(f"delta strava: {e}")
        _set_last_successful_sync(uid)
        return {
            "success": True,
            "message": "Delta sync completata",
            "no_changes": delta_writes == 0,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/strava/register-tokens")
async def strava_register_tokens(
    req: StravaRegisterRequest,
    _: None = Depends(verify_optional_bearer),
):
    _require_db()
    uid = req.uid.strip()
    if not _strava_client_configured():
        raise HTTPException(
            status_code=503,
            detail="Server senza STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET",
        )
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=6)
    if req.expires_at is not None:
        x = float(req.expires_at)
        if x > 1e12:
            x /= 1000.0
        exp = datetime.fromtimestamp(x, tz=timezone.utc)
    _save_strava_tokens_to_firestore(uid, req.access_token, req.refresh_token, exp)
    _set_backfill_status(uid, "pending", progress=0.0, message="Strava in coda", source="strava")
    threading.Thread(
        target=_strava_backfill_worker,
        args=(uid,),
        daemon=True,
        name=f"strava_backfill_{uid[:8]}",
    ).start()
    return {
        "success": True,
        "message": "Token Strava registrati",
        "backfillQueued": True,
    }


@app.post("/strava/disconnect")
async def strava_disconnect(
    req: GarminSyncRequest,
    _: None = Depends(verify_optional_bearer),
):
    _require_db()
    uid = req.uid.strip()
    _delete_strava_tokens_from_firestore(uid)
    db.collection("users").document(uid).set(
        {"strava_initial_sync_done": False},
        merge=True,
        timeout=_firestore_timeout_sec(),
    )
    return {"success": True, "message": "Strava disconnesso sul server"}


@app.post("/garmin/activity-detail")
async def garmin_activity_detail(
    req: ActivityDetailRequest,
    _: None = Depends(verify_optional_bearer),
):
    _require_db()
    uid = req.uid.strip()
    if req.garmin_activity_id is not None:
        token_b64 = _get_garmin_token_from_firestore(uid)
        if not token_b64:
            raise HTTPException(status_code=400, detail="Garmin non collegato")
        client = Garmin()
        client.login(tokenstore=token_b64)
        detail = client.get_activity(str(req.garmin_activity_id))
        changed = False
        if isinstance(detail, dict):
            start_raw = (
                detail.get("startTimeGMT")
                or detail.get("startTime")
                or detail.get("startTimeLocal")
                or ""
            )
            start_dt = _parse_datetime(start_raw) or datetime.utcnow()
            act_id = str(detail.get("activityId") or detail.get("activityID") or req.garmin_activity_id)
            doc_id = f"garmin_{act_id}"
            ref = (
                db.collection("users")
                .document(uid)
                .collection("activities")
                .document(doc_id)
            )
            existing_snap = ref.get(timeout=_firestore_timeout_sec()).to_dict()
            merged = _build_unified_garmin_doc(
                doc_id, detail, start_dt, existing_snap, list_mode=False
            )
            changed = _write_activity_if_changed(uid, doc_id, merged)
            if changed:
                _refresh_daily_log_index(uid, _date_key(start_dt))
        try:
            _save_garmin_token_to_firestore(uid, client.garth.dumps())
        except Exception:
            pass
        return {"success": True, "source": "garmin", "no_changes": not changed}
    if req.strava_activity_id is not None:
        access = _ensure_strava_access_token(uid)
        if not access:
            raise HTTPException(status_code=400, detail="Strava non disponibile")
        raw = strava_sync.strava_get_activity_detail(access, int(req.strava_activity_id))
        strava_changed = _upsert_strava_activity(uid, raw)
        return {"success": True, "source": "strava", "no_changes": not strava_changed}
    raise HTTPException(
        status_code=400,
        detail="Specificare garmin_activity_id o strava_activity_id",
    )


# === SYNC DAILY HEALTH (passi, sonno, HRV, Body Battery) ===
DAILY_HEALTH_SYNC_DAYS = 14  # Ultimi N giorni da sincronizzare (sync full)
ACTIVITY_MERGE_WINDOW_MINUTES = 2

def _date_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")

def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None

def _normalize_activity_type(raw_type):
    value = str(raw_type or "").strip().lower()
    if value == "running":
        return "run"
    if value in ("cycling", "bike"):
        return "ride"
    if value in ("walking", "hiking"):
        return "walk"
    return value

def _same_activity_type(left: str, right: str) -> bool:
    if not left or not right:
        return True
    run_like = {"run", "running", "trailrun"}
    ride_like = {"ride", "cycling", "bike", "virtualride"}
    walk_like = {"walk", "walking", "hike", "hiking"}
    if left in run_like and right in run_like:
        return True
    if left in ride_like and right in ride_like:
        return True
    if left in walk_like and right in walk_like:
        return True
    return left == right

def _garmin_type_key(act: dict) -> str:
    act_type = act.get("activityType")
    if isinstance(act_type, dict):
        return str(act_type.get("typeKey") or act_type.get("typeId") or "")
    return str(act_type or "")

def _firestore_safe_raw(obj: dict | None, max_depth: int = 3) -> dict | None:
    """Estrae un subset Firestore-safe da raw (niente array di oggetti, nesting limitato)."""
    if not obj or not isinstance(obj, dict):
        return None
    out = {}
    for k, v in obj.items():
        if v is None or isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, dict) and max_depth > 0:
            nested = _firestore_safe_raw(v, max_depth - 1)
            if nested is not None:
                out[k] = nested
        elif isinstance(v, list):
            # Firestore: array di oggetti non consentito. Solo primitivi.
            safe = [x for x in v if x is None or isinstance(x, (str, int, float, bool))]
            if len(safe) == len(v):
                out[k] = safe
            # altrimenti salta l'array (es. samples GPS)
    return out if out else None


# Chiavi Garmin incluse in garmin_raw per sync leggera (lista / pull); dettaglio full da get_activity + /garmin/activity-detail
_GARMIN_LIST_RAW_KEYS = (
    "activityId",
    "activityID",
    "activityName",
    "activityType",
    "startTimeGMT",
    "startTime",
    "startTimeLocal",
    "duration",
    "movingDuration",
    "distance",
    "calories",
    "averageHR",
    "maxHR",
    "elevationGain",
    "deviceName",
)


def _garmin_list_summary_raw(act: dict) -> dict | None:
    out: dict = {}
    for k in _GARMIN_LIST_RAW_KEYS:
        if k not in act:
            continue
        v = act[k]
        if v is not None:
            out[k] = v
    at = act.get("activityType")
    if isinstance(at, dict):
        out["activityType"] = at
    return _firestore_safe_raw(out, max_depth=4)


def _norm_cmp_val(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(a): _norm_cmp_val(b) for a, b in sorted(v.items(), key=lambda x: str(x[0]))}
    if isinstance(v, list):
        return [_norm_cmp_val(x) for x in v]
    if isinstance(v, float):
        return round(v, 6)
    return v


def _activity_compare_payload(d: dict | None) -> dict:
    if not d:
        return {}
    keys = (
        "distanceKm",
        "calories",
        "activeMinutes",
        "elapsedMinutes",
        "activityType",
        "activityName",
        "source",
        "hasGarmin",
        "hasStrava",
        "garminActivityId",
        "stravaActivityId",
        "avgHeartrate",
        "maxHeartrate",
        "elevationGainM",
        "deviceName",
        "avgSpeedKmh",
        "steps",
    )
    out = {}
    for k in keys:
        if k in d:
            out[k] = _norm_cmp_val(d.get(k))
    for rawk in ("garmin_raw", "strava_raw"):
        if rawk in d and d[rawk] is not None:
            out[rawk] = _norm_cmp_val(d[rawk])
    return out


def _activities_equal_for_sync(prev: dict | None, merged: dict) -> bool:
    if not prev:
        return False
    return _activity_compare_payload(prev) == _activity_compare_payload(merged)


def _daily_health_compare_payload(d: dict | None) -> dict:
    if not d:
        return {}
    out = {}
    for k, v in sorted(d.items()):
        if k in ("syncedAt",):
            continue
        out[k] = _norm_cmp_val(v)
    return out


def _daily_health_equal(prev: dict | None, new: dict) -> bool:
    return _daily_health_compare_payload(prev) == _daily_health_compare_payload(new)


def _write_activity_if_changed(uid: str, doc_id: str, merged: dict) -> bool:
    cref = (
        db.collection("users")
        .document(uid)
        .collection("activities")
        .document(doc_id)
    )
    prev = cref.get(timeout=_firestore_timeout_sec()).to_dict()
    if _activities_equal_for_sync(prev, merged):
        return False
    cref.set(merged, merge=True, timeout=_firestore_timeout_sec())
    return True


def _existing_has_strava(data: dict | None) -> bool:
    if not data:
        return False
    return bool(
        data.get("hasStrava")
        or data.get("source") in ("strava", "dual")
        or data.get("strava_raw")
        or data.get("stravaActivityId")
    )


def _existing_has_garmin(data: dict | None) -> bool:
    if not data:
        return False
    return bool(
        data.get("hasGarmin")
        or data.get("source") in ("garmin", "dual")
        or data.get("garmin_raw")
        or data.get("garminActivityId")
    )


def _parse_strava_start(raw: dict) -> datetime:
    v = raw.get("start_date") or raw.get("start_date_local")
    if not v:
        return datetime.utcnow()
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d.replace(tzinfo=None) if d.tzinfo else d
    except Exception:
        return datetime.utcnow()


def _build_unified_strava_doc(
    doc_id: str, raw: dict, start_dt: datetime, existing: dict | None = None
) -> dict:
    distance_m = float(raw.get("distance") or 0)
    moving_sec = int(raw.get("moving_time") or 0)
    elapsed_sec = int(raw.get("elapsed_time") or moving_sec)
    avg_speed = raw.get("average_speed")
    if avg_speed is not None:
        avg_speed = float(avg_speed)
    has_g = _existing_has_garmin(existing)
    garmin_raw = existing.get("garmin_raw") if existing else None
    raw_safe = _firestore_safe_raw(raw, max_depth=6) or {}
    return {
        "id": doc_id,
        "source": "dual" if has_g else "strava",
        "date": start_dt,
        "startTime": start_dt,
        "dateKey": _date_key(start_dt),
        "calories": float(raw["calories"]) if isinstance(raw.get("calories"), (int, float)) else None,
        "distanceKm": distance_m / 1000.0,
        "activeMinutes": moving_sec / 60.0,
        "activityType": raw.get("sport_type") or raw.get("type"),
        "activityName": raw.get("name"),
        "deviceName": raw.get("device_name"),
        "elevationGainM": float(raw["total_elevation_gain"])
        if isinstance(raw.get("total_elevation_gain"), (int, float))
        else None,
        "avgHeartrate": float(raw["average_heartrate"])
        if isinstance(raw.get("average_heartrate"), (int, float))
        else None,
        "maxHeartrate": float(raw["max_heartrate"])
        if isinstance(raw.get("max_heartrate"), (int, float))
        else None,
        "avgSpeedKmh": (avg_speed * 3.6) if avg_speed is not None else None,
        "elapsedMinutes": elapsed_sec / 60.0,
        "hasGarmin": has_g,
        "hasStrava": True,
        "garminActivityId": existing.get("garminActivityId") if existing else None,
        "stravaActivityId": str(raw.get("id") or ""),
        "garmin_raw": _firestore_safe_raw(garmin_raw) if garmin_raw else None,
        "strava_raw": raw_safe,
        "raw": raw_safe,
        "syncedAt": datetime.utcnow(),
    }


def _upsert_strava_activity(uid: str, raw: dict) -> bool:
    start_dt = _parse_strava_start(raw)
    dk = _date_key(start_dt)
    incoming_type = str(raw.get("sport_type") or raw.get("type") or "")
    existing_docs = _load_existing_activities_for_date(uid, dk)
    existing = _find_matching_activity(existing_docs, start_dt, incoming_type)
    sid = raw.get("id")
    doc_id = existing["id"] if existing else f"strava_{sid}"
    merged = _build_unified_strava_doc(doc_id, raw, start_dt, existing)
    changed = _write_activity_if_changed(uid, doc_id, merged)
    if changed:
        _refresh_daily_log_index(uid, dk)
    return changed


def _load_existing_activities_for_date(uid: str, date_key: str) -> list[dict]:
    aq = (
        db.collection("users")
        .document(uid)
        .collection("activities")
    )
    if FieldFilter is not None:
        aq = aq.where(filter=FieldFilter("dateKey", "==", date_key))
    else:
        aq = aq.where("dateKey", "==", date_key)
    to = _firestore_timeout_sec()
    try:
        docs = aq.get(timeout=to)
    except TypeError:
        docs = list(aq.stream())
    return [{"id": doc.id, **(doc.to_dict() or {})} for doc in docs]

def _naive_utc(dt: datetime | None) -> datetime | None:
    """Converte a datetime naive UTC per confronto (evita naive vs aware)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _find_matching_activity(existing_docs: list[dict], start_dt: datetime, incoming_type: str):
    normalized_type = _normalize_activity_type(incoming_type)
    start_naive = _naive_utc(start_dt) or start_dt
    for doc in existing_docs:
        candidate_start = _parse_datetime(doc.get("startTime")) or _parse_datetime(doc.get("date"))
        if candidate_start is None:
            continue
        cand_naive = _naive_utc(candidate_start) or candidate_start
        if abs((cand_naive - start_naive).total_seconds()) > ACTIVITY_MERGE_WINDOW_MINUTES * 60:
            continue
        candidate_type = _normalize_activity_type(doc.get("activityType"))
        if _same_activity_type(candidate_type, normalized_type):
            return doc
    return None

def _build_unified_garmin_doc(
    doc_id: str,
    act: dict,
    start_dt: datetime,
    existing: dict | None = None,
    *,
    list_mode: bool = False,
) -> dict:
    act_id = str(act.get("activityId") or act.get("activityID") or "")
    type_key = _garmin_type_key(act)
    duration_sec = (
        (act.get("duration") or act.get("movingDuration") or 0)
        if isinstance(act.get("duration") or act.get("movingDuration") or 0, (int, float))
        else 0
    )
    distance_raw = act.get("distance")
    distance_val = float(distance_raw) if isinstance(distance_raw, (int, float)) else 0.0
    distance_km = distance_val / 1000 if distance_val > 100 else distance_val
    has_strava = _existing_has_strava(existing)
    strava_raw = existing.get("strava_raw") if existing else None

    garmin_raw_out = (
        _garmin_list_summary_raw(act) if list_mode else _firestore_safe_raw(act)
    )
    if has_strava:
        raw_out = _firestore_safe_raw(strava_raw) if strava_raw else None
    else:
        raw_out = (
            _garmin_list_summary_raw(act) if list_mode else _firestore_safe_raw(act)
        )

    return {
        "id": doc_id,
        "source": "dual" if has_strava else "garmin",
        "date": start_dt,
        "startTime": start_dt,
        "dateKey": _date_key(start_dt),
        "calories": float(act["calories"]) if isinstance(act.get("calories"), (int, float)) else None,
        "distanceKm": distance_km if distance_km > 0 else None,
        "activeMinutes": (duration_sec / 60.0) if duration_sec else None,
        "activityType": type_key or str(act.get("activityType") or ""),
        "activityName": act.get("activityName"),
        "deviceName": act.get("deviceName"),
        "elevationGainM": float(act["elevationGain"]) if isinstance(act.get("elevationGain"), (int, float)) else None,
        "avgHeartrate": float(act["averageHR"]) if isinstance(act.get("averageHR"), (int, float)) else None,
        "maxHeartrate": float(act["maxHR"]) if isinstance(act.get("maxHR"), (int, float)) else None,
        "elapsedMinutes": (duration_sec / 60.0) if duration_sec else None,
        "hasGarmin": True,
        "hasStrava": has_strava,
        "garminActivityId": act_id or None,
        "stravaActivityId": str(existing.get("stravaActivityId")) if existing and existing.get("stravaActivityId") is not None else None,
        "garmin_raw": garmin_raw_out,
        "strava_raw": _firestore_safe_raw(strava_raw) if strava_raw else None,
        "raw": raw_out,
        "syncedAt": datetime.utcnow(),
    }

def _refresh_daily_log_index(uid: str, date_key: str):
    activities = _load_existing_activities_for_date(uid, date_key)
    total_burned = 0.0
    activity_ids: list[str] = []
    for activity in activities:
        activity_ids.append(activity["id"])
        calories = activity.get("calories")
        if isinstance(calories, (int, float)):
            total_burned += float(calories)
    activity_ids.sort()
    (
        db.collection("users")
        .document(uid)
        .collection("daily_logs")
        .document(date_key)
        .set(
            {
                "date": date_key,
                "activity_ids": activity_ids,
                "health_ref": date_key,
                "total_burned_kcal": total_burned,
                "timestamp": datetime.utcnow(),
            },
            merge=True,
            timeout=_firestore_timeout_sec(),
        )
    )


def _ingest_garmin_activity_list(uid: str, activities: list) -> int:
    """Merge attività Garmin in Firestore. Ritorna numero di documenti effettivamente aggiornati."""
    by_date: dict[str, list[dict]] = {}
    for act in activities:
        act_id = act.get("activityId") or act.get("activityID")
        if not act_id:
            continue
        start_raw = act.get("startTimeGMT") or act.get("startTime") or act.get("startTimeLocal") or ""
        dt = _parse_datetime(start_raw) or datetime.utcnow()
        date_key = _date_key(dt)
        by_date.setdefault(date_key, []).append(act)
    writes = 0
    for date_key, garmin_acts in by_date.items():
        existing_docs = _load_existing_activities_for_date(uid, date_key)
        date_changed = False
        for act in garmin_acts:
            start_raw = act.get("startTimeGMT") or act.get("startTime") or act.get("startTimeLocal") or ""
            start_dt = _parse_datetime(start_raw) or datetime.utcnow()
            incoming_type = _garmin_type_key(act)
            existing = _find_matching_activity(existing_docs, start_dt, incoming_type)
            act_id = str(act.get("activityId") or act.get("activityID"))
            doc_id = existing["id"] if existing else f"garmin_{act_id}"
            merged = _build_unified_garmin_doc(doc_id, act, start_dt, existing, list_mode=False)
            if _write_activity_if_changed(uid, doc_id, merged):
                writes += 1
                date_changed = True
            if existing is None:
                existing_docs.append(merged)
            else:
                idx = existing_docs.index(existing)
                existing_docs[idx] = merged
        if date_changed:
            _refresh_daily_log_index(uid, date_key)
    return writes


def _garmin_backfill_worker(uid: str, token_str: str) -> None:
    """Backfill BACKFILL_DAYS dopo connect (thread daemon)."""
    if db is None:
        return
    backfill_days = int(os.getenv("BACKFILL_DAYS", "60"))
    batch_days = max(1, int(os.getenv("GARMIN_BACKFILL_BATCH_DAYS", "10")))
    try:
        _set_backfill_status(uid, "processing", progress=0.02, message="Backfill Garmin…", source="garmin")
        client = Garmin()
        client.login(tokenstore=token_str)
        health_days, _ = _sync_daily_health(client, uid, num_days=backfill_days)
        _set_backfill_status(
            uid, "processing", progress=0.25, message=f"Biometrici {health_days} gg", source="garmin"
        )
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=backfill_days)
        cur = start
        nchunk = 0
        est_chunks = max(1, (end - start).days // batch_days + 2)
        while cur <= end:
            chunk_end = min(cur + timedelta(days=batch_days - 1), end)
            try:
                acts = client.get_activities_by_date(cur.isoformat(), chunk_end.isoformat())
                if acts:
                    _ingest_garmin_activity_list(uid, acts)
            except Exception as e:
                logger.warning(f"get_activities_by_date {cur}-{chunk_end}: {e}")
            nchunk += 1
            prog = 0.25 + 0.7 * min(1.0, nchunk / est_chunks)
            _set_backfill_status(
                uid, "processing", progress=prog, message=f"Attività fino a {chunk_end}", source="garmin"
            )
            cur = chunk_end + timedelta(days=1)
        try:
            _save_garmin_token_to_firestore(uid, client.garth.dumps())
        except Exception:
            pass
        db.collection("users").document(uid).set(
            {"garmin_initial_sync_done": True},
            merge=True,
            timeout=_firestore_timeout_sec(),
        )
        _store_sync_status(
            uid,
            success=True,
            message="Backfill Garmin completato",
            activities_synced=0,
            health_days_synced=health_days,
        )
        _set_backfill_status(uid, "completed", progress=1.0, message="Garmin completato", source="garmin")
        _set_last_successful_sync(uid)
        logger.success(f"Backfill Garmin completato {uid[:8]}…")
    except (
        GarminConnectConnectionError,
        GarminConnectAuthenticationError,
        GarthException,
        GarthHTTPError,
    ) as e:
        _log_garmin_comms("backfill.session_invalid", uid, e)
        _set_backfill_status(uid, "error", message="Sessione Garmin non valida", source="garmin")
        _store_sync_status(uid, success=False, message=str(e))
    except Exception as e:
        _log_garmin_comms("backfill.error", uid, e)
        _set_backfill_status(uid, "error", message=str(e)[:500], source="garmin")
        _store_sync_status(uid, success=False, message=str(e))
        logger.error(f"Backfill Garmin fallito {uid}: {e}")


def _sync_daily_health(client: Garmin, uid: str, num_days: int | None = None) -> tuple[int, int]:
    """Ritorna (giorni_con_dati_sostanziali, scritture_firestore_effettive)."""
    today = datetime.now().date()
    days = num_days if num_days is not None else DAILY_HEALTH_SYNC_DAYS
    synced_count = 0
    firestore_writes = 0

    for i in range(days):
        d = today - timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        doc_data = {"date": date_str, "syncedAt": datetime.utcnow().isoformat()}

        # get_stats: passi, riepilogo giornaliero
        try:
            stats = client.get_stats(date_str)
            if isinstance(stats, dict):
                doc_data["stats"] = stats
        except Exception as e:
            logger.debug(f"get_stats {date_str} non disponibile: {e}")

        # get_sleep_data: sonno
        try:
            sleep = client.get_sleep_data(date_str)
            if isinstance(sleep, dict):
                doc_data["sleep"] = sleep
        except Exception as e:
            logger.debug(f"get_sleep_data {date_str} non disponibile: {e}")

        # get_hrv_data: HRV
        try:
            hrv = client.get_hrv_data(date_str)
            if isinstance(hrv, dict):
                doc_data["hrv"] = hrv
        except Exception as e:
            logger.debug(f"get_hrv_data {date_str} non disponibile: {e}")

        # get_body_battery: Body Battery
        try:
            bb = client.get_body_battery(date_str, date_str)
            if isinstance(bb, list) and bb:
                doc_data["body_battery"] = bb
            elif isinstance(bb, dict):
                doc_data["body_battery"] = bb
        except Exception as e:
            logger.debug(f"get_body_battery {date_str} non disponibile: {e}")

        # get_max_metrics: VO2Max e metriche massime
        try:
            max_metrics = client.get_max_metrics(date_str)
            if isinstance(max_metrics, dict) and max_metrics:
                doc_data["max_metrics"] = max_metrics
        except Exception as e:
            logger.debug(f"get_max_metrics {date_str} non disponibile: {e}")

        # get_fitnessage_data: Fitness Age
        try:
            fitness_age = client.get_fitnessage_data(date_str)
            if isinstance(fitness_age, dict) and fitness_age:
                doc_data["fitness_age"] = fitness_age
        except Exception as e:
            logger.debug(f"get_fitnessage_data {date_str} non disponibile: {e}")

        # Salva solo se abbiamo almeno un dato oltre date/syncedAt
        # Sanitizza per Firestore: niente array di oggetti (Firestore li rifiuta)
        if len(doc_data) > 2:
            safe_data = {}
            for k, v in doc_data.items():
                if v is None or isinstance(v, (str, int, float, bool)):
                    safe_data[k] = v
                elif isinstance(v, dict):
                    s = _firestore_safe_raw(v, max_depth=5)
                    if s:
                        safe_data[k] = s
                elif isinstance(v, list):
                    if all(x is None or isinstance(x, (str, int, float, bool)) for x in v):
                        safe_data[k] = v
                    # array di oggetti non consentito da Firestore: salta
            if len(safe_data) > 2:
                href = (
                    db.collection("users")
                    .document(uid)
                    .collection("daily_health")
                    .document(date_str)
                )
                existing_h = href.get(timeout=_firestore_timeout_sec()).to_dict()
                health_changed = not _daily_health_equal(existing_h, safe_data)
                if health_changed:
                    href.set(safe_data, merge=True, timeout=_firestore_timeout_sec())
                    firestore_writes += 1
                dlog_ref = (
                    db.collection("users")
                    .document(uid)
                    .collection("daily_logs")
                    .document(date_str)
                )
                existing_dl = dlog_ref.get(timeout=_firestore_timeout_sec()).to_dict() or {}
                dlog_payload = {
                    "date": date_str,
                    "health_ref": date_str,
                    "timestamp": datetime.utcnow(),
                }
                if health_changed:
                    dlog_ref.set(dlog_payload, merge=True, timeout=_firestore_timeout_sec())
                    firestore_writes += 1
                elif existing_dl.get("health_ref") != date_str:
                    dlog_ref.set(dlog_payload, merge=True, timeout=_firestore_timeout_sec())
                    firestore_writes += 1
            synced_count += 1

        time.sleep(0.5)  # rate-limit API Garmin

    return synced_count, firestore_writes

# === SYNC PER UTENTE (usa client attivo o token da Firestore) ===
def sync_user(uid: str, client: Garmin | None = None):
    if db is None:
        return {
            "success": False,
            "activities_synced": 0,
            "health_days_synced": 0,
            "message": "Server non configurato (manca Firebase in .env).",
        }
    token_b64 = _get_garmin_token_from_firestore(uid) if client is None else None
    if client is None and not token_b64:
        return {
            "success": False,
            "activities_synced": 0,
            "health_days_synced": 0,
            "message": "Account Garmin non collegato. Esegui prima il login Garmin."
        }
    try:
        if client is None:
            client = Garmin()
            client.login(tokenstore=token_b64)

        raw_activities = client.get_activities(0, 50)
        activities = _extract_activities_list(raw_activities)
        if not activities:
            logger.info(f"sync_user {uid}: get_activities ha restituito {len(activities)} attivita (raw type: {type(raw_activities).__name__})")

        # 1. daily_health: passi, sonno, HRV, Body Battery (get_stats, get_sleep_data, get_hrv_data, get_body_battery)
        health_days, _ = _sync_daily_health(client, uid)

        # 2. activities + daily_logs.activity_ids (indice unificato)
        _ingest_garmin_activity_list(uid, activities)

        logger.success(f"Sync ok per {uid} ({len(activities)} attivita, {health_days} giorni health)")
        _store_sync_status(
            uid,
            success=True,
            message="Sync completata",
            activities_synced=len(activities),
            health_days_synced=health_days,
        )
        # Aggiorna token su Firestore (garth puo' aver fatto refresh)
        try:
            _save_garmin_token_to_firestore(uid, client.garth.dumps())
        except Exception:
            pass
        return {
            "success": True,
            "activities_synced": len(activities),
            "health_days_synced": health_days,
            "message": "Sync completata"
        }
    except (GarminConnectConnectionError, GarminConnectAuthenticationError, GarthException, GarthHTTPError) as e:
        _log_garmin_comms("sync_user.session_invalid", uid, e)
        _delete_garmin_token_from_firestore(uid)
        db.collection("users").document(uid).set(
            {"garmin_linked": False},
            merge=True,
            timeout=_firestore_timeout_sec(),
        )
        logger.warning(f"Sync fallito {uid}: token non valido, rimosso - {e}")
        _store_sync_status(uid, success=False, message="Sessione Garmin scaduta.")
        return {
            "success": False,
            "activities_synced": 0,
            "health_days_synced": 0,
            "message": "Sessione Garmin scaduta. Ricollega l'account.",
        }
    except Exception as e:
        _log_garmin_comms("sync_user.error", uid, e)
        logger.error(f"Sync fallito {uid}: {e}")
        _store_sync_status(uid, success=False, message=str(e))
        return {
            "success": False,
            "activities_synced": 0,
            "health_days_synced": 0,
            "message": str(e),
        }

# === SCHEDULER (multi-utente) ===
def scheduled_sync():
    """Sync Garmin per tutti gli utenti con garmin_linked=True."""
    if db is None:
        return
    logger.info("Inizio sync batch (utenti garmin_linked)...")
    uq = db.collection("users")
    if FieldFilter is not None:
        uq = uq.where(filter=FieldFilter("garmin_linked", "==", True))
    else:
        uq = uq.where("garmin_linked", "==", True)
    users = list(uq.stream())
    if not users:
        logger.info("Nessun utente garmin_linked, sync batch saltata")
        return
    for user in users:
        try:
            result = sync_user(user.id)
            if result.get("success"):
                logger.info(f"  ✓ {user.id[:8]}... ok")
            else:
                logger.warning(f"  ✗ {user.id[:8]}... {result.get('message', '')}")
        except Exception as e:
            logger.error(f"  ✗ {user.id[:8]}... {e}")
        time.sleep(10)  # rate-limit API Garmin
    logger.info(f"Sync batch completato ({len(users)} utenti)")


# === ENDPOINT PER CRON ESTERNO (opzionale) ===
@app.post("/internal/scheduled-sync")
async def trigger_scheduled_sync(
    x_cron_secret: str | None = Header(default=None),
    _: None = Depends(verify_optional_bearer),
):
    """
    Chiamato da cron esterno (es. systemd timer sul Pi o servizio esterno).
    Richiede header X-Cron-Secret = CRON_SECRET (env) se CRON_SECRET è impostato.
    Esegue sync batch per tutti gli utenti garmin_linked.
    """
    _require_db()
    secret = os.getenv("CRON_SECRET")
    if secret and x_cron_secret != secret:
        raise HTTPException(status_code=403, detail="Secret non valido")
    logger.info("📥 scheduled-sync richiesto via endpoint (cron esterno)")
    _run_scheduled_sync()
    return {"success": True, "message": "Sync batch eseguito"}


# === AVVIO ===
if __name__ == "__main__":
    logger.info("🚀 Server avviato con API + scheduler sync ogni 45 min")
    port = int(os.getenv("PORT", "8080"))
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
