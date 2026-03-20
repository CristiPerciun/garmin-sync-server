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

## Installazione

```bash
git clone https://github.com/CristiPerciun/garmin-sync-server.git
cd garmin-sync-server
sudo bash deploy/rpi/install.sh
```

## Fork GitHub + branch `fork-sync` (consigliato)

Flusso tipico:

1. **Fork** del repo su GitHub (il tuo `origin` sul PC e sul Pi punta al fork).
2. Sul PC crei / usi il branch **`fork-sync`**, ci lavori, fai **`git push`** (anche dopo **`git rebase`** + **`git push --force-with-lease`** sul branch).
3. Sul Pi imposti il branch da seguire e l’URL del fork (se non l’hai già fatto al clone):

   ```bash
   cd ~/garmin-sync-server
   git remote set-url origin https://github.com/TUO_USER/garmin-sync-server.git
   git fetch origin
   git checkout -B fork-sync origin/fork-sync   # prima volta, dopo il primo push del branch
   ```

4. Crea **`/etc/default/garmin-sync-env`** (vedi `deploy/rpi/garmin-sync-env.example`):

   ```bash
   echo 'GARMIN_SYNC_GIT_BRANCH=fork-sync' | sudo tee /etc/default/garmin-sync-env
   sudo systemctl daemon-reload
   ```

5. Il timer **`garmin-sync-pull.timer`** ogni ~3 minuti: `git fetch` con refspec che accetta history riscritta → se il commit remoto cambia → **`git reset --hard origin/fork-sync`** → `pip install -r requirements.txt` → **`systemctl restart garmin-sync`**.

> Il **pull sul PC** non aggiorna il Pi: serve **`git push`** verso GitHub (fork). Il **rebase** è supportato perché il fetch aggiorna `origin/fork-sync` anche quando non è fast-forward.

## Variabili d’ambiente (obbligatorie per avvio)

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

### Download pip interrotto (grpcio ~200MB su ARM)

Se `pip` si ferma a metà scaricamento (rete instabile o sessione SSH corta), sul Pi esegui **in locale** (monitor + tastiera o SSH interattivo):

```bash
cd ~/garmin-sync-server
bash deploy/rpi/complete_pip.sh
sudo systemctl restart garmin-sync
```

## Log errori verso Garmin (~1 giorno su disco)

| File | Contenuto |
|------|-----------|
| `logs/garmin_comms.log` | Comunicazioni / errori verso Garmin Connect (tipo eccezione, `http_status`, estratto body). **Retention 1 giorno** + rotazione a mezzanotte (poco spazio su SD). |
| `garmin.log` | Log applicativo generale (rotazione per dimensione). |
| `journalctl -u garmin-sync -f` | Output uvicorn in tempo reale. |

## Comandi utili

| Comando | Effetto |
|--------|---------|
| `systemctl status garmin-sync` | Stato API |
| `systemctl list-timers \| grep garmin` | Prossimo pull da GitHub |
| `sudo systemctl start garmin-sync-pull.service` | Pull immediato |
| `curl -s http://127.0.0.1:8080/docs` | Swagger locale |

## Deploy istantaneo da GitHub Actions (opzionale)

Funziona solo se il runner raggiunge il Pi (IP pubblico, Tailscale, self-hosted runner). Vedi `.github/workflows/deploy-rpi-ssh.example.yml`.
