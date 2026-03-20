# Deploy su Raspberry Pi (LAN) + aggiornamento dopo ogni push su GitHub

Repository: [github.com/CristiPerciun/garmin-sync-server](https://github.com/CristiPerciun/garmin-sync-server)

## Cosa fa l’installazione

1. Clona (o aggiorna) il repo in `~/garmin-sync-server`.
2. Crea `venv` e installa `requirements.txt`.
3. Registra **systemd** `garmin-sync.service` (uvicorn su porta **8080**).
4. Attiva un **timer** che ogni **3 minuti** esegue `git fetch` (anche dopo **rebase** / **force-push**), `reset --hard` sul branch configurato (default: branch corrente, oppure `GARMIN_SYNC_GIT_BRANCH` in `/etc/default/garmin-sync-env`), reinstalla dipendenze se il commit è cambiato e **riavvia sempre** `garmin-sync.service`.

> **Nota:** GitHub non può raggiungere un Pi solo su IP privato (es. `10.x`) senza VPN/Tailscale. Il timer implementa “dopo il push, entro pochi minuti il Pi si allinea a GitHub”.

## Prerequisiti sul Pi

- Utente `cperciun` (o imposta `SUDO_USER` quando lanci lo script).
- `git`, `python3`, `python3-venv`, `pip` (es. script `scripts/rpi_setup/02_prepare_environment.sh` in questo repo).

## Nome stabile per l’app Flutter (mDNS, senza rifare `.env` a ogni IP)

Su Raspberry Pi OS il hostname predefinito è **`raspberrypi`**: in LAN il telefono può usare **`http://raspberrypi.local:8080`** (risoluzione mDNS/Bonjour). L’IP può cambiare col DHCP; il nome **`.local`** resta lo stesso.

- Hostname personalizzato: `sudo hostnamectl set-hostname fitai-garmin` (esempio) → nell’app `.env`: `GARMIN_SERVER_URL=http://fitai-garmin.local:8080`.
- Serve **stessa rete** (stesso Wi‑Fi / hotspot) tra telefono e Pi.
- Se `.local` non risolve (raro su Android o reti filtrate), usa un **IP riservato sul router** o **Tailscale** (hostname fisso tipo `pi` in MagicDNS).

## Installazione

```bash
git clone https://github.com/CristiPerciun/garmin-sync-server.git
cd garmin-sync-server
sudo bash deploy/rpi/install.sh
```

## Fork GitHub + branch `fork-sync` (Pi legato al ramo deploy)

Documentazione completa del flusso PC → push → Pi: **`docs/WORKFLOW_FORK_SYNC.md`**.

**Una tantum sul Pi** (dopo `git pull` nel repo):

```bash
cd ~/garmin-sync-server
# opzionale: export GARMIN_SYNC_REPO_URL=https://github.com/TUO_UTENTE/garmin-sync-server.git
sudo -E bash deploy/rpi/setup_fork_sync_branch.sh
```

Lo script scrive `GARMIN_SYNC_GIT_BRANCH=fork-sync` in `/etc/default/garmin-sync-env`, opzionalmente imposta `origin` sul fork, poi esegue **`install.sh`** (che ora rispetta quel branch invece di forzare sempre `main`).

Sul **PC**, dopo aver allineato `fork-sync` a `main` (`merge` o `rebase` + `push`, anche `--force-with-lease`):

- il timer **`garmin-sync-pull.timer`** (~3 min) esegue fetch → **`reset --hard origin/fork-sync`** → pip → **`systemctl restart garmin-sync`**.

> Il **solo `git pull` sul PC** non aggiorna il Pi: serve **`git push origin fork-sync`** sul fork.

## Variabili d’ambiente (obbligatorie per avvio)

**Verifica formato credenziali (JSON / Base64) e permessi Firestore** (dopo `pip install` e `.env`):

```bash
cd ~/garmin-sync-server
source venv/bin/activate
python3 deploy/rpi/verify_firebase_credentials.py
```

Deve mostrare `[OK]` su service account e Firestore. Se fallisce con `PermissionDenied`, il JSON è leggibile ma l’IAM sul progetto è insufficiente (non è un problema “formato”).

Copia sul Pi:

```bash
cp .env.example .env
nano .env
```

Imposta almeno:

- `FIREBASE_CREDENTIALS_B64` **oppure** `FIREBASE_CREDENTIALS` (JSON su una riga) — genera Base64 con `scripts/rpi_setup/encode_firebase_credentials_b64.ps1` (Windows) o `base64 -w0` sul file JSON.
- Credenziali Garmin se usi login lato server (se applicabile al tuo flusso).

Poi:

```bash
sudo systemctl restart garmin-sync
sudo journalctl -u garmin-sync -f
```

## Rete e app Flutter (FitAI Analyzer)

Su **stessa LAN** (es. hotspot: spesso `172.20.10.x`), in `.env` dell’app Flutter:

```env
GARMIN_SERVER_URL=http://172.20.10.4:8080
```

Usa l’**IPv4** del Pi visibile dal telefono (il client HTTP dell’app di solito non usa IPv6 letterale). Fuori dalla LAN serve un tunnel/URL pubblico verso la porta 8080 (Tailscale, reverse proxy, ecc.).

**SSH da PC:** se `172.20.10.4` non risponde, prova IPv6:

`ssh -6 cperciun@[2a02:b025:14:79c8:c244:4ff3:3191:33d2]`

## Pip e errore SSL (`CERTIFICATE_VERIFY_FAILED`)

Se la rete intercetta HTTPS verso PyPI (certificato self-signed), crea:

```bash
echo 'GARMIN_SYNC_PIP_INSECURE=1' | sudo tee /etc/default/garmin-sync-env
sudo systemctl daemon-reload
```

Poi rilancia `sudo bash deploy/rpi/install.sh`. **Rischio:** disabilita la verifica SSL solo verso i `trusted-host` indicati nello script; in ufficio conviene installare il certificato CA della proxy sul Pi.

### Garmin Connect: `SSLCertVerificationError` / `self-signed certificate` verso `sso.garmin.com`

Nei log compare come `GarminConnectConnectionError` con catena `SSLCertVerificationError`. Non è la password Garmin: il TLS verso Garmin viene **rotto in mezzo** (proxy aziendale, antivirus che ispeziona HTTPS, captive portal, Pi collegato a una rete che inietta un certificato non firmato da una CA di sistema sul Pi).

**Cosa fare:** far uscire il Raspberry su Internet **senza** SSL inspection (hotspot del telefono, rete domestica senza proxy), oppure installare sul Pi il **certificato root** della CA che firma il certificato mostrato dalla proxy (`/usr/local/share/ca-certificates/` + `sudo update-ca-certificates`). Non disabilitare la verifica SSL nel codice del server salvo debug temporaneo.

### Download pip interrotto (grpcio ~200MB su ARM)

Se `pip` si ferma a metà scaricamento (rete instabile o sessione SSH corta), sul Pi esegui **in locale** (monitor + tastiera o SSH interattivo):

```bash
cd ~/garmin-sync-server
bash deploy/rpi/complete_pip.sh
sudo systemctl restart garmin-sync
```

## Collegamento Garmin fallisce: `403 Missing or insufficient permissions` o `504 Deadline Exceeded`

Questi messaggi nei log (`garmin.log` / `garmin_comms.log`) vengono da **Firestore / Google API**, non da Garmin Connect: il Pi non riesce a **scrivere** `garmin_tokens` o `users/{uid}` dopo (o durante) il login.

1. **Stesso progetto Firebase** dell’app Flutter: il JSON (o `FIREBASE_CREDENTIALS_B64`) deve essere la chiave **Account di servizio** di quel progetto (Firebase Console → Impostazioni progetto → Account di servizio → Genera nuova chiave privata).
2. **IAM su Google Cloud** (console.cloud.google.com → progetto corretto → IAM): il service account della chiave deve poter usare Firestore, ad es. ruolo **Cloud Datastore User** (`roles/datastore.user`) o **Editor** sul progetto (per prove; in produzione restringi).
3. **API abilitate**: nel progetto deve risultare attiva **Cloud Firestore API**.
4. **`504 Deadline Exceeded`**: rete instabile dal Pi a Google, DNS, firewall in uscita; riprova; evita VPN che bloccano `*.googleapis.com`.

Dopo aver corretto IAM o la chiave, `sudo systemctl restart garmin-sync` e riprova il collegamento dall’app.

## Log errori verso Garmin (~1 giorno su disco)

| File | Contenuto |
|------|-----------|
| `logs/garmin_comms.log` | Comunicazioni / errori verso Garmin Connect (tipo eccezione, `http_status`, estratto body). **Retention 1 giorno** + rotazione a mezzanotte (poco spazio su SD). |
| `garmin.log` | Log applicativo generale (rotazione per dimensione). |
| `journalctl -u garmin-sync -f` | Output uvicorn in tempo reale. |

## Verifica automatica (sul Pi, senza SSH da Cursor)

Dopo `git pull`, dalla directory del repo:

```bash
cd ~/garmin-sync-server
python3 deploy/rpi/verify_pi_setup.py
```

Controlla unit systemd, timer, script in `/usr/local/sbin`, venv, `systemctl is-active`, e `GET http://127.0.0.1:8080/`.  
Questo script va eseguito **sulla macchina Ubuntu/Raspberry** (terminale locale o tua sessione SSH); l’IDE su Windows non può collegarsi al Pi senza SSH configurato.

## Comandi utili

| Comando | Effetto |
|--------|---------|
| `systemctl status garmin-sync` | Stato API |
| `systemctl list-timers \| grep garmin` | Prossimo pull da GitHub |
| `sudo systemctl start garmin-sync-pull.service` | Pull immediato |
| `curl -s http://127.0.0.1:8080/docs` | Swagger locale |

## Deploy istantaneo da GitHub Actions (opzionale)

Funziona solo se il runner raggiunge il Pi (IP pubblico, Tailscale, self-hosted runner). Vedi `.github/workflows/deploy-rpi-ssh.example.yml`.
