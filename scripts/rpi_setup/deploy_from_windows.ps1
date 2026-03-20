# Deploy script per Raspberry Pi - esegui da Windows (stessa rete del Pi)
# Uso: .\deploy_from_windows.ps1

$PiUser = "cperciun"
$PiHost = "172.20.10.4"
$ScriptDir = $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Deploy setup su Raspberry Pi" -ForegroundColor Cyan
Write-Host "  Host: $PiHost" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Test connessione
Write-Host "Test connessione a $PiHost..." -ForegroundColor Yellow
$ping = Test-Connection -ComputerName $PiHost -Count 1 -Quiet -ErrorAction SilentlyContinue
if (-not $ping) {
    Write-Host "ERRORE: Impossibile raggiungere $PiHost" -ForegroundColor Red
    Write-Host "Assicurati che il Raspberry Pi sia sulla stessa rete." -ForegroundColor Yellow
    exit 1
}
Write-Host "OK: Host raggiungibile" -ForegroundColor Green
Write-Host ""

# Crea directory remota e copia script
Write-Host "Copia script su $PiUser@${PiHost}:~/rpi_setup ..." -ForegroundColor Yellow
ssh "${PiUser}@${PiHost}" "mkdir -p ~/rpi_setup"
scp -r "$ScriptDir\*" "${PiUser}@${PiHost}:~/rpi_setup/"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERRORE: Copia fallita (verifica password SSH)." -ForegroundColor Red
    exit 1
}
Write-Host "OK: Script copiati" -ForegroundColor Green
Write-Host ""

Write-Host "Connessione SSH per eseguire setup..." -ForegroundColor Yellow
Write-Host "Esegui manualmente:" -ForegroundColor White
Write-Host "  ssh $PiUser@$PiHost" -ForegroundColor Gray
Write-Host "  cd ~/rpi_setup && bash 01_check_system.sh" -ForegroundColor Gray
Write-Host "  sudo bash 02_prepare_environment.sh" -ForegroundColor Gray
Write-Host "  # Modifica URL_REPO in 03_clone_project.sh, poi:" -ForegroundColor Gray
Write-Host "  bash 03_clone_project.sh" -ForegroundColor Gray
Write-Host ""
