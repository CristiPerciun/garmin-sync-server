#!/usr/bin/env bash
# Avvio garmin-sync con FIREBASE_CREDENTIALS_B64 decifrato da file (OpenSSL).
# File attesi (solo utente del servizio, tipicamente cperciun):
#   ~/.secrets/garmin-firebase.enc   — blob AES-256-CBC + PBKDF2 (openssl enc)
#   ~/.secrets/garmin-firebase.pass  — passphrase (una riga, senza CR finale se possibile)
#
# Generazione sul PC: encrypt_garmin_firebase_secret.ps1
set -euo pipefail

SECRETS_DIR="${GARMIN_SECRETS_DIR:-$HOME/.secrets}"
ENC="${SECRETS_DIR}/garmin-firebase.enc"
PASSF="${SECRETS_DIR}/garmin-firebase.pass"
# Repo clonato sul Pi (override: export GARMIN_REPO=...)
REPO="${GARMIN_REPO:-$HOME/garmin-sync-server}"

if [[ ! -f "$ENC" || ! -f "$PASSF" ]]; then
  echo "garmin-sync-encrypted-start: mancano $ENC o $PASSF" >&2
  exit 1
fi

B64="$(openssl enc -d -aes-256-cbc -pbkdf2 -iter 10000 -in "$ENC" -pass "file:${PASSF}" 2>/dev/null | tr -d '\r\n')"
if [[ -z "$B64" ]]; then
  echo "garmin-sync-encrypted-start: decifra fallita (passphrase o file corrotto)" >&2
  exit 1
fi

export FIREBASE_CREDENTIALS_B64="$B64"
cd "$REPO"
exec ./venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
