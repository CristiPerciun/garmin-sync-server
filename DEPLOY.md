# Deploy su Railway

## 1. Push su GitHub

```bash
git init
git add .
git commit -m "Initial commit: Garmin sync server"
git remote add origin https://github.com/CristiPerciun/garmin-sync-server.git
git branch -M main
git push -u origin main
```

> `.env` e `firebase-service-account.json` sono esclusi da `.gitignore` – non verranno committati.

---

## 2. Crea progetto su Railway

1. Vai su [railway.app](https://railway.app) e accedi con GitHub
2. **New Project** → **Deploy from GitHub repo**
3. Seleziona `CristiPerciun/garmin-sync-server`
4. Railway rileva automaticamente Python e usa `requirements.txt`

---

## 3. Variabili d’ambiente

In **Variables** aggiungi tutte le variabili dal tuo `.env`:

| Variabile | Valore |
|-----------|--------|
| `GARMIN_EMAIL` | email Garmin Connect |
| `GARMIN_PASSWORD` | password Garmin |
| `USER_ID` | `ejpP1HTRaeajZ2k72Tkqs36L0bp2` |
| `SYNC_INTERVAL_MINUTES` | `45` |
| `GARTH_HOME` | `/app/garth_tokens` (path del volume) |

### Firebase Credentials

**Opzione A – Variabile (consigliata)**  
Aggiungi `FIREBASE_CREDENTIALS` con il contenuto JSON del file `firebase-service-account.json` (come stringa):

```
{"type":"service_account","project_id":"fit-ai-analyzer",...}
```

**Opzione B – File**  
Se usi il file locale, non è possibile caricarlo su Railway. Usa la variabile `FIREBASE_CREDENTIALS`.

---

## 4. Volume persistente

1. Nel tuo servizio → **Settings** → **Volumes**
2. **Add Volume**
3. **Mount Path:** `/app/garth_tokens`
4. Salva

Il volume mantiene i token Garmin tra i deploy e i riavvii.

---

## 5. Deploy

Railway avvia automaticamente il build e il deploy. Il server resta attivo e sincronizza ogni `SYNC_INTERVAL_MINUTES` minuti.

---

## Verifica

- **Logs** → vedi output di loguru e messaggi di sync
- **Deployments** → storico delle deploy
