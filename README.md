# garmin-sync-server

Sincronizza dati da Garmin Connect (garth) e opzionalmente Firebase.

## Struttura

```
garmin-sync-server/
├── .env                  ← segreti (NON commitare!)
├── .env.example          ← template per .env
├── requirements.txt
├── main.py               ← cuore
├── Dockerfile
├── .dockerignore
├── garth_tokens/         ← token Garmin persistenti (vuota all’inizio)
└── firebase-service-account.json  ← da Firebase Console (NON commitare!)
```

## Setup

1. **Copia e compila `.env`**
   ```bash
   cp .env.example .env
   ```
   Inserisci `GARMIN_EMAIL` e `GARMIN_PASSWORD` in `.env`.

2. **Firebase**  
   Scarica la chiave di servizio da [Firebase Console](https://console.firebase.google.com) → Impostazioni progetto → Account di servizio → Genera nuova chiave privata e salvala come `firebase-service-account.json` nella root del progetto.

3. **Dipendenze**
   ```bash
   pip install -r requirements.txt
   ```

4. **Esecuzione**
   ```bash
   python main.py
   ```

## Docker

```bash
docker build -t garmin-sync-server .
docker run --env-file .env -v "$(pwd)/garth_tokens:/app/garth_tokens" garmin-sync-server
```

Monta anche `firebase-service-account.json` se usi Firebase:
`-v "$(pwd)/firebase-service-account.json:/app/firebase-service-account.json"`

## Documentazione sync

- **Indice / architettura unificata (app + server)**: nel repo FitAI Analyzer, file **`docs/SYNC_ARCHITECTURE.md`**.  
- **Puntatore da questo repo**: **`docs/SYNC.md`**.

## API (FitAI Analyzer)

| Endpoint | Scopo |
|----------|--------|
| `GET /` | Health: `status`, `service`, `firestore`, **`version`** (stringa in `main.py` → `SERVER_VERSION`; incrementala tu a ogni push per verificare il deploy sul Pi). |
| `POST /garmin/connect` | Login; risposta rapida con `backfillQueued`. Backfill **~BACKFILL_DAYS** (default 60) in **thread** → `users/{uid}/sync_status/backfill`, `daily_health`, `activities`, `lastSuccessfulSync`. |
| `POST /garmin/sync-today` | Pull-to-refresh: oggi+ieri + attività recenti (come l’ex sync-vitals leggera). |
| `POST /garmin/sync-vitals` | Stesso handler di `sync-today` (compat). |
| `POST /sync/delta` | Delta Garmin + Strava da `lastSuccessfulSync` (body JSON). |
| `POST /strava/register-tokens` | Salva token in `strava_tokens/{uid}`; backfill Strava in background. |
| `POST /strava/disconnect` | Elimina token server-side. |
| `POST /garmin/activity-detail` | Dettaglio attività Garmin o Strava on-demand. |

Variabili: `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `BACKFILL_DAYS`, `GARMIN_BACKFILL_BATCH_DAYS`, opz. `GARMIN_SERVER_BEARER_TOKEN` (allineato all’app). In caso di errori Garmin, cerca `backfill.*` / `connect_garmin.*` in `logs/garmin_comms.log`.

### Login Garmin: “credenziali non valide” ma sul sito funzionano?

La libreria [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) usa [Garth](https://github.com/matin/garth) (stesso flusso OAuth dell’app ufficiale). Non sempre un fallimento è password errata:

- **HTTP 503 / messaggio “SSO” o “preauthorized” o “oauth-service”**: Garmin ha accettato le credenziali ma lo scambio token è fallito — spesso temporaneo; aggiorna dipendenze sul Pi: `pip install -U garminconnect garth` e riprova dopo 15–30 minuti.
- **Account con MFA/2FA**: serve supporto MFA nella libreria (`prompt_mfa` / `resume_login`); il nostro endpoint oggi invia solo email+password — se Garmin richiede il secondo fattore, il login può fallire anche con password corretta.
- **Sul Pi**: esegui `pip install -r requirements.txt` dopo il pull per allineare `garminconnect`/`garth`.

## Raspberry Pi e log

Deploy automatico da GitHub: vedi **`RPI_DEPLOY.md`**.  
Branch **`fork-sync`** + aggiornamento automatico sul Pi: **`docs/WORKFLOW_FORK_SYNC.md`**, script **`deploy/rpi/setup_fork_sync_branch.sh`**.  
Log diagnostici verso Garmin (circa **1 giorno** su disco): `logs/garmin_comms.log` (oltre a `garmin.log` e `journalctl -u garmin-sync`).
