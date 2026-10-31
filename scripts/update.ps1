# Toast Report Workstation - update + launch.
#
# One click: take a safety snapshot, pull the latest code, rebuild only what
# changed, restart the stack, then open the workstation (via launch.ps1).
#
# Safe by design:
#   * a verified database snapshot is taken BEFORE anything changes (migrations
#     only move forward, so the snapshot is the way back);
#   * code is updated with `git pull --ff-only` - it never merges, never
#     overwrites your local edits, and stops before changing anything if it can't;
#   * third-party images (Ollama, SearXNG, ...) are pinned by digest in
#     docker-compose.yml, so they only change when an update changes a pin -
#     and then `docker compose up` fetches exactly that image;
#   * if the API doesn't come back healthy it tells you how to roll back instead
#     of guessing - the database may already have migrated.
#
# Options:  -NoLaunch      update only, don't open the window
#           -SkipBackup    skip the snapshot (not recommended)
#           -PullImages    re-download the pinned images (repairs a damaged image)
#           -Rebuild       rebuild the backend image even if nothing changed

param(
    [switch]$NoLaunch,
    [switch]$SkipBackup,
    [switch]$PullImages,
    [switch]$Rebuild
)

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
$ApiHealth = "http://127.0.0.1:8400/healthz"

# Run a native command without letting its stderr progress output (git and docker
# both write progress there) trip PowerShell 5.1's NativeCommandError. Returns the
# exit code; output is shown live and also returned in $script:LastOutput.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments, [switch]$Quiet)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = & $Exe @Arguments 2>&1 | ForEach-Object {
            $text = "$_"
            if (-not $Quiet) { Write-Host "    $text" -ForegroundColor DarkGray }
            $text
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    $script:LastOutput = ($lines -join "`n")
    return $code
}

function Step($n, $total, $text) {
    Write-Host "[$n/$total] $text" -ForegroundColor DarkYellow
}

function Stop-Update($message) {
    Write-Host ""
    Write-Host "UPDATE STOPPED: $message" -ForegroundColor Red
    Write-Host "Nothing has been changed beyond what is described above." -ForegroundColor Red
    exit 1
}

$total = 6

# ---- 1. preflight ----------------------------------------------------------
Step 1 $total "checking prerequisites..."
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Stop-Update "git is not installed or not on PATH." }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Stop-Update "Docker is not installed or not on PATH." }
if (-not (Test-Path (Join-Path $root ".git"))) {
    Stop-Update "this folder is not a git checkout, so it can't update itself. Re-install with: git clone https://github.com/Fablestarexpanse/ToastReportOps"
}
if ((Invoke-Native docker @("info") -Quiet) -ne 0) { Stop-Update "Docker isn't running. Start Docker Desktop, wait for it to be ready, then try again." }

$before = (& git rev-parse HEAD).Trim()
$branch = (& git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -eq "HEAD") { Stop-Update "git is on a detached HEAD. Check out a branch (git checkout main) first." }

if ((Invoke-Native git @("fetch", "origin") -Quiet) -ne 0) {
    Stop-Update "could not reach GitHub (no internet?). Launch without updating by using ToastReport.cmd."
}
$upstream = (& git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null)
if (-not $upstream) { Stop-Update "branch '$branch' doesn't track a remote branch, so there is nothing to update from." }
$behind = [int](& git rev-list --count "HEAD..@{u}")

$dirty = (& git status --porcelain --untracked-files=no)
if ($dirty) {
    Write-Host "    note: you have local edits to tracked files; they are left untouched." -ForegroundColor Yellow
}

# ---- 2. safety snapshot ------------------------------------------------------
Step 2 $total "safety snapshot of the database..."
if ($SkipBackup) {
    Write-Host "    skipped (-SkipBackup)" -ForegroundColor Yellow
} elseif ($behind -eq 0 -and -not $Rebuild) {
    Write-Host "    skipped - nothing new to install" -ForegroundColor DarkGray
} else {
    $apiUp = (& docker compose --profile desktop ps --status running --services 2>$null) -contains "api"
    if (-not $apiUp) {
        Write-Host "    the stack isn't running (first install?) - nothing to snapshot yet" -ForegroundColor DarkGray
    } else {
        $code = Invoke-Native docker @("compose", "--profile", "desktop", "exec", "-T", "api", "python", "-m", "toast.backup") -Quiet
        if ($code -ne 0) {
            Write-Host $script:LastOutput -ForegroundColor DarkGray
            Stop-Update "the safety snapshot failed, so I won't touch the install. Fix that, or run again with -SkipBackup if you accept the risk."
        }
        Write-Host "    snapshot saved in data\backups ($($script:LastOutput.Split("`n")[-1]))" -ForegroundColor DarkGray
    }
}

# ---- 3. pull the new code -----------------------------------------------------
Step 3 $total "getting the latest version..."
if ($behind -eq 0) {
    Write-Host "    already up to date ($($before.Substring(0, 7)))" -ForegroundColor Green
} else {
    if ((Invoke-Native git @("pull", "--ff-only")) -ne 0) {
        Stop-Update "git couldn't fast-forward (your local changes conflict with the update). Nothing was changed - ask for help rather than forcing it."
    }
}
$after = (& git rev-parse HEAD).Trim()
$changed = @()
if ($after -ne $before) { $changed = @(& git diff --name-only $before $after) }
$backendChanged = [bool]($changed | Where-Object { $_ -like "backend/*" -or $_ -eq "docker-compose.yml" })
$uiDepsChanged = [bool]($changed | Where-Object { $_ -like "operator-ui/package*.json" })
$uiChanged = [bool]($changed | Where-Object { $_ -like "operator-ui/*" })

# ---- 4. rebuild what changed ----------------------------------------------------
Step 4 $total "rebuilding what changed..."
if ($PullImages) {
    Write-Host "    pulling third-party images (-PullImages)..." -ForegroundColor DarkGray
    if ((Invoke-Native docker (@("compose") + $profiles + @("pull", "--quiet", "--ignore-buildable"))) -ne 0) {
        Write-Host "    image pull failed - continuing with the images you already have" -ForegroundColor Yellow
    }
}
if ($backendChanged -or $Rebuild) {
    if ((Invoke-Native docker (@("compose") + $profiles + @("build", "--quiet"))) -ne 0) {
        Stop-Update "the backend build failed. The code is updated but the old containers are still running. To go back: git checkout $before"
    }
} else {
    Write-Host "    backend unchanged" -ForegroundColor DarkGray
}
if ($uiDepsChanged) {
    Write-Host "    UI dependencies changed - installing..." -ForegroundColor DarkGray
    if ((Invoke-Native npm @("--prefix", (Join-Path $root "operator-ui"), "ci")) -ne 0) { Stop-Update "npm install failed. To go back: git checkout $before" }
}
if ($uiChanged) {
    # the running UI holds the old build; the launcher rebuilds and starts a fresh one
    Get-NetTCPConnection -LocalPort $UiPort -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}

# ---- 5. restart and check health ---------------------------------------------------
Step 5 $total "restarting the stack..."
if ((Invoke-Native docker (@("compose") + $profiles + @("up", "-d"))) -ne 0) {
    Stop-Update "docker couldn't start the stack. To go back: git checkout $before ; docker compose $($profiles -join ' ') up -d --build"
}
$healthy = $false
$deadline = (Get-Date).AddSeconds(150)
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri $ApiHealth -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if (-not $healthy) {
    Write-Host ""
    Write-Host "The update installed, but the API did not come back healthy." -ForegroundColor Red
    Write-Host "  Look at the logs:  docker compose logs api --tail 80" -ForegroundColor Red
    Write-Host "  To go back:        git checkout $before ; docker compose $($profiles -join ' ') up -d --build" -ForegroundColor Red
    Write-Host "  If the database misbehaves after going back, restore the snapshot from data\backups" -ForegroundColor Red
    Write-Host "  (see docs\BACKUP-RESTORE.md) - migrations only move forward." -ForegroundColor Red
    exit 1
}

# ---- 6. summary + launch ------------------------------------------------------------
Step 6 $total "done."
if ($after -ne $before) {
    Write-Host ""
    Write-Host "Updated $($before.Substring(0, 7)) -> $($after.Substring(0, 7)). What's new:" -ForegroundColor Green
    & git log --oneline --no-decorate -n 15 "$before..$after" | ForEach-Object { Write-Host "  $_" }
    $newMigrations = @($changed | Where-Object { $_ -like "*/migrations/*.sql" })
    if ($newMigrations.Count) { Write-Host "  ($($newMigrations.Count) database migration(s) applied)" -ForegroundColor DarkGray }
}
# models: never downloaded here (that's a big, deliberate step) - just point at the installer
$modelCode = Invoke-Native docker @("compose", "--profile", "desktop", "exec", "-T", "api", "python", "-m", "toast.modelsetup", "status") -Quiet
# status also lists optional comparison models, which are never required
$modelLines = @($script:LastOutput.Split("`n") | Where-Object { $_ -notmatch "optional" })
if ($modelCode -ne 0 -or ($modelLines -join "`n") -match "not installed|NOTE") {
    Write-Host ""
    Write-Host "Models need attention:" -ForegroundColor Yellow
    $modelLines | Where-Object { $_ -match "not installed|NOTE|DIFFERENT" } | ForEach-Object { Write-Host $_ -ForegroundColor Yellow }
    Write-Host "  Double-click 'Install Models.cmd' to download and verify them." -ForegroundColor Yellow
}
if (-not $NoLaunch) {
    & (Join-Path $PSScriptRoot "launch.ps1")
}
