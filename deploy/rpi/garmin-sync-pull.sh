#!/bin/bash
# Eseguito da root (timer systemd): fetch da origin, allinea il branch configurato,
# reinstalla requirements se il commit è cambiato, riavvia garmin-sync.
# Supporta push normali e history riscritta (rebase + force-push su GitHub): dopo fetch,
# origin/<branch> punta al nuovo commit → reset --hard + restart.
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

REPO="${GARMIN_SYNC_HOME:-/home/cperciun/garmin-sync-server}"
VENV="$REPO/venv"
GIT_USER="${GARMIN_SYNC_GIT_USER:-cperciun}"

if [[ ! -d "$REPO/.git" ]]; then
  echo "garmin-sync-pull: repo mancante in $REPO" >&2
  exit 0
fi

# Branch: esplicito (fork / deploy) oppure quello corrente sul clone
if [[ -n "${GARMIN_SYNC_GIT_BRANCH:-}" ]]; then
  BRANCH="$GARMIN_SYNC_GIT_BRANCH"
else
  BRANCH=$(runuser -u "$GIT_USER" -- git -C "$REPO" rev-parse --abbrev-ref HEAD)
fi

REMOTE_REF="origin/$BRANCH"

# Fetch del branch remoto (dopo rebase su GitHub il ref non è fast-forward: refspec con + aggiorna comunque)
runuser -u "$GIT_USER" -- git -C "$REPO" fetch origin "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}" 2>/dev/null \
  || runuser -u "$GIT_USER" -- git -C "$REPO" fetch origin "$BRANCH" 2>/dev/null \
  || runuser -u "$GIT_USER" -- git -C "$REPO" fetch origin 2>/dev/null \
  || true

LOCAL=$(runuser -u "$GIT_USER" -- git -C "$REPO" rev-parse HEAD)
REMOTE=$(runuser -u "$GIT_USER" -- git -C "$REPO" rev-parse "$REMOTE_REF" 2>/dev/null || echo "$LOCAL")

if [[ "$LOCAL" == "$REMOTE" ]]; then
  exit 0
fi

echo "garmin-sync-pull: branch=$BRANCH aggiornamento $LOCAL -> $REMOTE (pip + restart garmin-sync)"
runuser -u "$GIT_USER" -- git -C "$REPO" checkout -B "$BRANCH" "$REMOTE_REF"
runuser -u "$GIT_USER" -- git -C "$REPO" reset --hard "$REMOTE_REF"

if [[ -x "$VENV/bin/pip" ]]; then
  runuser -u "$GIT_USER" -- "$VENV/bin/pip" install "${PIP_EXTRA[@]}" --no-cache-dir -q -r "$REPO/requirements.txt"
fi

systemctl restart garmin-sync.service
