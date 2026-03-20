#!/bin/bash
# Collega il progetto sul Pi/Ubuntu al branch fork-sync del tuo fork GitHub.
# Dopo setup: ogni push su origin fork-sync (anche dopo merge/rebase da main) viene
# preso dal timer garmin-sync-pull (~3 min) -> git reset --hard -> pip -> restart API.
#
# Uso (sul Raspberry/Ubuntu):
#   cd ~/garmin-sync-server
#   git pull
#   # opzionale, se origin deve essere il fork:
#   export GARMIN_SYNC_REPO_URL=https://github.com/TUO_UTENTE/garmin-sync-server.git
#   sudo -E bash deploy/rpi/setup_fork_sync_branch.sh
#
set -euo pipefail

BRANCH="${GARMIN_SYNC_GIT_BRANCH:-fork-sync}"
USER_NAME="${SUDO_USER:-cperciun}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
TARGET="${GARMIN_SYNC_HOME:-$HOME_DIR/garmin-sync-server}"
ENV_FILE="/etc/default/garmin-sync-env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Esegui con: sudo bash deploy/rpi/setup_fork_sync_branch.sh"
  exit 1
fi

echo "=== Scrivo GARMIN_SYNC_GIT_BRANCH=$BRANCH in $ENV_FILE ==="
touch "$ENV_FILE"
chmod 0644 "$ENV_FILE"
if grep -q '^GARMIN_SYNC_GIT_BRANCH=' "$ENV_FILE" 2>/dev/null; then
  sed -i "s/^GARMIN_SYNC_GIT_BRANCH=.*/GARMIN_SYNC_GIT_BRANCH=$BRANCH/" "$ENV_FILE"
else
  echo "GARMIN_SYNC_GIT_BRANCH=$BRANCH" >> "$ENV_FILE"
fi

if [[ -n "${GARMIN_SYNC_REPO_URL:-}" ]]; then
  echo "=== Imposto origin -> $GARMIN_SYNC_REPO_URL ==="
  if [[ -d "$TARGET/.git" ]]; then
    runuser -u "$USER_NAME" -- git -C "$TARGET" remote set-url origin "$GARMIN_SYNC_REPO_URL"
    runuser -u "$USER_NAME" -- git -C "$TARGET" remote -v
  else
    echo "WARN: repo assente in $TARGET — clona prima o lancia install.sh"
  fi
fi

# Ricarica env per install.sh
set -a
# shellcheck disable=SC1091
source "$ENV_FILE"
set +a

echo "=== install.sh (unit systemd + checkout branch se esiste su origin) ==="
bash "$SCRIPT_DIR/install.sh"

echo ""
echo "OK. Il Pi e' legato al branch: $BRANCH"
echo "  - Verifica timer: systemctl list-timers | grep garmin"
echo "  - Workflow PC: vedi docs/WORKFLOW_FORK_SYNC.md"
