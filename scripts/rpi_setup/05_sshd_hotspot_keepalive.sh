#!/bin/bash
# Sul Pi: mantiene vive le sessioni SSH (hotspot / NAT). Esegui: sudo bash 05_sshd_hotspot_keepalive.sh
set -euo pipefail
CFG=/etc/ssh/sshd_config
if [[ "$EUID" -ne 0 ]]; then
  echo "Esegui con: sudo bash $0"
  exit 1
fi
ensure_kv() {
  local key="$1" val="$2"
  if grep -qE "^[#[:space:]]*${key}[[:space:]]" "$CFG"; then
    sed -i "s/^[#[:space:]]*${key}[[:space:]].*/${key} ${val}/" "$CFG"
  else
    echo "${key} ${val}" >> "$CFG"
  fi
}
ensure_kv ClientAliveInterval 30
ensure_kv ClientAliveCountMax 6
if sshd -t 2>/dev/null; then
  systemctl restart ssh || systemctl restart sshd
  echo "OK: sshd riavviato con keepalive lato server."
else
  echo "ERRORE: sshd -t fallito, non riavvio."
  exit 1
fi
