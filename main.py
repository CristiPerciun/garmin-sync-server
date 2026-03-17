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
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="Garmin Sync - FitAI Analyzer")
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
            raise ValueError("FIREBASE_CREDENTIALS_B64 non valido. Riesegui .\\set-firebase-secret.ps1")

    logger.error("Manca FIREBASE_CREDENTIALS o FIREBASE_CREDENTIALS_B64")
    raise ValueError("Esegui: .\\set-firebase-secret.ps1")

cred = _load_firebase_cred()

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

logger.add(os.path.join(BASE_DIR, "garmin.log"), rotation="10 MB", level="INFO")

# Usa sempre un path assoluto: su Fly il volume e' montato in /app/tokens.
TOKENS_DIR = os.getenv("GARMINTOKENS_ROOT", os.path.join(BASE_DIR, "tokens"))
os.makedirs(TOKENS_DIR, exist_ok=True)

# === MODELLO PER IL LOGIN DALL'APP ===
class GarminConnectRequest(BaseModel):
    uid: str
    email: str
    password: str

class GarminSyncRequest(BaseModel):
    uid: str

# === HEALTH CHECK (Fly.io, load balancer) ===
@app.get("/")
def health():
    return {"status": "ok", "service": "garmin-sync-server"}

# === ENDPOINT LOGIN GARMIN (il tasto "Connect Garmin") ===
@app.post("/garmin/connect")
async def connect_garmin(req: GarminConnectRequest):
    uid = req.uid.strip()
    token_subdir = os.path.join(TOKENS_DIR, uid)
    os.makedirs(token_subdir, exist_ok=True)

    try:
        client = Garmin(req.email, req.password)
        # Importante: al primo login NON passare tokenstore/GARMINTOKENS,
        # altrimenti la libreria prova a caricare token gia' esistenti.
        client.login()
        client.garth.dump(os.path.abspath(token_subdir))

        # Marca utente come collegato su Firestore
        db.collection("users").document(uid).set({
            "garmin_linked": True,
            "garmin_linked_at": datetime.utcnow().isoformat(),
            "garmin_last_email": req.email
        }, merge=True)

        sync_result = sync_user(uid, client)
        synced_activities = sync_result["activities_synced"]
        health_days = sync_result.get("health_days_synced", 0)
        logger.success(f"✅ Garmin collegato per UID {uid} (attivita: {synced_activities}, health: {health_days} giorni)")
        return {
            "success": True,
            "message": f"Garmin collegato correttamente. Sincronizzate {synced_activities} attivita, {health_days} giorni di dati biometrici."
        }

    except (GarminConnectConnectionError, GarminConnectAuthenticationError, GarthException):
        logger.warning(f"Login fallito per {uid} (credenziali non valide)")
        raise HTTPException(status_code=401, detail="Credenziali Garmin non valide")
    except GarthHTTPError as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (401, 403):
            logger.warning(f"Login fallito per {uid} (HTTP {status})")
            raise HTTPException(status_code=401, detail="Credenziali Garmin non valide")
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
        auth_keywords = ("401", "unauthorized", "authentication", "login", "invalid", "credential", "password", "forbidden", "403", "client error")
        if any(kw in err_msg for kw in auth_keywords):
            logger.warning(f"Login fallito per {uid}: {e}")
            raise HTTPException(status_code=401, detail="Credenziali Garmin non valide")
        logger.error(f"Errore {uid}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Errore interno del server")

# === ENDPOINT SYNC IMMEDIATA (pull-to-refresh / login app) ===
@app.post("/garmin/sync")
async def sync_garmin(req: GarminSyncRequest):
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

# === ENDPOINT SYNC VITALS (pull-to-refresh: solo oggi e ieri) ===
@app.post("/garmin/sync-vitals")
async def sync_vitals(req: GarminSyncRequest):
    """Forza il download dei dati biometrici Garmin di oggi e ieri. Leggero, per pull-to-refresh."""
    uid = req.uid.strip()
    token_subdir = os.path.join(TOKENS_DIR, uid)
    if not os.path.isdir(token_subdir):
        raise HTTPException(status_code=404, detail="Account Garmin non collegato. Esegui prima il login Garmin.")
    try:
        client = Garmin()
        client.login(tokenstore=os.path.abspath(token_subdir))
        health_days = _sync_daily_health(client, uid, num_days=2)
        logger.info(f"Sync vitals ok per {uid} ({health_days} giorni)")
        return {
            "success": True,
            "health_days_synced": health_days,
            "message": f"Aggiornati {health_days} giorni di dati biometrici (oggi e ieri)."
        }
    except (GarminConnectConnectionError, GarminConnectAuthenticationError, GarthException):
        logger.warning(f"Sync vitals fallito per {uid} (credenziali scadute)")
        raise HTTPException(status_code=401, detail="Sessione Garmin scaduta. Ricollega l'account.")
    except Exception as e:
        logger.error(f"Sync vitals fallito {uid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === SYNC DAILY HEALTH (passi, sonno, HRV, Body Battery) ===
DAILY_HEALTH_SYNC_DAYS = 14  # Ultimi N giorni da sincronizzare (sync full)

def _sync_daily_health(client: Garmin, uid: str, num_days: int | None = None) -> int:
    """Estrae dati biometrici giornalieri e salva in daily_health/{date}. Ritorna numero giorni sincronizzati."""
    prefix = f"users/{uid}/daily_health"
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
        if len(doc_data) > 2:
            db.collection(prefix).document(date_str).set(doc_data, merge=True)
            synced_count += 1

        time.sleep(0.5)  # rate-limit API Garmin

    return synced_count

# === SYNC PER UTENTE (usa client attivo o token salvato) ===
def sync_user(uid: str, client: Garmin | None = None):
    token_subdir = os.path.join(TOKENS_DIR, uid)
    if client is None and not os.path.isdir(token_subdir):
        return {
            "success": False,
            "activities_synced": 0,
            "health_days_synced": 0,
            "message": "Account Garmin non collegato. Esegui prima il login Garmin."
        }
    try:
        if client is None:
            client = Garmin()
            client.login(tokenstore=os.path.abspath(token_subdir))

        today = datetime.now().strftime("%Y-%m-%d")
        activities = client.get_activities(0, 20)
        if not isinstance(activities, list):
            activities = []

        prefix = f"users/{uid}"
        batch = db.batch()

        # 1. Salva in garmin_activities (merge - evita duplicati per activityId)
        for act in activities:
            act_id = act.get("activityId") or act.get("activityID")
            if not act_id:
                continue
            start_raw = act.get("startTimeGMT") or act.get("startTime") or act.get("startTimeLocal") or ""
            try:
                dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00")) if start_raw else datetime.utcnow()
            except Exception:
                dt = datetime.utcnow()
            date_key = dt.strftime("%Y-%m-%d")
            act_type = act.get("activityType")
            type_key = act_type.get("typeKey", "") if isinstance(act_type, dict) else str(act_type or "")
            doc_data = {
                **act,
                "syncedAt": datetime.utcnow().isoformat(),
                "startTime": start_raw or act.get("startTimeGMT"),
                "dateKey": date_key,
                "activityTypeKey": type_key,
                "source": "garmin",
            }
            doc_ref = db.collection(f"{prefix}/garmin_activities").document(str(act_id))
            batch.set(doc_ref, doc_data, merge=True)

        # 2. garmin_daily (Livello 1 - daily stats)
        daily_ref = db.collection(f"{prefix}/garmin_daily").document(today)
        batch.set(daily_ref, {"date": today, "syncedAt": datetime.utcnow().isoformat()}, merge=True)
        batch.commit()

        # 2b. daily_health: passi, sonno, HRV, Body Battery (get_stats, get_sleep_data, get_hrv_data, get_body_battery)
        health_days = _sync_daily_health(client, uid)

        # 3. daily_logs (Livello 1): SOLO garmin_activities. merge=True: NON toccare strava_activities.
        #    Strava scrive da app Flutter (StravaService), Garmin scrive da questo server.
        by_date: dict[str, list[dict]] = {}
        for act in activities:
            act_id = act.get("activityId") or act.get("activityID")
            if not act_id:
                continue
            start_raw = act.get("startTimeGMT") or act.get("startTime") or act.get("startTimeLocal") or ""
            try:
                dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00")) if start_raw else datetime.utcnow()
            except Exception:
                dt = datetime.utcnow()
            date_key = dt.strftime("%Y-%m-%d")
            by_date.setdefault(date_key, []).append(act)

        for date_key, garmin_acts in by_date.items():
            daily_log_ref = db.collection(f"{prefix}/daily_logs").document(date_key)
            # Formato Garmin nativo + campi normalizzati per query/deduplicazione
            garmin_for_log = []
            for a in garmin_acts:
                at = a.get("activityType")
                tk = at.get("typeKey", "") if isinstance(at, dict) else str(at or "")
                garmin_for_log.append({**a, "source": "garmin", "dateKey": date_key, "activityTypeKey": tk})
            update = {
                "date": date_key,
                "garmin_activities": garmin_for_log,
                "timestamp": datetime.utcnow(),
            }
            daily_log_ref.set(update, merge=True)

        logger.success(f"Sync ok per {uid} ({len(activities)} attivita, {health_days} giorni health)")
        return {
            "success": True,
            "activities_synced": len(activities),
            "health_days_synced": health_days,
            "message": "Sync completata"
        }
    except Exception as e:
        logger.error(f"Sync fallito {uid}: {e}")
        return {
            "success": False,
            "activities_synced": 0,
            "health_days_synced": 0,
            "message": str(e),
        }

# === SCHEDULER (multi-utente) ===
def scheduled_sync():
    logger.info("Inizio sync batch...")
    users = db.collection("users").where("garmin_linked", "==", True).stream()
    for user in users:
        sync_user(user.id)
        time.sleep(10)   # rate-limit Garmin

# === AVVIO (compatibile Fly.io) ===
if __name__ == "__main__":
    logger.info("🚀 Server avviato con API + sync")

    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_sync, "interval", minutes=45)
    scheduler.start()

    # Health check + API: Fly.io usa internal_port 8080, imposta PORT=8080
    port = int(os.getenv("PORT", "8080"))
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
