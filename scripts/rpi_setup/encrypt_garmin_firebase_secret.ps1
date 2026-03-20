# Crea garmin-firebase.enc (OpenSSL AES-256-CBC + PBKDF2, 10000 iter) dal JSON account di servizio.
# La passphrase non viene salvata in file da questo script (solo prompt).
#
# Richiede openssl nel PATH (es. Git for Windows: C:\Program Files\Git\usr\bin).
#
# Uso:
#   .\encrypt_garmin_firebase_secret.ps1 -JsonPath "C:\path\firebase-adminsdk-xxxxx.json"
#   .\encrypt_garmin_firebase_secret.ps1 -JsonPath "..." -OutEnc ".\garmin-firebase.enc"

param(
    [Parameter(Mandatory = $true)]
    [string] $JsonPath,
    [string] $OutEnc = "garmin-firebase.enc"
)

$openssl = Get-Command openssl -ErrorAction SilentlyContinue
if (-not $openssl) {
    Write-Error "openssl non trovato nel PATH. Installa OpenSSL o aggiungi Git usr\bin al PATH."
    exit 1
}

if (-not (Test-Path -LiteralPath $JsonPath)) {
    Write-Error "File non trovato: $JsonPath"
    exit 1
}

$bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $JsonPath))
$b64 = [Convert]::ToBase64String($bytes)

$tmpPlain = [System.IO.Path]::GetTempFileName()
try {
    [System.IO.File]::WriteAllText($tmpPlain, $b64, [System.Text.UTF8Encoding]::new($false))
    $p1 = Read-Host "Passphrase (non verra' mostrata)" -AsSecureString
    $p2 = Read-Host "Ripeti passphrase" -AsSecureString
    $BSTR1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($p1)
    $BSTR2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($p2)
    try {
        $plain1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR1)
        $plain2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR2)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR1)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR2)
    }
    if ($plain1 -ne $plain2) {
        Write-Error "Le passphrase non coincidono."
        exit 1
    }
    if ([string]::IsNullOrEmpty($plain1)) {
        Write-Error "Passphrase vuota."
        exit 1
    }

    $tmpPass = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tmpPass, $plain1, [System.Text.UTF8Encoding]::new($false))
        $plain1 = $null
        $plain2 = $null

        $outFull = [System.IO.Path]::GetFullPath($OutEnc)
        & openssl enc -aes-256-cbc -salt -pbkdf2 -iter 10000 -in $tmpPlain -out $outFull -pass "file:$tmpPass"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "openssl enc fallito (exit $LASTEXITCODE)"
            exit 1
        }
    } finally {
        Remove-Item -LiteralPath $tmpPass -Force -ErrorAction SilentlyContinue
    }
} finally {
    Remove-Item -LiteralPath $tmpPlain -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Creato: $([System.IO.Path]::GetFullPath($OutEnc))"
Write-Host "Sul Pi la stessa passphrase va in ~/.secrets/garmin-firebase.pass (chmod 600)."
Write-Host "Deploy automatico: imposta RPI_GARMIN_DECRYPT_PASS e lancia push_encrypted_firebase_to_pi.py"
