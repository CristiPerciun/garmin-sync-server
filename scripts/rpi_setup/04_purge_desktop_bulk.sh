#!/bin/bash
# Rimuove suite desktop pesanti se presenti (Debian / Raspberry Pi OS).
# Più sicuro di: apt purge libreoffice*  (il * in bash è glob sui FILE, non sui pacchetti).
# Esegui: sudo bash 04_purge_desktop_bulk.sh

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "=========================================="
echo "  Purge opzionale: office / mail / torrent / giochi"
echo "=========================================="

mapfile -t CANDIDATES < <(
  dpkg-query -W -f='${Package}\n' 2>/dev/null | grep -E -i '^(gnome-games|libreoffice|thunderbird|transmission)' || true
)

if [ "${#CANDIDATES[@]}" -eq 0 ] || [ -z "${CANDIDATES[0]:-}" ]; then
  echo "Nessun pacchetto corrispondente installato (immagine minimale: OK)."
  exit 0
fi

echo "Pacchetti da rimuovere:"
printf '  %s\n' "${CANDIDATES[@]}"
apt-get purge -y --auto-remove "${CANDIDATES[@]}"
apt-get autoremove -y --purge
apt-get clean
echo ""
df -h /
