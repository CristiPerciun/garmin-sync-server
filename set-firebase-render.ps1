# Genera FIREBASE_CREDENTIALS_B64 per Render
# Esegui dalla cartella garmin-sync-server dove si trova firebase-service-account.json
# Poi incolla il valore su Render → Environment → FIREBASE_CREDENTIALS_B64

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

Write-Host ""
Write-Host "Copia il valore qui sotto e incollalo su Render:" -ForegroundColor Yellow
Write-Host "  Dashboard → tuo servizio → Environment → Add Environment Variable" -ForegroundColor Gray
Write-Host "  Key: FIREBASE_CREDENTIALS_B64" -ForegroundColor Gray
Write-Host "  Value: (incolla sotto)" -ForegroundColor Gray
Write-Host ""
Write-Host $b64 -ForegroundColor Green
Write-Host ""
Write-Host "Poi redeploy il servizio." -ForegroundColor Yellow
