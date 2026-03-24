#Requires -Version 5.1
<#
  Confronta clone locale vs origin, chiama health HTTPS, opzionale SSH sul Pi.
  Ieri in LAN: ssh cperciun@192.168.1.200  →  cd ~/garmin-sync-server  →  git branch -v
  Da remoto: SSH su DuckDNS richiede port forwarding TCP verso il Pi (443/80 != SSH).

  Esempi:
    .\scripts\check_deploy_sync.ps1
    .\scripts\check_deploy_sync.ps1 -SshTarget "cperciun@192.168.1.200"
#>
param(
    [string] $RepoRoot = "",
    [string] $LocalBranch = "main",
    [string] $PublicUrl = "https://myrasberrysyncgar.duckdns.org",
    [string] $SshTarget = "",
    [int] $SshPort = 22,
    [string] $PiRepoPath = "~/garmin-sync-server",
    [string] $PiBranch = "fork-sync"
)

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
    $RepoRoot = Split-Path $PSScriptRoot -Parent
}

Set-Location $RepoRoot
if (-not (Test-Path ".git")) {
    Write-Error "Non e' un repo Git: $RepoRoot"
}

Write-Host "=== Repo: $RepoRoot ===" -ForegroundColor Cyan

Write-Host "`n--- git fetch origin ---" -ForegroundColor Yellow
git fetch origin 2>&1 | ForEach-Object { Write-Host $_ }

$localSha = (git rev-parse $LocalBranch).Trim()
$remoteSha = (git rev-parse "origin/$LocalBranch").Trim()
Write-Host "`n--- PC: $LocalBranch vs origin/$LocalBranch ---" -ForegroundColor Yellow
Write-Host "  $LocalBranch     : $localSha"
Write-Host "  origin/$LocalBranch : $remoteSha"
if ($localSha -eq $remoteSha) {
    Write-Host "  OK: allineato" -ForegroundColor Green
} else {
    Write-Host "  ATTENZIONE: push/pull necessario" -ForegroundColor Red
}

$forkRemote = git rev-parse "origin/$PiBranch" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "`n--- origin/$PiBranch (deploy Pi via timer) ---" -ForegroundColor Yellow
    Write-Host "  $($forkRemote.Trim())"
    if ($remoteSha -eq $forkRemote.Trim()) {
        Write-Host "  OK: stesso commit di origin/$LocalBranch" -ForegroundColor Green
    } else {
        Write-Host "  INFO: diverso da origin/$LocalBranch (normale se usi solo fork-sync sul Pi)" -ForegroundColor DarkYellow
    }
}

Write-Host "`n--- Health API (solo servizio UP, no SHA Git) ---" -ForegroundColor Yellow
try {
    $r = Invoke-WebRequest -Uri $PublicUrl -UseBasicParsing -TimeoutSec 15
    Write-Host "  HTTP $($r.StatusCode)"
    Write-Host "  $($r.Content)"
} catch {
    Write-Host "  Errore: $_" -ForegroundColor Red
}

if ($SshTarget) {
    $sshArgs = @()
    if ($SshPort -ne 22) { $sshArgs += "-p", "$SshPort" }
    $sshArgs += $SshTarget
    $remoteCmd = "cd $PiRepoPath && git fetch origin 2>/dev/null; echo HEAD:; git rev-parse HEAD; echo branch:; git branch --show-current; echo origin/$PiBranch:; git rev-parse origin/$PiBranch 2>/dev/null"
    Write-Host "`n--- SSH $SshTarget ---" -ForegroundColor Yellow
    & ssh @sshArgs $remoteCmd
} else {
    Write-Host "`n--- SSH non eseguito ---" -ForegroundColor DarkGray
    Write-Host "  LAN (come ieri): ssh cperciun@192.168.1.200"
    Write-Host "  Remoto: apri porta SSH sul router verso il Pi, poi: ssh -p PORTA cperciun@myrasberrysyncgar.duckdns.org"
    Write-Host "  Riesegui lo script con -SshTarget 'user@host' [-SshPort N]"
}

Write-Host "`nFine." -ForegroundColor Cyan
