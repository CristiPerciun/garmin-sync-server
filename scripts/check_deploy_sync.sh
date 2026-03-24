#!/usr/bin/env bash
# LAN (come sessione recente): ssh cperciun@192.168.1.200
# Remoto: serve port forwarding SSH; DuckDNS da solo espone spesso solo HTTPS.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

LOCAL_BRANCH="${LOCAL_BRANCH:-main}"
PUBLIC_URL="${PUBLIC_URL:-https://myrasberrysyncgar.duckdns.org}"
PI_REPO="${PI_REPO:-~/garmin-sync-server}"
PI_BRANCH="${PI_BRANCH:-fork-sync}"

echo "=== Repo: $REPO_ROOT ==="
git fetch origin
echo ""
echo "--- $LOCAL_BRANCH vs origin/$LOCAL_BRANCH ---"
git rev-parse "$LOCAL_BRANCH" "origin/$LOCAL_BRANCH"
echo ""
echo "--- origin/$PI_BRANCH ---"
git rev-parse "origin/$PI_BRANCH" || true
echo ""
echo "--- Health ---"
curl -sS -m 15 "$PUBLIC_URL/" || true

if [[ -n "${SSH_TARGET:-}" ]]; then
  PORT="${SSH_PORT:-22}"
  SSH_OPTS=()
  [[ "$PORT" != "22" ]] && SSH_OPTS+=(-p "$PORT")
  echo ""
  echo "--- SSH $SSH_TARGET ---"
  ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "cd $PI_REPO && git fetch origin 2>/dev/null; git rev-parse HEAD; git branch --show-current; git rev-parse origin/$PI_BRANCH 2>/dev/null"
else
  echo ""
  echo "Esporta SSH_TARGET=user@host per controllare il Pi (es. cperciun@192.168.1.200 in LAN)."
fi
