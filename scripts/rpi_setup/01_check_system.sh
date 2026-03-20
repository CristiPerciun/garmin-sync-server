#!/bin/bash
# Script 1: Verifica sistema Raspberry Pi 3 B+ (Ubuntu 32-bit)
# Esegui con: bash 01_check_system.sh

set -e

echo "=========================================="
echo "  VERIFICA SISTEMA - Raspberry Pi 3 B+"
echo "=========================================="
echo ""

# Info sistema
echo "--- Sistema ---"
uname -a
echo ""

# Spazio disco (CRITICO su RPi)
echo "--- Spazio disco ---"
df -h
echo ""

# Spazio libero (usare MB: su SD piccole ~1GB liberi altrimenti diventa "0 GB" con la divisione intera)
DISK_FREE_RAW=$(df -k / | tail -1 | awk '{print $4}')
DISK_FREE_MB=$((DISK_FREE_RAW / 1024))
DISK_FREE_GB=$((DISK_FREE_RAW / 1024 / 1024))
echo "Spazio libero su /: ~${DISK_FREE_MB}MB (~${DISK_FREE_GB}GB interi)"

# Memoria RAM (RPi 3 B+ ha solo 1GB)
echo "--- Memoria RAM ---"
free -h
echo ""

# Swap: /proc/swaps è leggibile senza root (più affidabile di swapon da utente)
echo "--- Swap ---"
cat /proc/swaps 2>/dev/null || echo "(impossibile leggere /proc/swaps)"
echo ""

# Soglie in MB (affidabili su card 8GB con Ubuntu già installato)
HARD_MIN_MB=400
SOFT_MIN_MB=1200
COMFORT_MB=3072

if [ "$DISK_FREE_MB" -lt "$HARD_MIN_MB" ] 2>/dev/null; then
    echo "ERRORE: Spazio critico (< ${HARD_MIN_MB}MB). Libera spazio prima di proseguire."
    echo "Suggerimento: sudo apt autoremove -y && sudo apt clean"
    exit 1
elif [ "$DISK_FREE_MB" -lt "$SOFT_MIN_MB" ] 2>/dev/null; then
    echo "ATTENZIONE: Poco spazio (~${DISK_FREE_MB}MB). Setup Python minimo ok; evita dipendenze pesanti."
elif [ "$DISK_FREE_MB" -lt "$COMFORT_MB" ] 2>/dev/null; then
    echo "OK per sync Python+venv (~${DISK_FREE_MB}MB). Con >=${COMFORT_MB}MB sei più tranquillo per aggiornamenti."
else
    echo "OK: Spazio confortevole (~${DISK_FREE_MB}MB liberi)."
fi

echo ""
echo "Verifica completata. Procedi con 02_prepare_environment.sh"
