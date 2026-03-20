#!/bin/bash
# Script 2: Prepara ambiente Ubuntu minimale per progetto Python + Git
# Ottimizzato per Raspberry Pi 3 B+ con spazio limitato
# Esegui con: sudo bash 02_prepare_environment.sh

set -e

echo "=========================================="
echo "  PREPARAZIONE AMBIENTE - Ubuntu minimale"
echo "=========================================="
echo ""

# Verifica root
if [ "$EUID" -ne 0 ]; then
    echo "Esegui come root: sudo bash 02_prepare_environment.sh"
    exit 1
fi

# Aggiorna indice pacchetti (senza upgrade completo per risparmiare spazio)
echo "--- Aggiornamento indice pacchetti ---"
apt-get update -qq
echo ""

# Pacchetti ESSENZIALI minimi (circa 50-80MB totali)
echo "--- Installazione pacchetti essenziali ---"
apt-get install -y --no-install-recommends \
    git \
    python3 \
    python3-pip \
    python3-venv \
    ca-certificates \
    openssh-client

echo ""

# Swap: molte immagini Ubuntu sul Pi hanno già swap (zram/partizione) — non duplicare con /swapfile
TOTAL_RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
if [ "$TOTAL_RAM_MB" -lt 1536 ]; then
    echo "--- Swap (RAM: ${TOTAL_RAM_MB}MB) ---"
    if swapon --show 2>/dev/null | grep -q .; then
        echo "Swap già attivo, non creo /swapfile"
    elif [ -f /swapfile ]; then
        echo "File /swapfile già presente"
    else
        FREE_KB=$(df -k / | tail -1 | awk '{print $4}')
        if [ "$FREE_KB" -ge 2097152 ]; then
            SWAP_MB=512
        else
            SWAP_MB=256
            echo "Spazio limitato: swapfile ${SWAP_MB}MB"
        fi
        NEED_KB=$(( (SWAP_MB + 400) * 1024 ))
        if [ "$FREE_KB" -lt "$NEED_KB" ]; then
            echo "Spazio insufficiente per swapfile (serve ~$((NEED_KB/1024))MB liberi), skip"
        else
            fallocate -l ${SWAP_MB}M /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=$SWAP_MB
            chmod 600 /swapfile
            mkswap /swapfile
            swapon /swapfile
            echo '/swapfile none swap sw 0 0' >> /etc/fstab
            echo "Swapfile ${SWAP_MB}MB attivato"
        fi
    fi
else
    echo "RAM sufficiente, skip swap"
fi

echo ""

# Pulizia per liberare spazio
echo "--- Pulizia cache apt ---"
apt-get autoremove -y 2>/dev/null || true
apt-get clean
echo ""

# Verifica installazioni
echo "--- Verifica ---"
echo "Git:  $(git --version)"
echo "Python: $(python3 --version)"
echo "Pip: $(python3 -m pip --version 2>/dev/null || echo 'pip presente')"
echo ""

# Crea directory progetto (modifica il path se necessario)
PROJECT_DIR="/home/cperciun/sync_project"
mkdir -p "$PROJECT_DIR"
chown cperciun:cperciun "$PROJECT_DIR" 2>/dev/null || true
echo "Directory progetto: $PROJECT_DIR"
echo ""

echo "=========================================="
echo "  AMBIENTE PRONTO!"
echo "=========================================="
echo "Spazio residuo:"
df -h /
echo ""
echo "Prossimo passo: clona il repo con 03_clone_project.sh"
echo "Oppure manualmente: cd $PROJECT_DIR && git clone <URL_REPO>"
