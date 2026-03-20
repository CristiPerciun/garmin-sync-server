#!/bin/bash
# Copia-incolla QUESTO BLOCCO in un terminale sul Raspberry/Ubuntu (SSH o console locale).
# Non contiene password.

set -e
echo "=== 1. Repo ==="
cd ~/garmin-sync-server
git fetch origin
git status -sb
git log -1 --oneline

echo ""
echo "=== 2. Install systemd (se unit mancanti) ==="
if ! systemctl list-unit-files garmin-sync-pull.service --no-legend 2>/dev/null | grep -q garmin-sync-pull; then
  echo "Unit mancanti: eseguo install.sh ..."
  sudo bash deploy/rpi/install.sh
else
  echo "Unit già presenti."
fi

sudo systemctl daemon-reload
sudo systemctl enable --now garmin-sync-pull.timer 2>/dev/null || true
sudo systemctl enable --now garmin-sync.service 2>/dev/null || true

echo ""
echo "=== 3. Timer / servizio ==="
systemctl is-active garmin-sync-pull.timer || true
systemctl is-active garmin-sync.service || true
systemctl list-timers --all --no-pager | grep -i garmin || true

echo ""
echo "=== 4. Health API ==="
curl -sS http://127.0.0.1:8080/ || echo "API down — vedi: journalctl -u garmin-sync -n 40"

echo ""
echo "=== 5. Verifica Python (se file presente dopo git pull) ==="
if [[ -f deploy/rpi/verify_pi_setup.py ]]; then
  python3 deploy/rpi/verify_pi_setup.py
else
  echo "Esegui: git pull && python3 deploy/rpi/verify_pi_setup.py"
fi
