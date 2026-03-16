import json
import os
import time
from datetime import datetime
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore
from garminconnect import Garmin, GarminConnectConnectionError
import garth
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="Garmin Sync - FitAI Analyzer")

# === FIREBASE (uguale al tuo vecchio codice) ===
firebase_creds = os.getenv("FIREBASE_CREDENTIALS")
if firebase_creds:
    cred = credentials.Certificate(json.loads(firebase_creds))
elif os.path.exists("firebase-service-account.json"):
    cred = credentials.Certificate("firebase-service-account.json")
else:
    logger.error("Manca firebase-service-account.json o FIREBASE_CREDENTIALS")
    exit(1)

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

logger.add("garmin.log", rotation="10 MB", level="INFO")

TOKENS_DIR = "./tokens"
os.makedirs(TOKENS_DIR, exist_ok=True)

# === MODELLO PER IL LOGIN DALL'APP ===
class GarminConnectRequest(BaseModel):
    uid: str
    email: str
    password: str

# === ENDPOINT CHE VUOI (il tasto "Connect Garmin") ===
@app.post("/garmin/connect")
async def connect_garmin(req: GarminConnectRequest):
    uid = req.uid.strip()
    token_subdir = os.path.join(TOKENS_DIR, uid)
    os.makedirs(token_subdir, exist_ok=True)
    os.environ["GARMINTOKENS"] = token_subdir   # <-- importante per il tuo setup

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

    except GarminConnectConnectionError:
        logger.warning(f"Login fallito per {uid}")
        raise HTTPException(status_code=401, detail="Credenziali Garmin non valide")
    except Exception as e:
        logger.error(f"Errore {uid}: {e}")
        raise HTTPException(status_code=500, detail="Errore interno del server")

# === SYNC PER UTENTE (usa token salvato) ===
def sync_user(uid: str):
    token_subdir = os.path.join(TOKENS_DIR, uid)
    if not os.path.exists(token_subdir):
        return
    os.environ["GARMINTOKENS"] = token_subdir
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

    # Health check + API su PORT di Fly.io
    port = int(os.getenv("PORT", 8000))
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
