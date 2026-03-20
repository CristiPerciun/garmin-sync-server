# Esegui sul PC Windows (PowerShell). SSH chiederà la password — non salvata qui.
#   cd ...\garmin-sync-server\scripts\windows
#   .\ssh_pi_verify.ps1

param(
    [string] $PiHost = "172.20.10.4",
    [string] $User = "cperciun"
)

$ErrorActionPreference = "Stop"
$remote = "${User}@${PiHost}"

$script = @'
cd ~/garmin-sync-server || exit 1
echo "=== git fetch ==="
git fetch origin 2>/dev/null || true
echo "=== git pull (ff-only, allinea al remoto) ==="
git pull --ff-only origin main 2>/dev/null || git pull --ff-only origin master 2>/dev/null || echo "WARN: pull fallito, controlla a mano"
git status -sb
git log -1 --oneline
echo ""
echo "=== systemd ==="
systemctl list-unit-files "garmin-sync*" 2>/dev/null | head -20 || true
echo ""
echo "timer pull: $(systemctl is-active garmin-sync-pull.timer 2>/dev/null || echo missing)"
echo "garmin-sync: $(systemctl is-active garmin-sync.service 2>/dev/null || echo missing)"
systemctl list-timers --all --no-pager 2>/dev/null | grep -i garmin || true
echo ""
echo "=== GET / ==="
curl -sS --connect-timeout 5 http://127.0.0.1:8080/ || echo "(no response)"
echo ""
if test -f deploy/rpi/verify_pi_setup.py; then
  python3 deploy/rpi/verify_pi_setup.py
else
  echo "Manca ancora deploy/rpi/verify_pi_setup.py - fai push dal PC poi rilancia questo script."
fi
if ! systemctl is-active --quiet garmin-sync-pull.timer 2>/dev/null; then
  echo ""
  echo "NOTA: garmin-sync-pull.timer non attivo - niente auto-sync da GitHub."
  echo "      Sul Pi: sudo bash deploy/rpi/install.sh && sudo systemctl enable --now garmin-sync-pull.timer"
fi
'@

# Solo LF: CRLF dal file .ps1 su Windows rompe bash sul Pi ($'true\r', git che vede caratteri strani).
$script = $script -replace "`r`n", "`n" -replace "`r", "`n"
$b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($script))

Write-Host "=== SSH $remote (password al prompt) ===" -ForegroundColor Cyan
# tr -d '\r' = ulteriore sicurezza se il terminale aggiunge CR
ssh -o StrictHostKeyChecking=accept-new $remote "echo $b64 | base64 -d | tr -d '\r' | bash"
