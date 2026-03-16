# Imposta FIREBASE_CREDENTIALS_B64 su Fly.io (base64 - evita problemi encoding)
# Esegui dalla cartella garmin-sync-server dove si trova firebase-service-account.json

$ErrorActionPreference = "Stop"
$jsonPath = Join-Path $PSScriptRoot "firebase-service-account.json"

if (-not (Test-Path $jsonPath)) {
    Write-Host "ERRORE: firebase-service-account.json non trovato in $PSScriptRoot" -ForegroundColor Red
    exit 1
}

$jsonBytes = [System.IO.File]::ReadAllBytes($jsonPath)
$b64 = [Convert]::ToBase64String($jsonBytes)

# Verifica che il JSON sia valido
try {
    $json = [System.Text.Encoding]::UTF8.GetString($jsonBytes)
    $null = $json | ConvertFrom-Json
} catch {
    Write-Host "ERRORE: Il file non e' JSON valido" -ForegroundColor Red
    exit 1
}

Write-Host "Impostazione FIREBASE_CREDENTIALS_B64 su Fly.io (base64)..." -ForegroundColor Yellow
fly secrets set "FIREBASE_CREDENTIALS_B64=$b64" --app garmin-sync-server
Write-Host "OK: Secret impostato. La macchina si riavviera' automaticamente." -ForegroundColor Green
