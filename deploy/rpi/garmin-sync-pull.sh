#!/bin/bash
# Eseguito da root (timer systemd): aggiorna il repo da GitHub e riavvia il servizio.
set -euo pipefail
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
REPO=/home/cperciun/garmin-sync-server
VENV="$REPO/venv"

if [[ ! -d "$REPO/.git" ]]; then
  echo "garmin-sync-pull: repo mancante in $REPO" >&2
  exit 0
fi

runuser -u cperciun -- git -C "$REPO" fetch origin main 2>/dev/null || runuser -u cperciun -- git -C "$REPO" fetch origin master 2>/dev/null || true

BRANCH=$(runuser -u cperciun -- git -C "$REPO" rev-parse --abbrev-ref HEAD)
REMOTE_REF="origin/$BRANCH"
LOCAL=$(runuser -u cperciun -- git -C "$REPO" rev-parse HEAD)
REMOTE=$(runuser -u cperciun -- git -C "$REPO" rev-parse "$REMOTE_REF" 2>/dev/null || echo "$LOCAL")

if [[ "$LOCAL" == "$REMOTE" ]]; then
  exit 0
fi

echo "garmin-sync-pull: aggiornamento $LOCAL -> $REMOTE"
runuser -u cperciun -- git -C "$REPO" reset --hard "$REMOTE_REF"

if [[ -x "$VENV/bin/pip" ]]; then
  runuser -u cperciun -- "$VENV/bin/pip" install "${PIP_EXTRA[@]}" --no-cache-dir -q -r "$REPO/requirements.txt"
fi

systemctl restart garmin-sync.service
