# Deploy su Raspberry Pi (LAN) + aggiornamento dopo ogni push su GitHub

Repository: [github.com/CristiPerciun/garmin-sync-server](https://github.com/CristiPerciun/garmin-sync-server)

## Cosa fa l’installazione

1. Clona (o aggiorna) il repo in `~/garmin-sync-server`.
2. Crea `venv` e installa `requirements.txt`.
3. Registra **systemd** `garmin-sync.service` (uvicorn su porta **8080**).
4. Attiva un **timer** che ogni **3 minuti** esegue `git fetch` / `reset --hard` su `origin/main`, reinstalla dipendenze se il commit è cambiato e **riavvia** il servizio.

> **Nota:** GitHub non può raggiungere un Pi solo su IP privato (es. `10.x`) senza VPN/Tailscale. Il timer implementa “dopo il push, entro pochi minuti il Pi si allinea a GitHub”.

## Prerequisiti sul Pi

- Utente `cperciun` (o imposta `SUDO_USER` quando lanci lo script).
- `git`, `python3`, `python3-venv`, `pip` (es. script `02_prepare_environment.sh` nel repo ifev).

## Installazione

```bash
git clone https://github.com/CristiPerciun/garmin-sync-server.git
cd garmin-sync-server
sudo bash deploy/rpi/install.sh
```

## Variabili d’ambiente (obbligatorie per avvio)

Copia sul Pi:

```bash
cp .env.example .env
nano .env
```

Imposta almeno:

- `FIREBASE_CREDENTIALS_B64` **oppure** `FIREBASE_CREDENTIALS` (JSON su una riga) — vedi `RENDER_DEPLOY.md`.
- Credenziali Garmin se usi login lato server (se applicabile al tuo flusso).

Poi:

```bash
sudo systemctl restart garmin-sync
sudo journalctl -u garmin-sync -f
```

## Rete e app Flutter (FitAI Analyzer)

Su **stessa LAN** dell’iPhone/telefono/Android emulator che usa l’app, imposta in `.env` dell’app:

```env
GARMIN_SERVER_URL=http://10.15.22.3:8080
```

(Usa l’IP reale del Pi.) Fuori dalla LAN continua a usare l’URL pubblico (es. Render).

## Comandi utili

| Comando | Effetto |
|--------|---------|
| `systemctl status garmin-sync` | Stato API |
| `systemctl list-timers \| grep garmin` | Prossimo pull da GitHub |
| `sudo systemctl start garmin-sync-pull.service` | Pull immediato |
| `curl -s http://127.0.0.1:8080/docs` | Swagger locale |

## Deploy istantaneo da GitHub Actions (opzionale)

Funziona solo se il runner raggiunge il Pi (IP pubblico, Tailscale, self-hosted runner). Vedi `.github/workflows/deploy-rpi-ssh.example.yml`.
