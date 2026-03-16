# Imposta FIREBASE_CREDENTIALS su Fly.io
# Esegui dalla cartella garmin-sync-server dove si trova firebase-service-account.json

$ErrorActionPreference = "Stop"
$jsonPath = Join-Path $PSScriptRoot "firebase-service-account.json"

if (-not (Test-Path $jsonPath)) {
    Write-Host "ERRORE: firebase-service-account.json non trovato in $PSScriptRoot" -ForegroundColor Red
    exit 1
}

$json = Get-Content $jsonPath -Raw -Encoding UTF8
$json = $json.Trim().TrimStart([char]0xFEFF)  # rimuovi BOM

# Verifica che sia JSON valido
try {
    $null = $json | ConvertFrom-Json
} catch {
    Write-Host "ERRORE: Il file non e' JSON valido" -ForegroundColor Red
    exit 1
}

# Salva in file temporaneo e usa @ per fly (evita problemi con caratteri speciali in PowerShell)
$tmpFile = [System.IO.Path]::GetTempFileName()
[System.IO.File]::WriteAllText($tmpFile, $json, [System.Text.UTF8Encoding]::new($false))

try {
    Write-Host "Impostazione FIREBASE_CREDENTIALS su Fly.io..." -ForegroundColor Yellow
    fly secrets set "FIREBASE_CREDENTIALS=@$tmpFile" --app garmin-sync-server
    Write-Host "OK: Secret impostato. La macchina si riavviera' automaticamente." -ForegroundColor Green
} finally {
    Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
}
