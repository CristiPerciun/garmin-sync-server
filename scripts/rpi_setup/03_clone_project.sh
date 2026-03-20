#!/bin/bash
# Script 3: Clona il progetto Git (modifica URL_REPO prima di eseguire)
# Esegui come utente cperciun: bash 03_clone_project.sh

set -e

# MODIFICA QUESTO con l'URL del tuo repository
URL_REPO="https://github.com/TUO_ORG/tuo_repo_sync.git"
PROJECT_DIR="/home/cperciun/sync_project"

echo "=========================================="
echo "  CLONAZIONE PROGETTO"
echo "=========================================="
echo ""

if [ "$URL_REPO" = "https://github.com/TUO_ORG/tuo_repo_sync.git" ]; then
    echo "ERRORE: Modifica URL_REPO in questo script con l'URL reale del repository!"
    exit 1
fi

mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR" || exit 1

# Clone (shallow per risparmiare spazio su RPi)
# Se la directory non è vuota, clona in sottocartella
if [ -n "$(ls -A 2>/dev/null)" ]; then
    REPO_NAME=$(basename "$URL_REPO" .git)
    git clone --depth 1 "$URL_REPO" "$REPO_NAME" 2>/dev/null || git clone "$URL_REPO" "$REPO_NAME"
    cd "$REPO_NAME"
else
    git clone --depth 1 "$URL_REPO" . 2>/dev/null || git clone "$URL_REPO" .
fi

echo ""
echo "Contenuto:"
ls -la

# Se c'è requirements.txt, crea venv e installa
if [ -f "requirements.txt" ]; then
    echo ""
    echo "--- Setup ambiente Python ---"
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install --no-cache-dir -r requirements.txt
    echo "Dipendenze installate. Attiva con: source venv/bin/activate"
fi

echo ""
echo "Progetto clonato in $PROJECT_DIR"
