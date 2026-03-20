#!/bin/bash
# Sul Pi, da cperciun: completa pip se l'installazione si è interrotta (download grpcio ~200MB).
# bash deploy/rpi/complete_pip.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ -f /etc/default/garmin-sync-env ]]; then
  set -a
  # shellcheck disable=SC1091
  source /etc/default/garmin-sync-env
  set +a
fi
EXTRA=(--trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host www.piwheels.org)
for attempt in 1 2 3; do
  echo "pip install (tentativo $attempt)..."
  if ./venv/bin/pip install "${EXTRA[@]}" --no-cache-dir --default-timeout=600 -r requirements.txt; then
    echo "OK"
    exit 0
  fi
  echo "Riprovo tra 10s..."
  sleep 10
done
exit 1
