# Setup Raspberry Pi 3 B+ per progetto Python Git Sync

**Target:** Ubuntu 32-bit su Raspberry Pi 3 B+ (1GB RAM, spazio limitato)

**Utente SSH:** `cperciun`  
**IP (es. hotspot):** `172.20.10.4` (IPv4) — oppure IPv6 se il v4 non risponde: `2a02:b025:14:79c8:c244:4ff3:3191:33d2`  
La password non va committata: usa SSH interattivo oppure `$env:RPI_SSH_PASSWORD` con `run_remote_prep.py` / `finish_garmin_on_pi.py`.

---

## Connessione

Da un PC sulla **stessa rete** del Raspberry Pi:

```bash
ssh cperciun@172.20.10.4
```

### Hotspot: `Connection reset` / `client_loop: send disconnect`

Su hotspot il NAT spesso **chiude sessioni SSH** inattive o lunghe. Non è un IP sbagliato: conviene tenere viva la sessione.

**Windows — client OpenSSH** (`~/.ssh/config`, vedi anche `ssh_config_pi_hotspot.example`):

```sshconfig
Host pi-hotspot
    HostName 172.20.10.4
    User cperciun
    ServerAliveInterval 25
    ServerAliveCountMax 6
    TCPKeepAlive yes
```

Poi: `ssh pi-hotspot`

**Sul Pi** (una volta): dalla cartella `rpi_setup` copiata sul Pi, `sudo bash 05_sshd_hotspot_keepalive.sh` (oppure applica a mano `ClientAliveInterval 30` e `ClientAliveCountMax 6` in `/etc/ssh/sshd_config` e `sudo systemctl restart ssh`).

Messaggio *“host key is known by … 10.15.22.3”*: è normale se è **lo stesso Pi** che prima aveva un altro IP; hai già accettato la nuova voce per `172.20.10.4`.

---

## Automazione da Windows (consigliato)

Dopo `pip install paramiko` sulla macchina Windows (stessa rete del Pi):

```powershell
cd scripts\rpi_setup
$env:RPI_SSH_PASSWORD = "tua_password"
python run_remote_prep.py
```

Opzionale: `$env:RPI_HOST = "172.20.10.4"`; se fallisce: `$env:RPI_HOST6 = "2a02:b025:14:79c8:c244:4ff3:3191:33d2"` per `finish_garmin_on_pi.py`.

---

## Procedura manuale (sul Raspberry Pi)

### 1. Copia gli script sul Raspberry Pi

Dal tuo PC Windows (PowerShell):

```powershell
scp -r scripts/rpi_setup cperciun@172.20.10.4:~/
```

### 2. Verifica sistema (disco e memoria)

```bash
cd ~/rpi_setup
chmod +x *.sh
bash 01_check_system.sh
```

- **Spazio:** sotto ~1GB liberi su `/` lo script si ferma; 2–3GB+ sono consigliati per margine
- **RAM:** 1GB (RPi 3 B+). Lo script 02 prepara uno swap se necessario.

### 3. Prepara ambiente (richiede sudo)

```bash
sudo bash 02_prepare_environment.sh
```

Installa:

- `git`
- `python3`
- `python3-pip`
- `python3-venv`
- `ca-certificates`
- `openssh-client`

Configura swap se la RAM è inferiore a 1.5GB.

### 4. (Opzionale) Liberare spazio — office / mail / torrent

Su immagini **minime** (es. Raspberry Pi OS Lite) spesso **non** ci sono LibreOffice, Thunderbird o Transmission: non guadagni nulla.

Attenzione: comandi del tipo `apt purge libreoffice*` espandono `*` come **nomi di file** nella directory corrente, non come elenco pacchetti. Meglio:

```bash
sudo bash 04_purge_desktop_bulk.sh
```

Poi: `sudo apt-get autoremove -y --purge && sudo apt-get clean`.

### 5. Garmin Sync Server (Python su Pi)

Il server **garmin-sync-server** ha script dedicati nel [repo GitHub](https://github.com/CristiPerciun/garmin-sync-server): cartella `deploy/rpi/` e guida `RPI_DEPLOY.md` (clone, `sudo bash deploy/rpi/install.sh`, timer git ogni 3 min).

Da Windows (dopo `pip install paramiko`):

- **`finish_garmin_on_pi.py`**: `git pull`, `complete_pip.sh`, systemd, verifica HTTP. Retry SSH + fallback IPv6 se imposti `RPI_HOST6`.
- **`deploy_garmin_sync_pi.py`**: primo deploy (clone + `install.sh`).

```powershell
cd scripts\rpi_setup
$env:RPI_SSH_PASSWORD = "..."
$env:RPI_HOST6 = "2a02:b025:14:79c8:c244:4ff3:3191:33d2"   # opzionale se 172.20.10.4 non va
python finish_garmin_on_pi.py
```

### 5b. Firebase: `FIREBASE_CREDENTIALS_B64` e lettura/scrittura Firestore

**Dove è definito (codice)**

| Progetto | Cosa guardare |
|----------|----------------|
| **garmin-sync-server** (GitHub) | `main.py`: funzione `_load_firebase_cred()` — legge `FIREBASE_CREDENTIALS` (JSON) o **`FIREBASE_CREDENTIALS_B64`** (consigliato). `.env.example` nella root del repo elenca le variabili. |
| **Deploy Pi** | `deploy/rpi/garmin-sync.service`: `EnvironmentFile=-/home/cperciun/garmin-sync-server/.env` — systemd carica il `.env` prima di avviare uvicorn. |
| **Documentazione deploy** | Sul repo: `RPI_DEPLOY.md` (installazione Pi + git pull), `encode_firebase_credentials_b64.ps1` per Base64 Firebase. |

**Non** usare `android/app/google-services.json` come credenziale server: serve il **file chiave Account di servizio** (JSON) da Firebase Console.

**Generare la chiave e il Base64**

1. [Firebase Console](https://console.firebase.google.com/) → progetto (es. **helpful-silo-473610-h6** se condividi lo stesso progetto dell’app **FitAI Analyzer**).
2. ⚙️ Impostazioni progetto → **Account di servizio** → **Genera nuova chiave privata** → scarica il `.json`.
3. In GCP, per quel service account sono tipici ruoli tipo *Firebase Admin SDK Administrator Service Agent* / accesso a Firestore: la chiave predefinita **firebase-adminsdk-…** consente già lettura/scrittura tramite Admin SDK.
4. Su Windows, dalla cartella `scripts\rpi_setup`:

   ```powershell
   .\encode_firebase_credentials_b64.ps1 -JsonPath "C:\percorso\firebase-adminsdk-xxxxx.json"
   ```

   Copia la riga `FIREBASE_CREDENTIALS_B64=...` oppure usa lo script Python sotto.

**Sul Raspberry Pi**

- Crea/aggiorna `~/garmin-sync-server/.env` con quella riga (file **600**, non in git).
- Riavvio: `sudo systemctl restart garmin-sync`
- Verifica: `curl -s http://127.0.0.1:8080/` → JSON con **`"firestore":true`**.

**Da Windows (automazione)**

```powershell
cd scripts\rpi_setup
$env:RPI_SSH_PASSWORD = "..."
$env:FIREBASE_CREDENTIALS_B64 = "<incolla base64>"
python push_firebase_env_to_pi.py
# oppure: python push_firebase_env_to_pi.py --json C:\path\chiave.json
```

**Lettura/scrittura Firestore: regole vs Admin SDK**

- Il server Python usa **firebase-admin**: le operazioni **non passano** dalle regole di sicurezza Firestore (hanno privilegi di servizio). Se Firestore non si aggiorna, il problema è quasi sempre **chiave mancante/errata**, progetto sbagliato o **API Firestore** non abilitata sul progetto GCP — non “regole che bloccano il server”.
- Se l’**app mobile** legge/scrive Firestore con il client SDK, allora servono **Regole** in Console → Firestore. Nel repo **FitAI Analyzer** trovi un esempio adattabile: `docs/firestore.rules.example` (blocca `garmin_tokens` ai client; esempio `users/{userId}` solo per l’utente autenticato).

### 5c. Chiave Firebase cifrata sul Pi (senza dipendere dal PC)

Il PC può essere spento: sul Pi restano solo file **cifrati** più una **passphrase** in directory nascosta (`~/.secrets/`, permessi 700/600). Non è crittografia hardware: chi ha accesso root al Pi in esecuzione può comunque ispezionare la memoria del processo; serve soprattutto a **non tenere il JSON in chiaro** su disco e a non dover ripetere il deploy dal PC.

1. Sul PC (serve `openssl` nel PATH, es. Git for Windows):

   ```powershell
   cd scripts\rpi_setup
   .\encrypt_garmin_firebase_secret.ps1 -JsonPath "C:\percorso\firebase-adminsdk-xxxxx.json"
   ```

   Scegli una passphrase robusta; ottieni `garmin-firebase.enc`.

2. Carica sul Pi e attiva systemd (stessa rete):

   ```powershell
   $env:RPI_SSH_PASSWORD = "..."
   $env:RPI_GARMIN_DECRYPT_PASS = "<stessa passphrase>"
   python push_encrypted_firebase_to_pi.py --enc .\garmin-firebase.enc
   ```

   Lo script installa `/usr/local/sbin/garmin-sync-encrypted-start.sh` (fuori dal repo: il `git pull` sul server Garmin non lo sovrascrive), aggiorna `garmin-sync.service` e commenta eventuali `FIREBASE_*` nel `.env`.

3. Verifica: `curl -s http://127.0.0.1:8080/` sul Pi → `"firestore":true` se la chiave e il progetto sono corretti.

### 6. Clona il progetto (altro)

Modifica `03_clone_project.sh` e imposta l’URL del tuo repository:

```bash
nano 03_clone_project.sh
# Cambia URL_REPO="https://github.com/TUO_ORG/tuo_repo_sync.git"
```

Poi esegui:

```bash
bash 03_clone_project.sh
```

---

## Se non riesci a connetterti via SSH

1. **Controlla:** Raspberry Pi sulla stessa rete del PC?
2. **Da terminale sul Pi** (monitor + tastiera):

```bash
sudo systemctl enable ssh
sudo systemctl start ssh
ip addr show
```

3. **Firewall:** `sudo ufw allow 22` se `ufw` è attivo.

---

## Spazio stimato

| Componente      | Spazio   |
|-----------------|----------|
| git + python3   | ~50–80MB |
| venv + dipendenze | ~50–200MB |
| Progetto        | ~10–50MB |
| **Totale**      | ~150–350MB |

---

## Note Raspberry Pi 3 B+

- **RAM:** 1GB → può servire swap se il progetto usa molte dipendenze
- **SD:** 8GB minimo, 16GB+ consigliato
- **Ubuntu 32-bit:** usa `armhf` (armv7l)
