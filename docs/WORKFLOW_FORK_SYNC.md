# Workflow: `main` + branch `fork-sync` + aggiornamento automatico sul Pi

Il branch **`fork-sync`** e' il ramo che il **Raspberry/Ubuntu** segue (`GARMIN_SYNC_GIT_BRANCH`).  
Quando allinei `fork-sync` a `main` sul PC e fai **push** verso GitHub, entro pochi minuti il Pi **si riallinea** e **riavvia** `garmin-sync` (timer `garmin-sync-pull`).

## Una tantum sul Pi

```bash
cd ~/garmin-sync-server
git pull origin main   # o il branch che hai, per avere gli script aggiornati

# Se il remote origin deve essere il TUO fork:
export GARMIN_SYNC_REPO_URL=https://github.com/TUO_UTENTE/garmin-sync-server.git
sudo -E bash deploy/rpi/setup_fork_sync_branch.sh
```

Senza cambiare URL (fork gia' configurato come `origin`):

```bash
sudo bash deploy/rpi/setup_fork_sync_branch.sh
```

Lo script:

1. Scrive `GARMIN_SYNC_GIT_BRANCH=fork-sync` in `/etc/default/garmin-sync-env`
2. Opzionalmente imposta `git remote set-url origin` sul fork
3. Esegue `install.sh` (systemd + checkout `fork-sync` se esiste su `origin`)

## Sul PC (dopo modifiche su `main`)

Allineare `fork-sync` a `main` e pubblicare (scegli **un** flusso).

**Merge (storico semplice):**

```bash
cd /percorso/garmin-sync-server
git checkout fork-sync
git fetch origin
git merge origin/main
git push origin fork-sync
```

**Rebase (storico lineare; poi force-push sicuro):**

```bash
git checkout fork-sync
git fetch origin
git rebase origin/main
git push --force-with-lease origin fork-sync
```

Il Pi accetta anche **force-push**: `garmin-sync-pull.sh` fa fetch con refspec che aggiorna `origin/fork-sync` anche dopo rebase.

## Cosa succede sul Pi (automatico)

Ogni ~3 minuti:

1. `git fetch` del branch `fork-sync`
2. Se il commit remoto e' diverso: `git reset --hard origin/fork-sync`
3. `pip install -r requirements.txt`
4. `systemctl restart garmin-sync`

## Verifica

```bash
systemctl is-active garmin-sync-pull.timer
cat /etc/default/garmin-sync-env
curl -s http://127.0.0.1:8080/
```

Da Windows: `scripts/windows/ssh_pi_verify.ps1`

## Nota

- **`git pull` solo sul PC** non aggiorna il Pi: serve **`git push origin fork-sync`**.
- Il branch si chiama **`fork-sync`** (con la `c`).
