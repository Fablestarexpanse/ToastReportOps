# Toast Report Workstation launcher.
# Brings up the backend (Docker), the operator UI, and opens the workstation
# in a standalone app window (no browser chrome). Safe to run repeatedly -
# anything already running is reused.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# MESH is optional (its library is GPL-licensed): add its profile when .env asks for it.
$profiles = @("--profile", "desktop")
$dotenv = Join-Path $root ".env"
if ((Test-Path $dotenv) -and (Select-String -Path $dotenv -Pattern '^\s*TOAST_MESH\s*=\s*1\s*$' -Quiet)) {
    $profiles += @("--profile", "mesh")
}

$UiPort = 3005
$UiUrl = "http://localhost:$UiPort"

function Test-Port([int]$port) {
    try {
        $client = New-Object Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", $port)
        $client.Close()
        return $true
    } catch { return $false }
}

Write-Host "[1/3] backend (Docker)..." -ForegroundColor DarkYellow
# NOTE: no stderr redirection - PowerShell 5.1 turns redirected native stderr
# (docker's progress output) into script-killing NativeCommandErrors.
docker compose @profiles up -d | Out-Null

Write-Host "[2/3] operator UI..." -ForegroundColor DarkYellow
if (-not (Test-Port $UiPort)) {
    Start-Process -WindowStyle Hidden cmd.exe -ArgumentList "/c", "npm --prefix operator-ui run dev -- -p $UiPort"
}
$deadline = (Get-Date).AddSeconds(90)
while (-not (Test-Port $UiPort)) {
    if ((Get-Date) -gt $deadline) { throw "operator UI did not start on port $UiPort" }
    Start-Sleep -Milliseconds 700
}

# Models are never downloaded here; just say so if they're missing (never blocks launch).
try {
    $models = (& docker compose --profile desktop exec -T api python -m toast.modelsetup status) -join "`n"
    if ($models -match "not installed") {
        Write-Host "    AI models are not installed yet - double-click 'Install Models.cmd'." -ForegroundColor Yellow
    }
} catch { }

Write-Host "[3/3] opening workstation window..." -ForegroundColor DarkYellow
# Edge app mode: standalone window, own taskbar icon, no browser chrome.
Start-Process msedge.exe -ArgumentList "--app=$UiUrl"
Write-Host "Toast Report is up: $UiUrl (public site: http://localhost:8400/site/)" -ForegroundColor Green
