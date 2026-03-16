import json
import os
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore
from garminconnect import Garmin, GarminConnectConnectionError
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

load_dotenv()

# Garth/Garmin token store – usa GARTH_HOME per persistenza su Railway/volume
# garminconnect legge GARMINTOKENS come path della cartella token
garth_home = os.getenv("GARTH_HOME", "./garth_tokens")
if not os.path.isabs(garth_home):
    garth_home = os.path.abspath(garth_home)
os.makedirs(garth_home, exist_ok=True)
os.environ["GARMINTOKENS"] = garth_home

# === CONFIG ===
USER_ID = os.getenv("USER_ID")
if not USER_ID:
    logger.error("USER_ID mancante in .env!")
    exit(1)

# === FIREBASE ===
# Supporta file locale O variabile FIREBASE_CREDENTIALS (JSON) per Railway/cloud
firebase_creds = os.getenv("FIREBASE_CREDENTIALS")
if firebase_creds:
    cred = credentials.Certificate(json.loads(firebase_creds))
elif os.path.exists("firebase-service-account.json"):
    cred = credentials.Certificate("firebase-service-account.json")
else:
    logger.error("Serve firebase-service-account.json o variabile FIREBASE_CREDENTIALS")
    exit(1)

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# === LOGGING (best practice) ===
logger.add("garmin_sync.log", rotation="10 MB", level="INFO")


# === GARMIN CLIENT ===
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type(GarminConnectConnectionError),
)
def get_garmin_client() -> Garmin:
    client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    client.login()
    return client


# === SYNC PRINCIPALE ===
def sync_garmin_data() -> None:
    logger.info(f"[{datetime.now()}] Inizio sincronizzazione Garmin...")
    try:
        client = get_garmin_client()

        today = datetime.now().strftime("%Y-%m-%d")

        # Dati principali (best practice: solo ultimi 30 giorni per non sovraccaricare)
        activities = client.get_activities(0, 30)
        stats = client.get_stats(today)
        hr = client.get_heart_rates(today)
        sleep = client.get_sleep_data(today)

        batch = db.batch()
        collection_prefix = f"users/{USER_ID}"

        # Attività
        for act in activities:
            doc_ref = db.collection(f"{collection_prefix}/garmin_activities").document(
                str(act["activityId"])
            )
            batch.set(
                doc_ref,
                {
                    "activityId": act["activityId"],
                    "startTime": act["startTimeGMT"],
                    "activityType": act["activityType"]["typeKey"],
                    "distance": act.get("distance"),
                    "duration": act.get("duration"),
                    "averageHR": act.get("averageHR"),
                    "calories": act.get("calories"),
                    "rawData": act,
                    "syncedAt": datetime.utcnow().isoformat(),
                },
                merge=True,
            )

        # Dati giornalieri
        daily_ref = db.collection(f"{collection_prefix}/garmin_daily").document(today)
        batch.set(
            daily_ref,
            {
                "date": today,
                "stats": stats,
                "heartRate": hr,
                "sleep": sleep,
                "syncedAt": datetime.utcnow().isoformat(),
            },
            merge=True,
        )

        batch.commit()
        logger.success(
            f"[{datetime.now()}] Sync completato! {len(activities)} attività salvate."
        )

    except Exception as e:  # noqa: BLE001
        logger.error(f"Errore sync: {e}")


# === HEALTH CHECK (per Fly.io / cloud: risponde su PORT) ===
def run_health_server() -> None:
    """Server HTTP minimale per health check – Fly.io richiede risposta su PORT."""
    port = int(os.getenv("PORT", 8080))

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args: object) -> None:
            pass  # silenzia log HTTP

    with HTTPServer(("", port), HealthHandler) as httpd:
        logger.info(f"Health check su :{port}")
        httpd.serve_forever()


# === SCHEDULER ===
if __name__ == "__main__":
    logger.info("Server Garmin Sync avviato")

    # Avvia health server in background (per Fly.io)
    threading.Thread(target=run_health_server, daemon=True).start()

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        sync_garmin_data,
        "interval",
        minutes=int(os.getenv("SYNC_INTERVAL_MINUTES", 45)),
        id="garmin_sync",
        replace_existing=True,
    )
    scheduler.start()

    # Mantieni vivo + graceful shutdown
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logger.info("Server fermato correttamente")
