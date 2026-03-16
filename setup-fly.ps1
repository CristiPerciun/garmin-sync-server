# Setup Fly.io per garmin-sync-server
# Richiede: flyctl installato (winget install Fly.Flyctl) e fly auth login

$ErrorActionPreference = "Stop"

Write-Host "=== Setup Fly.io garmin-sync-server ===" -ForegroundColor Cyan

# 1. Verifica flyctl
if (-not (Get-Command fly -ErrorAction SilentlyContinue)) {
    Write-Host "ERRORE: flyctl non trovato. Installa con: winget install Fly.Flyctl" -ForegroundColor Red
    exit 1
}

# 2. FIREBASE_CREDENTIALS
if (Test-Path "firebase-service-account.json") {
    Write-Host "`nImpostazione FIREBASE_CREDENTIALS..." -ForegroundColor Yellow
    $creds = Get-Content "firebase-service-account.json" -Raw
    fly secrets set "FIREBASE_CREDENTIALS=$creds"
    Write-Host "OK: FIREBASE_CREDENTIALS impostato" -ForegroundColor Green
} else {
    Write-Host "`nATTENZIONE: firebase-service-account.json non trovato." -ForegroundColor Yellow
    Write-Host "Impostalo manualmente: fly secrets set FIREBASE_CREDENTIALS='{...}'" -ForegroundColor Yellow
}

# 3. Volume
Write-Host "`nCreazione volume garmin_tokens (se non esiste)..." -ForegroundColor Yellow
$volumes = fly volumes list 2>$null
if ($volumes -match "garmin_tokens") {
    Write-Host "OK: Volume garmin_tokens già presente" -ForegroundColor Green
} else {
    fly volumes create garmin_tokens --region iad --size 1
    Write-Host "OK: Volume creato" -ForegroundColor Green
}

Write-Host "`n=== Setup completato ===" -ForegroundColor Cyan
Write-Host "Esegui: fly deploy" -ForegroundColor White
