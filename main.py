import json
import os
import time
from datetime import datetime
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
    os.environ["GARMINTOKENS"] = os.path.abspath(token_subdir)   # <-- importante per il tuo setup

    try:
        client = Garmin(req.email, req.password)
        client.login()   # salva token automaticamente

        # Marca utente come collegato su Firestore
        db.collection("users").document(uid).set({
            "garmin_linked": True,
            "garmin_linked_at": datetime.utcnow().isoformat(),
            "garmin_last_email": req.email
        }, merge=True)

        logger.success(f"✅ Garmin collegato per UID {uid}")
        return {"success": True, "message": "Garmin collegato correttamente!"}

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

# === SYNC PER UTENTE (usa token salvato) ===
def sync_user(uid: str):
    token_subdir = os.path.join(TOKENS_DIR, uid)
    if not os.path.isdir(token_subdir):
        return
    os.environ["GARMINTOKENS"] = os.path.abspath(token_subdir)
    try:
        garth.resume()
        client = Garmin()   # usa token

        today = datetime.now().strftime("%Y-%m-%d")
        activities = client.get_activities(0, 20)

        batch = db.batch()
        prefix = f"users/{uid}"
        for act in activities:
            doc_ref = db.collection(f"{prefix}/garmin_activities").document(str(act.get("activityId")))
            batch.set(doc_ref, {**act, "syncedAt": datetime.utcnow().isoformat()}, merge=True)

        daily_ref = db.collection(f"{prefix}/garmin_daily").document(today)
        batch.set(daily_ref, {"date": today, "syncedAt": datetime.utcnow().isoformat()}, merge=True)
        batch.commit()

        logger.success(f"Sync ok per {uid}")
    except Exception as e:
        logger.error(f"Sync fallito {uid}: {e}")

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
