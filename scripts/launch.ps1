# Toast Report Workstation launcher.
# Brings up the backend (Docker), the operator UI, and opens the workstation
# in a standalone app window (no browser chrome). Safe to run repeatedly -
# anything already running is reused.

$ErrorActionPreference = "Stop"
# Containers print UTF-8; without this Windows PowerShell shows an em dash as "an unreadable 3-character mess".
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
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
docker compose @profiles up -d --quiet-pull | Out-Null

Write-Host "[2/3] operator UI..." -ForegroundColor DarkYellow
$uiDir = Join-Path $root "operator-ui"
if (-not (Test-Port $UiPort)) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        throw "Node.js is not installed. Install Node 20 or newer from https://nodejs.org, then run this again."
    }
    $nodeMajor = [int]((& node --version) -replace "^v(\d+).*", '$1')
    if ($nodeMajor -lt 20) { throw "Node.js $(& node --version) is too old; the UI needs Node 20 or newer (https://nodejs.org)." }
    # a fresh clone has no UI packages yet; without them the hidden window just dies
    if (-not (Test-Path (Join-Path $uiDir "node_modules\.bin"))) {
        Write-Host "    installing UI packages (first run only, a minute or two)..." -ForegroundColor DarkGray
        $prev = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & npm --prefix $uiDir ci 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        $npmCode = $LASTEXITCODE
        $ErrorActionPreference = $prev
        if ($npmCode -ne 0) { throw "installing the UI packages failed (npm exit $npmCode) - see the lines above." }
    }
    # absolute paths + an explicit working folder: Start-Process does not follow
    # Set-Location, so a relative path would resolve against wherever PowerShell started
    Start-Process -WindowStyle Hidden -WorkingDirectory $root cmd.exe -ArgumentList "/c", "npm --prefix `"$uiDir`" run dev -- -p $UiPort"
}
$deadline = (Get-Date).AddSeconds(120)
while (-not (Test-Port $UiPort)) {
    if ((Get-Date) -gt $deadline) {
        throw "operator UI did not start on port $UiPort. To see why, run: npm --prefix `"$uiDir`" run dev -- -p $UiPort"
    }
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
