#!/bin/bash
# Installa/aggiorna garmin-sync-server sul Raspberry Pi.
# Prerequisito: git, python3-venv (vedi ifev scripts/rpi_setup/02_prepare_environment.sh)
#
# Uso:
#   git clone https://github.com/CristiPerciun/garmin-sync-server.git
#   cd garmin-sync-server && sudo bash deploy/rpi/install.sh
set -euo pipefail

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
runuser -u "$USER_NAME" -- "$TARGET/venv/bin/pip" install --upgrade pip
runuser -u "$USER_NAME" -- "$TARGET/venv/bin/pip" install --no-cache-dir -r "$TARGET/requirements.txt"

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
