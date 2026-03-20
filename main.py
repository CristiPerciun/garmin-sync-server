import json
import os
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore
try:
    from garminconnect import (
        Garmin,
        GarminConnectConnectionError,
        GarminConnectAuthenticationError,
    )
except ImportError:
    from garminconnect import Garmin, GarminConnectConnectionError
    GarminConnectAuthenticationError = GarminConnectConnectionError  # fallback
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

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

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
# Supporta FIREBASE_CREDENTIALS (JSON) o FIREBASE_CREDENTIALS_B64 (base64)
# Base64 evita problemi con caratteri speciali/newline su Windows
def _load_firebase_cred():
    import base64

    # 1. Prova JSON diretto
    raw = os.getenv("FIREBASE_CREDENTIALS")
    if raw:
        s = raw.strip().lstrip("\ufeff")
        if s:
            try:
                return credentials.Certificate(json.loads(s))
            except json.JSONDecodeError:
                pass

    # 2. Prova base64 (consigliato: evita problemi encoding)
    b64 = os.getenv("FIREBASE_CREDENTIALS_B64")
    if b64:
        try:
            decoded = base64.b64decode(b64.strip()).decode("utf-8")
            return credentials.Certificate(json.loads(decoded))
        except Exception as e:
            logger.error(f"FIREBASE_CREDENTIALS_B64 non valido: {e}")
            raise ValueError(
                "FIREBASE_CREDENTIALS_B64 non valido. Verifica che sia Base64 del JSON "
                "firebase-service-account.json. Vedi RPI_DEPLOY.md."
            )

    logger.error("Manca FIREBASE_CREDENTIALS o FIREBASE_CREDENTIALS_B64")
    raise ValueError(
        "Configura FIREBASE_CREDENTIALS_B64 (o FIREBASE_CREDENTIALS) come variabile d'ambiente. "
        "Vedi RPI_DEPLOY.md o scripts/rpi_setup/encode_firebase_credentials_b64.ps1."
    )

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


# === HELPER: token Garmin su Firestore (collection garmin_tokens/{uid}, campo token_b64) ===
# Collection separata per evitare che il client legga i token (regole Firestore negano accesso).
# Validita token Garmin: ~1 anno. Se non valido, viene rimosso e l'utente deve ricollegare.
GARMIN_TOKENS_COLLECTION = "garmin_tokens"

def _get_garmin_token_from_firestore(uid: str) -> str | None:
    """Legge token Garmin (Base64) da Firestore. Ritorna None se assente."""
    doc = db.collection(GARMIN_TOKENS_COLLECTION).document(uid).get()
    data = doc.to_dict() or {}
    token = data.get("token_b64")
    return str(token).strip() if token else None

def _save_garmin_token_to_firestore(uid: str, token_b64: str) -> None:
    """Salva token Garmin (Base64) su Firestore. Validita ~1 anno; se scaduto, va rimosso e utente ricollega."""
    db.collection(GARMIN_TOKENS_COLLECTION).document(uid).set(
        {"token_b64": token_b64, "updated_at": datetime.utcnow().isoformat()},
        merge=True,
    )

def _delete_garmin_token_from_firestore(uid: str) -> None:
    """Rimuove token Garmin da Firestore (disconnect)."""
    db.collection(GARMIN_TOKENS_COLLECTION).document(uid).delete()

# === MODELLO PER IL LOGIN DALL'APP ===
class GarminConnectRequest(BaseModel):
    uid: str
    email: str
    password: str

class GarminSyncRequest(BaseModel):
    uid: str

# === HEALTH CHECK ===
@app.get("/")
def health():
    return {
        "status": "ok",
        "service": "garmin-sync-server",
        "firestore": db is not None,
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
    health_days = _sync_daily_health(client, uid, num_days=num_days)
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

    for date_key, garmin_acts in by_date.items():
        existing_docs = _load_existing_activities_for_date(uid, date_key)
        for act in garmin_acts:
            start_raw = act.get("startTimeGMT") or act.get("startTime") or act.get("startTimeLocal") or ""
            start_dt = _parse_datetime(start_raw) or datetime.utcnow()
            incoming_type = _garmin_type_key(act)
            existing = _find_matching_activity(existing_docs, start_dt, incoming_type)
            act_id = str(act.get("activityId") or act.get("activityID"))
            doc_id = existing["id"] if existing else f"garmin_{act_id}"
            merged = _build_unified_garmin_doc(doc_id, act, start_dt, existing)
            (
                db.collection("users")
                .document(uid)
                .collection("activities")
                .document(doc_id)
                .set(merged, merge=True)
            )
            if existing is None:
                existing_docs.append(merged)
            else:
                idx = existing_docs.index(existing)
                existing_docs[idx] = merged
        _refresh_daily_log_index(uid, date_key)

    logger.info(
        f"Sync vitals ok per {uid} ({health_days} giorni health, {len(activities)} attivita)"
    )
    return {
        "success": True,
        "health_days_synced": health_days,
        "activities_synced": len(activities),
        "message": f"Aggiornati {health_days} giorni biometrici e {len(activities)} attivita.",
    }


# === ENDPOINT LOGIN GARMIN (il tasto "Connect Garmin") ===
@app.post("/garmin/connect")
async def connect_garmin(req: GarminConnectRequest):
    _require_db()
    uid = req.uid.strip()

    try:
        client = Garmin(req.email, req.password)
        # Importante: al primo login NON passare tokenstore, altrimenti la libreria prova a caricare token esistenti.
        client.login()
        token_b64 = client.garth.dumps()
        _save_garmin_token_to_firestore(uid, token_b64)

        # Marca utente come collegato su Firestore
        db.collection("users").document(uid).set({
            "garmin_linked": True,
            "garmin_linked_at": datetime.utcnow().isoformat(),
            "garmin_last_email": req.email
        }, merge=True)

        # Al login facciamo una sync iniziale leggera ma obbligatoria:
        # se non scrive almeno daily_health/activities, il login non deve risultare "ok".
        sync_result = _sync_vitals_for_client(client, uid, num_days=2, activities_limit=50)
        synced_activities = sync_result.get("activities_synced", 0)
        health_days = sync_result.get("health_days_synced", 0)

        if not sync_result.get("success", False):
            _log_garmin_comms(
                "connect_garmin.sync_after_login_failed",
                uid,
                extra=(sync_result.get("message") or "")[:400],
            )
            _store_sync_status(
                uid,
                success=False,
                message=sync_result.get("message"),
                activities_synced=synced_activities,
                health_days_synced=health_days,
            )
            logger.warning(f"Garmin collegato per {uid} ma sync fallita: {sync_result.get('message', '')}")
            return {
                "success": False,
                "message": f"Login Garmin riuscito ma sync iniziale fallita: {sync_result.get('message', '')}",
            }

        _store_sync_status(
            uid,
            success=True,
            message=sync_result.get("message"),
            activities_synced=synced_activities,
            health_days_synced=health_days,
        )
        # Aggiorna token su Firestore (garth puo' aver fatto refresh durante la sync)
        try:
            _save_garmin_token_to_firestore(uid, client.garth.dumps())
        except Exception:
            pass
        logger.success(f"✅ Garmin collegato per UID {uid} (attivita: {synced_activities}, health: {health_days} giorni)")
        return {
            "success": True,
            "message": f"Garmin collegato correttamente. Sincronizzate {synced_activities} attivita, {health_days} giorni di dati biometrici."
        }

    except (GarminConnectConnectionError, GarminConnectAuthenticationError, GarthException) as e:
        _log_garmin_comms("connect_garmin.auth_failed", uid, e)
        logger.warning(f"Login fallito per {uid} (credenziali non valide)")
        raise HTTPException(status_code=401, detail="Credenziali Garmin non valide")
    except GarthHTTPError as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        _log_garmin_comms("connect_garmin.garth_http", uid, e, extra=f"mapped_status={status}")
        if status in (401, 403):
            logger.warning(f"Login fallito per {uid} (HTTP {status})")
            raise HTTPException(status_code=401, detail="Credenziali Garmin non valide")
        if status == 429:
            logger.warning(f"Login fallito per {uid}: rate limit Garmin (429)")
            raise HTTPException(
                status_code=429,
                detail="Troppi tentativi di accesso a Garmin. Attendi 15-30 minuti e riprova.",
            )
        logger.error(f"Errore HTTP Garmin {uid}: {status} - {e}")
        raise HTTPException(status_code=500, detail="Errore interno del server")
    except Exception as e:
        def _all_messages(exc):
            msgs = [str(exc)]
            if exc.__cause__:
                msgs.append(str(exc.__cause__))
            if exc.__context__:
                msgs.append(str(exc.__context__))
            return " ".join(msgs).lower()
        err_msg = _all_messages(e)
        # 429: rate limit - NON confondere con credenziali errate
        if "429" in err_msg or "too many requests" in err_msg:
            _log_garmin_comms("connect_garmin.rate_limit", uid, e)
            logger.warning(f"Login fallito per {uid}: rate limit Garmin")
            raise HTTPException(
                status_code=429,
                detail="Troppi tentativi di accesso a Garmin. Attendi 15-30 minuti e riprova.",
            )
        auth_keywords = ("401", "unauthorized", "authentication", "login", "invalid", "credential", "password", "forbidden", "403")
        if any(kw in err_msg for kw in auth_keywords):
            _log_garmin_comms("connect_garmin.auth_keyword", uid, e)
            logger.warning(f"Login fallito per {uid}: {e}")
            raise HTTPException(status_code=401, detail="Credenziali Garmin non valide")
        _log_garmin_comms("connect_garmin.unexpected", uid, e)
        logger.error(f"Errore {uid}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Errore interno del server")

# === ENDPOINT SYNC IMMEDIATA (pull-to-refresh / login app) ===
@app.post("/garmin/sync")
async def sync_garmin(req: GarminSyncRequest):
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
async def disconnect_garmin(req: GarminSyncRequest):
    """Elimina i token Garmin da Firestore e marca l'utente come scollegato."""
    _require_db()
    uid = req.uid.strip()

    try:
        _delete_garmin_token_from_firestore(uid)
        logger.info(f"Token Garmin eliminati per {uid}")

        # 2. Aggiorna Firestore: garmin_linked = False
        db.collection("users").document(uid).set({
            "garmin_linked": False,
            "garmin_disconnected_at": datetime.utcnow().isoformat(),
        }, merge=True)

        return {
            "success": True,
            "message": "Account Garmin scollegato correttamente.",
        }
    except Exception as e:
        logger.error(f"Disconnect fallito {uid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === ENDPOINT SYNC VITALS (pull-to-refresh / post-login: biometrici + attivita) ===
@app.post("/garmin/sync-vitals")
async def sync_vitals(req: GarminSyncRequest):
    """Biometrici oggi+ieri + attivita (ultime 20). Usato per pull-to-refresh e post-login."""
    _require_db()
    uid = req.uid.strip()
    logger.info(f"📥 sync-vitals richiesta ricevuta per uid={uid[:8]}...")
    token_b64 = _get_garmin_token_from_firestore(uid)
    if not token_b64:
        logger.warning(f"sync-vitals: token non trovato per {uid[:8]}...")
        raise HTTPException(status_code=404, detail="Account Garmin non collegato. Esegui prima il login Garmin.")
    try:
        client = Garmin()
        client.login(tokenstore=token_b64)
        logger.info(f"🔗 Connesso a Garmin Connect per {uid[:8]}..., avvio sync...")
        sync_result = _sync_vitals_for_client(client, uid, num_days=2, activities_limit=50)
        _store_sync_status(
            uid,
            success=True,
            message=sync_result.get("message"),
            activities_synced=sync_result.get("activities_synced", 0),
            health_days_synced=sync_result.get("health_days_synced", 0),
        )
        # Aggiorna token su Firestore (garth puo' aver fatto refresh)
        try:
            _save_garmin_token_to_firestore(uid, client.garth.dumps())
        except Exception:
            pass
        return sync_result
    except (GarminConnectConnectionError, GarminConnectAuthenticationError, GarthException, GarthHTTPError) as e:
        _log_garmin_comms("sync_vitals.session_invalid", uid, e)
        _delete_garmin_token_from_firestore(uid)
        db.collection("users").document(uid).set({"garmin_linked": False}, merge=True)
        _store_sync_status(uid, success=False, message="Sessione Garmin scaduta.")
        logger.warning(f"Sync vitals fallito per {uid} (token non valido, rimosso)")
        raise HTTPException(status_code=401, detail="Sessione Garmin scaduta. Ricollega l'account.")
    except Exception as e:
        _log_garmin_comms("sync_vitals.error", uid, e)
        _store_sync_status(uid, success=False, message=str(e))
        logger.error(f"Sync vitals fallito {uid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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


def _existing_has_strava(data: dict | None) -> bool:
    if not data:
        return False
    return bool(
        data.get("hasStrava")
        or data.get("source") in ("strava", "dual")
        or data.get("strava_raw")
        or data.get("stravaActivityId")
    )

def _load_existing_activities_for_date(uid: str, date_key: str) -> list[dict]:
    snapshot = (
        db.collection("users")
        .document(uid)
        .collection("activities")
        .where("dateKey", "==", date_key)
        .stream()
    )
    return [{"id": doc.id, **doc.to_dict()} for doc in snapshot]

def _find_matching_activity(existing_docs: list[dict], start_dt: datetime, incoming_type: str):
    normalized_type = _normalize_activity_type(incoming_type)
    for doc in existing_docs:
        candidate_start = _parse_datetime(doc.get("startTime")) or _parse_datetime(doc.get("date"))
        if candidate_start is None:
            continue
        if abs((candidate_start - start_dt).total_seconds()) > ACTIVITY_MERGE_WINDOW_MINUTES * 60:
            continue
        candidate_type = _normalize_activity_type(doc.get("activityType"))
        if _same_activity_type(candidate_type, normalized_type):
            return doc
    return None

def _build_unified_garmin_doc(doc_id: str, act: dict, start_dt: datetime, existing: dict | None = None) -> dict:
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
        "garmin_raw": _firestore_safe_raw(act),
        "strava_raw": _firestore_safe_raw(strava_raw) if strava_raw else None,
        "raw": _firestore_safe_raw(strava_raw if has_strava else act),
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
        )
    )

def _sync_daily_health(client: Garmin, uid: str, num_days: int | None = None) -> int:
    """Estrae dati biometrici giornalieri e salva in daily_health/{date}. Ritorna numero giorni sincronizzati."""
    today = datetime.now().date()
    days = num_days if num_days is not None else DAILY_HEALTH_SYNC_DAYS
    synced_count = 0

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
                (
                    db.collection("users")
                    .document(uid)
                    .collection("daily_health")
                    .document(date_str)
                    .set(safe_data, merge=True)
                )
            (
                db.collection("users")
                .document(uid)
                .collection("daily_logs")
                .document(date_str)
                .set(
                    {
                        "date": date_str,
                        "health_ref": date_str,
                        "timestamp": datetime.utcnow(),
                    },
                    merge=True,
                )
            )
            synced_count += 1

        time.sleep(0.5)  # rate-limit API Garmin

    return synced_count

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
        health_days = _sync_daily_health(client, uid)

        # 2. activities + daily_logs.activity_ids (indice unificato)
        by_date: dict[str, list[dict]] = {}
        for act in activities:
            act_id = act.get("activityId") or act.get("activityID")
            if not act_id:
                continue
            start_raw = act.get("startTimeGMT") or act.get("startTime") or act.get("startTimeLocal") or ""
            dt = _parse_datetime(start_raw) or datetime.utcnow()
            date_key = _date_key(dt)
            by_date.setdefault(date_key, []).append(act)

        for date_key, garmin_acts in by_date.items():
            existing_docs = _load_existing_activities_for_date(uid, date_key)
            for act in garmin_acts:
                start_raw = act.get("startTimeGMT") or act.get("startTime") or act.get("startTimeLocal") or ""
                start_dt = _parse_datetime(start_raw) or datetime.utcnow()
                incoming_type = _garmin_type_key(act)
                existing = _find_matching_activity(existing_docs, start_dt, incoming_type)
                act_id = str(act.get("activityId") or act.get("activityID"))
                doc_id = existing["id"] if existing else f"garmin_{act_id}"
                merged = _build_unified_garmin_doc(doc_id, act, start_dt, existing)
                (
                    db.collection("users")
                    .document(uid)
                    .collection("activities")
                    .document(doc_id)
                    .set(merged, merge=True)
                )
                if existing is None:
                    existing_docs.append(merged)
                else:
                    idx = existing_docs.index(existing)
                    existing_docs[idx] = merged
            _refresh_daily_log_index(uid, date_key)

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
        db.collection("users").document(uid).set({"garmin_linked": False}, merge=True)
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
    users = list(db.collection("users").where("garmin_linked", "==", True).stream())
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
async def trigger_scheduled_sync(x_cron_secret: str | None = Header(default=None)):
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
