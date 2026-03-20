#!/bin/bash
# Installa/aggiorna garmin-sync-server sul Raspberry Pi.
# Prerequisito: git, python3-venv (vedi scripts/rpi_setup/02_prepare_environment.sh in questo repo)
#
# Uso:
#   git clone https://github.com/CristiPerciun/garmin-sync-server.git
#   cd garmin-sync-server && sudo bash deploy/rpi/install.sh
set -euo pipefail

# SSL aziendale / proxy: crea /etc/default/garmin-sync-env con:
#   GARMIN_SYNC_PIP_INSECURE=1
# (o passa la stessa variabile prima di sudo). Sconsigliato salvo reti con MITM su PyPI.
if [[ -f /etc/default/garmin-sync-env ]]; then
  set -a
  # shellcheck disable=SC1091
  source /etc/default/garmin-sync-env
  set +a
fi
PIP_EXTRA=()
if [[ "${GARMIN_SYNC_PIP_INSECURE:-}" == "1" ]]; then
  PIP_EXTRA=(--trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host www.piwheels.org)
fi

USER_NAME="${SUDO_USER:-cperciun}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
TARGET="${GARMIN_SYNC_HOME:-$HOME_DIR/garmin-sync-server}"
REPO_URL="${GARMIN_SYNC_REPO_URL:-https://github.com/CristiPerciun/garmin-sync-server.git}"

echo "Target: $TARGET (utente $USER_NAME)"

if [[ ! -d "$TARGET/.git" ]]; then
  echo "Clono $REPO_URL -> $TARGET"
  mkdir -p "$(dirname "$TARGET")"
  runuser -u "$USER_NAME" -- git clone "$REPO_URL" "$TARGET"
fi

echo "Sync con GitHub ..."
runuser -u "$USER_NAME" -- git -C "$TARGET" fetch origin
if runuser -u "$USER_NAME" -- git -C "$TARGET" show-ref --verify --quiet refs/remotes/origin/main; then
  runuser -u "$USER_NAME" -- git -C "$TARGET" checkout -B main origin/main
else
  runuser -u "$USER_NAME" -- git -C "$TARGET" checkout -B master origin/master
fi

echo "venv + requirements.txt ..."
runuser -u "$USER_NAME" -- python3 -m venv "$TARGET/venv"
runuser -u "$USER_NAME" -- "$TARGET/venv/bin/pip" install "${PIP_EXTRA[@]}" --upgrade pip
runuser -u "$USER_NAME" -- "$TARGET/venv/bin/pip" install "${PIP_EXTRA[@]}" --no-cache-dir -r "$TARGET/requirements.txt"

install -m 0755 "$TARGET/deploy/rpi/garmin-sync-pull.sh" /usr/local/sbin/garmin-sync-pull.sh
cp "$TARGET/deploy/rpi/garmin-sync.service" /etc/systemd/system/garmin-sync.service
cp "$TARGET/deploy/rpi/garmin-sync-pull.service" /etc/systemd/system/garmin-sync-pull.service
cp "$TARGET/deploy/rpi/garmin-sync-pull.timer" /etc/systemd/system/garmin-sync-pull.timer

systemctl daemon-reload
systemctl enable garmin-sync.service
systemctl enable garmin-sync-pull.timer
systemctl start garmin-sync-pull.timer
systemctl restart garmin-sync.service || true

echo ""
echo "OK. Configura $TARGET/.env (FIREBASE_CREDENTIALS_B64, GARMIN_*, ecc.) poi:"
echo "  sudo systemctl restart garmin-sync"
echo "Timer aggiornamenti: systemctl list-timers | grep garmin"
echo ""
echo "Opzionale (fork + branch dedicato, es. fork-sync):"
echo "  sudo cp $TARGET/deploy/rpi/garmin-sync-env.example /etc/default/garmin-sync-env"
echo "  sudo nano /etc/default/garmin-sync-env   # es. GARMIN_SYNC_GIT_BRANCH=fork-sync"
echo "  sudo systemctl daemon-reload"
echo "Vedi RPI_DEPLOY.md sezione \"Fork GitHub + branch fork-sync\"."
