# Deploy garmin-sync-server su Render

## 1. Variabili d'ambiente obbligatorie

Il deploy fallisce con `Manca FIREBASE_CREDENTIALS o FIREBASE_CREDENTIALS_B64` se non configuri le credenziali Firebase.

### FIREBASE_CREDENTIALS_B64 (consigliato)

1. **Genera il valore Base64** dal file `firebase-service-account.json`:

   **PowerShell (Windows):**
   ```powershell
   $jsonBytes = [System.IO.File]::ReadAllBytes("firebase-service-account.json")
   [Convert]::ToBase64String($jsonBytes)
   ```
   Copia l'output (stringa lunga).

   **Bash (Linux/Mac):**
   ```bash
   base64 -w 0 firebase-service-account.json
   ```

2. **Su Render** → Dashboard → tuo servizio → **Environment** → **Add Environment Variable**
   - **Key:** `FIREBASE_CREDENTIALS_B64`
   - **Value:** incolla la stringa Base64 generata sopra
   - Marca come **Secret** (opzionale ma consigliato)

3. **Redeploy** il servizio (Render lo farà automaticamente se "Auto-Deploy" è attivo).

### Alternativa: FIREBASE_CREDENTIALS (JSON raw)

- **Key:** `FIREBASE_CREDENTIALS`
- **Value:** contenuto completo del file `firebase-service-account.json` come stringa (una riga, senza newline)
- Attenzione: su alcune piattaforme i caratteri speciali possono dare problemi → preferisci `FIREBASE_CREDENTIALS_B64`.

---

## 2. Altre variabili utili

| Variabile | Descrizione | Esempio |
|----------|-------------|---------|
| `CRON_SECRET` | Secret per endpoint `/internal/scheduled-sync` (cron esterno) | stringa casuale |
| `GARTH_HOME` | Path token Garmin (Render usa `/app/garth_tokens`) | `/app/garth_tokens` |
| `PORT` | Porta (Render imposta automaticamente) | — |

---

## 3. Aggiorna l'app FitAI Analyzer

Dopo il deploy, l'URL del server sarà simile a:
`https://<nome-servizio>.onrender.com`

Aggiorna `lib/services/garmin_service.dart`:

```dart
const String garminServerUrl = 'https://<tuo-servizio>.onrender.com';
```

---

## 4. Volume persistente (token Garmin)

Render supporta **Disks** per dati persistenti. Se usi un disco:

- **Mount Path:** `/app/tokens` (o `GARMINTOKENS_ROOT` se diverso)
- Senza disco, i token Garmin si perdono ad ogni redeploy (gli utenti dovranno ricollegare l'account).

---

## 5. Verifica

- **Logs** su Render: dovresti vedere `🚀 Server avviato con API + scheduler sync ogni 45 min`
- **Health check:** `GET https://<tuo-servizio>.onrender.com/` → `{"status":"ok","service":"garmin-sync-server"}`
