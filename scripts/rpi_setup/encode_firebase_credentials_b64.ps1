# Genera la riga per .env: FIREBASE_CREDENTIALS_B64=<base64 del JSON account di servizio>
# Uso: .\encode_firebase_credentials_b64.ps1 -JsonPath "C:\path\firebase-adminsdk-xxxxx.json"
# Non committare il JSON né il valore in chiaro.

param(
    [Parameter(Mandatory = $true)]
    [string] $JsonPath
)

if (-not (Test-Path -LiteralPath $JsonPath)) {
    Write-Error "File non trovato: $JsonPath"
    exit 1
}

$bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $JsonPath))
$b64 = [Convert]::ToBase64String($bytes)

Write-Host "Aggiungi sul Pi in .env (o variabili systemd):"
Write-Host ""
Write-Host "FIREBASE_CREDENTIALS_B64=$b64"
Write-Host ""

$out = Join-Path (Get-Location) "garmin-sync-firebase.env.fragment.txt"
[System.IO.File]::WriteAllText($out, "FIREBASE_CREDENTIALS_B64=$b64")
Write-Host "Salvato anche in: $out (non committare questo file)"
