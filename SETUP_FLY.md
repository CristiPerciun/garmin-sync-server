# Setup Fly.io - garmin-sync-server

## Script automatico (PowerShell)

Con `firebase-service-account.json` nella cartella del progetto:

```powershell
.\setup-fly.ps1
```

Richiede [flyctl](https://fly.io/docs/hands-on/install-flyctl/) installato e `fly auth login`.

---

## Setup manuale

## 1. FIREBASE_CREDENTIALS (obbligatorio)

Il server richiede le credenziali Firebase per scrivere su Firestore.

### Opzione A: Da file locale (PowerShell)

```powershell
cd C:\Users\c.perciun\Documents\Custom_WorkSpace\garmin-sync-server
fly secrets set FIREBASE_CREDENTIALS="$(Get-Content firebase-service-account.json -Raw)"
```

### Opzione B: Da file locale (Bash/WSL)

```bash
fly secrets set FIREBASE_CREDENTIALS="$(cat firebase-service-account.json)"
```

### Opzione C: Fly.io Dashboard

1. Vai su https://fly.io/apps/garmin-sync-server
2. **Secrets** → **Set secret**
3. Nome: `FIREBASE_CREDENTIALS`
4. Valore: incolla il contenuto JSON di `firebase-service-account.json`

---

## 2. Volume per token Garmin (consigliato)

Senza volume, i token si perdono a ogni restart e gli utenti devono riconnettersi.

### Crea i volumi (una sola volta)

L'app richiede **2 volumi** (2 machine in iad):

```bash
fly volumes create garmin_tokens --region iad --count 2
```

Poi esegui `fly deploy`.

### Verifica

```bash
fly volumes list
```

Il `fly.toml` ha già `[mounts]` configurato. Dopo aver creato il volume, il prossimo `fly deploy` lo monterà su `/app/tokens`.

---

## 3. Cold start (nessuna azione)

Con `auto_stop_machines = 'stop'`, la prima richiesta dopo inattività può richiedere 30-60 secondi. L'app Flutter ha timeout 60s.

---

## Ordine consigliato

1. `fly auth login` (se non già autenticato)
2. `fly secrets set FIREBASE_CREDENTIALS=...`
3. `fly volumes create garmin_tokens --region iad --size 1`
4. `fly deploy`
