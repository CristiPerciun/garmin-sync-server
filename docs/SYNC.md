# Sincronizzazione (Garmin + Strava)

La descrizione **unificata** (app Flutter + questo server + Firestore + flussi deprecati) è nel repo **FitAI Analyzer**:

**`FitAI Analyzer/docs/SYNC_ARCHITECTURE.md`** (nel clone accanto a questo repo, o dove tieni l’app Flutter).

In questo repository: implementazione in **`main.py`** (FastAPI, merge, backfill, delta) e **`strava_sync.py`** (client HTTP Strava).
