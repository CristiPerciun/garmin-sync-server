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

## Raspberry Pi e log

Deploy automatico da GitHub: vedi **`RPI_DEPLOY.md`**.  
Log diagnostici verso Garmin (circa **1 giorno** su disco): `logs/garmin_comms.log` (oltre a `garmin.log` e `journalctl -u garmin-sync`).
