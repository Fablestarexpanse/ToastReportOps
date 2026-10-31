# Toast Report Workstation - first-time install. Safe to run again at any time.
#
# Checks this computer, creates .env (with fresh random passwords for the local-only
# services), installs the UI packages, starts the stack, seeds a new database, installs
# the AI models (showing the plan first), verifies everything and opens the workstation.
# Every line it prints also goes to install.log in this folder, so an AI helper can read
# what happened instead of guessing.
#
# It never: commits or pushes, writes API keys, enables agent write access or
# publishing, changes an existing .env, or downloads anything you weren't shown first
# (except Docker images and UI packages, which every install needs).
#
# Options:  -CheckOnly    only check this computer; change nothing
#           -NoLaunch     don't open the workstation at the end
#           -SkipGpuTest  skip the ~100 MB container GPU test
#           -SkipModels   don't install models now (Install Models.cmd later)
#           -Yes          accept the model plan without asking

param(
    [switch]$CheckOnly,
    [switch]$NoLaunch,
    [switch]$SkipGpuTest,
    [switch]$SkipModels,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
# Containers print UTF-8; without this Windows PowerShell shows an em dash as "an unreadable 3-character mess".
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logFile = Join-Path $root "install.log"
try { Start-Transcript -Path $logFile -Force | Out-Null } catch { }

$CudaTestImage = "nvidia/cuda:12.4.0-base-ubuntu22.04"
$warnings = New-Object System.Collections.ArrayList

# Run a native command without PowerShell 5.1 turning its stderr progress into errors.
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

function Step($n, $text) { Write-Host ""; Write-Host "[$n/8] $text" -ForegroundColor DarkYellow }
function Ok($text) { Write-Host "    OK    $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    WARN  $text" -ForegroundColor Yellow; [void]$warnings.Add($text) }
function Stop-Install($text) {
    Write-Host ""
    Write-Host "INSTALL STOPPED: $text" -ForegroundColor Red
    Write-Host "Full log: $logFile" -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}
function New-Secret {
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
}
function Test-Listening([int]$port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

Write-Host "Toast Report Workstation - install ($(Get-Date -Format 'yyyy-MM-dd HH:mm'))" -ForegroundColor Cyan
Write-Host "Folder: $root"

# ---- 1. this computer --------------------------------------------------------------
Step 1 "checking this computer..."
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-Install "Docker Desktop is not installed. Install it (WSL2 backend) from https://docs.docker.com/desktop/setup/install/windows-install/ and run this again."
}
if ((Invoke-Native docker @("info") -Quiet) -ne 0) {
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if ($CheckOnly -or -not (Test-Path $desktop)) {
        Stop-Install "Docker is installed but not running. Start Docker Desktop, wait for 'Engine running', then run this again."
    }
    Write-Host "    Docker Desktop isn't running - starting it (can take a minute or two)..." -ForegroundColor DarkGray
    Start-Process $desktop
    $deadline = (Get-Date).AddMinutes(4)
    while ((Invoke-Native docker @("info") -Quiet) -ne 0) {
        if ((Get-Date) -gt $deadline) { Stop-Install "Docker Desktop did not finish starting within 4 minutes. Open it, wait for 'Engine running', then run this again." }
        Start-Sleep -Seconds 5
    }
}
Ok "Docker engine running"
if ((Invoke-Native docker @("compose", "version") -Quiet) -ne 0) { Stop-Install "Docker Compose v2 is missing; update Docker Desktop." }
Ok "Docker Compose: $($script:LastOutput.Trim())"

if (Get-Command node -ErrorAction SilentlyContinue) {
    $nodeVersion = (& node --version).Trim()
    if ([int]($nodeVersion -replace "^v(\d+).*", '$1') -lt 20) {
        Stop-Install "Node.js $nodeVersion is too old; install Node 20 or newer from https://nodejs.org."
    }
    Ok "Node.js $nodeVersion"
} else {
    Stop-Install "Node.js is not installed (the workstation UI needs it). Install Node 20 or newer from https://nodejs.org and run this again."
}
if (Get-Command git -ErrorAction SilentlyContinue) { Ok "git $((& git --version) -replace '^git version ', '')" }
else { Warn "git is not installed - the app runs, but 'Update and Launch' needs git to update." }

$drive = (Split-Path -Qualifier $root).TrimEnd(":")
$freeGb = [math]::Round((Get-PSDrive -Name $drive).Free / 1GB)
if ($freeGb -lt 40) { Warn "only $freeGb GB free on ${drive}: - images and models need about 20 GB, and the archive grows over time." }
else { Ok "$freeGb GB free on ${drive}:" }

foreach ($port in 3005, 8400) {
    if (Test-Listening $port) {
        $owner = (Get-NetTCPConnection -LocalPort $port -State Listen | Select-Object -First 1).OwningProcess
        $name = (Get-Process -Id $owner -ErrorAction SilentlyContinue).ProcessName
        if ($name -match "docker|wslrelay|vpnkit|node") { Ok "port $port in use by $name (probably this app already running)" }
        else { Warn "port $port is already used by '$name' - stop it, or the workstation can't start there." }
    } else { Ok "port $port free" }
}

$gpu = $null
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $row = (& nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
    $p = @("$row".Split(",") | ForEach-Object { $_.Trim() })
    if ($p.Count -ge 4 -and $p[1] -match "^\d+$") {
        $gpu = [pscustomobject]@{
            Name = $p[0]
            TotalGb = [math]::Round([double]$p[1] / 1024, 1)
            UsedGb = [math]::Round([double]$p[2] / 1024, 1)
            Driver = $p[3]
        }
        Ok "GPU: $($gpu.Name), $($gpu.TotalGb) GB, driver $($gpu.Driver) ($($gpu.UsedGb) GB in use right now)"
        if ($gpu.TotalGb -lt 8) { Warn "the GPU has under 8 GB; local models will be slow. A cloud OpenAI-compatible endpoint is an option (docs/MODELS.md)." }
    }
}
if ($null -eq $gpu) {
    Warn "no NVIDIA GPU detected. The stack runs, but local AI will be very slow or won't run; you can point LLM_BASE_URL at a cloud OpenAI-compatible endpoint instead (docs/MODELS.md)."
}

if ($gpu -and -not $SkipGpuTest) {
    Write-Host "    testing that containers can see the GPU (first time downloads ~100 MB)..." -ForegroundColor DarkGray
    if ((Invoke-Native docker @("run", "--rm", "--gpus", "all", $CudaTestImage, "nvidia-smi", "-L") -Quiet) -eq 0) {
        Ok "containers can use the GPU"
    } else {
        Write-Host $script:LastOutput -ForegroundColor DarkGray
        Stop-Install "containers cannot use the GPU, so the model server would fail to start. In Docker Desktop: Settings > Resources > WSL integration on, and update the NVIDIA driver; then run this again (or -SkipGpuTest to skip this check)."
    }
}

if ($CheckOnly) {
    Write-Host ""
    Write-Host "Check finished; nothing was changed. Warnings: $($warnings.Count)" -ForegroundColor Cyan
    try { Stop-Transcript | Out-Null } catch { }
    exit 0
}

# ---- 2. configuration ----------------------------------------------------------------
Step 2 "configuration (.env)..."
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    Ok ".env already exists - left exactly as it is"
    if (Select-String -Path $envFile -Pattern "^LDR_PASSWORD=change-me-before-first-start\s*$" -Quiet) {
        Warn "LDR_PASSWORD in .env is still the public example value. If research has never run, set your own now; changing it later means re-creating the research account."
    }
} else {
    Copy-Item (Join-Path $root ".env.example") $envFile
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $lines = [IO.File]::ReadAllLines($envFile, $utf8)
    # Fresh random passwords for services that only talk to each other on this PC. Nothing
    # has used the example values yet, so replacing them now is safe; API keys stay blank.
    $generated = New-Object System.Collections.ArrayList
    $lines = $lines | ForEach-Object {
        if ($_ -match "^LDR_PASSWORD=change-me-before-first-start\s*$") { [void]$generated.Add("LDR_PASSWORD"); "LDR_PASSWORD=$(New-Secret)" }
        elseif ($_ -match "^#\s*SEARXNG_SECRET=\s*$") { [void]$generated.Add("SEARXNG_SECRET"); "SEARXNG_SECRET=$(New-Secret)" }
        elseif ($_ -match "^#\s*(ICECAST_(SOURCE|ADMIN|RELAY)_PASSWORD)=") { [void]$generated.Add($Matches[1]); "$($Matches[1])=$(New-Secret)" }
        else { $_ }
    }
    [IO.File]::WriteAllLines($envFile, [string[]]$lines, $utf8)
    Ok "created .env from .env.example (all API keys left blank)"
    Ok "generated local passwords: $($generated -join ', ')"
}

$profiles = @("--profile", "desktop")
if (Select-String -Path $envFile -Pattern '^\s*TOAST_MESH\s*=\s*1\s*$' -Quiet) { $profiles += @("--profile", "mesh") }
$compose = @("compose") + $profiles

# ---- 3. UI packages --------------------------------------------------------------------
Step 3 "workstation UI packages..."
if (Test-Path (Join-Path $root "operator-ui\node_modules\.bin")) { Ok "already installed" }
else {
    if ((Invoke-Native npm @("--prefix", "operator-ui", "ci")) -ne 0) { Stop-Install "installing the UI packages failed - see the npm output above." }
    Ok "installed"
}

# ---- 4. start the stack ------------------------------------------------------------------
Step 4 "starting the stack..."
# Quiet on purpose: piped, Docker prints a line per layer per update - thousands of lines
# that bury anything important. Its output is shown only if a stage fails.
Write-Host "    downloading images (about 5 GB the first time; nothing to do if already present)..." -ForegroundColor DarkGray
if ((Invoke-Native docker ($compose + @("pull", "--quiet", "--ignore-buildable")) -Quiet) -ne 0) {
    Write-Host $script:LastOutput -ForegroundColor DarkGray
    Stop-Install "downloading the Docker images failed - see the output above (check the internet connection and free disk space)."
}
Write-Host "    building the backend image (a minute or two the first time)..." -ForegroundColor DarkGray
if ((Invoke-Native docker ($compose + @("build", "--quiet")) -Quiet) -ne 0) {
    Write-Host $script:LastOutput -ForegroundColor DarkGray
    Stop-Install "building the backend image failed - see the output above."
}
Write-Host "    starting the containers..." -ForegroundColor DarkGray
if ((Invoke-Native docker ($compose + @("up", "-d")) -Quiet) -ne 0) {
    Write-Host $script:LastOutput -ForegroundColor DarkGray
    Stop-Install "docker compose could not start the stack - see the output above."
}
$healthy = $false
$deadline = (Get-Date).AddMinutes(4)
while ((Get-Date) -lt $deadline) {
    try {
        if ((Invoke-WebRequest -Uri "http://127.0.0.1:8400/healthz" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
    Start-Sleep -Seconds 3
}
if (-not $healthy) { Stop-Install "the API did not become healthy. Look at: docker compose logs api --tail 80" }
Ok "API healthy on http://127.0.0.1:8400"

# ---- 5. seed --------------------------------------------------------------------------------
Step 5 "seeding the source list (only on a new database)..."
if ((Invoke-Native docker ($compose + @("exec", "-T", "api", "python", "-m", "toast.seed", "--if-empty"))) -ne 0) {
    Stop-Install "seeding failed - see the output above."
}
Ok $script:LastOutput.Trim().Split("`n")[-1]

# ---- 6. models ----------------------------------------------------------------------------------
Step 6 "AI models..."
if ($SkipModels) {
    Warn "models skipped (-SkipModels) - run 'Install Models.cmd' before using the AI features."
} else {
    $modelProfile = "starter"
    if ($gpu -and $gpu.TotalGb -lt 12) { $modelProfile = "small-8gb" }
    # in-process, so its plan and prompts land in install.log too
    $modelParams = @{ Profile = $modelProfile }
    if ($Yes) { $modelParams.Yes = $true }
    & (Join-Path $PSScriptRoot "models.ps1") @modelParams
    if ($LASTEXITCODE -eq 1) { Warn "the model install stopped - run 'Install Models.cmd' again once the message above is dealt with." }
    elseif ($LASTEXITCODE -ne 0) { Warn "models finished with warnings - read the WARNING lines above." }
}

# ---- 7. verify ----------------------------------------------------------------------------------
Step 7 "verifying..."
if ((Invoke-Native docker ($compose + @("ps", "--format", "{{.Service}}: {{.State}} {{.Health}}")) -Quiet) -eq 0) {
    $script:LastOutput.Split("`n") | Where-Object { $_.Trim() } | ForEach-Object {
        if ($_ -match "running" -and $_ -notmatch "unhealthy") { Ok $_.Trim() } else { Warn "service $($_.Trim())" }
    }
}
try {
    $status = Invoke-RestMethod -Uri "http://127.0.0.1:8400/system/status" -TimeoutSec 15
    if ($status.ok) { Ok "system status: no problems" }
    else {
        $status.problems.PSObject.Properties | ForEach-Object { Warn "system status: $($_.Value)" }
    }
} catch { Warn "could not read http://127.0.0.1:8400/system/status" }
if ((Invoke-Native docker ($compose + @("exec", "-T", "api", "python", "-m", "toast.modelsetup", "status")) -Quiet) -eq 0) {
    Ok "models match their pinned fingerprints (or are not installed yet - see below)"
} else { Warn "an installed model differs from its pinned fingerprint - run: docker compose --profile desktop exec -T api python -m toast.modelsetup status" }
$script:LastOutput.Split("`n") | Where-Object { $_ -match "not installed|NOTE|DIFFERENT" } | ForEach-Object { Warn $_.Trim() }

# ---- 8. launch + summary ---------------------------------------------------------------------
Step 8 "done."
$model = (Select-String -Path $envFile -Pattern "^LLM_MODEL=(.+)$" | Select-Object -First 1)
Write-Host ""
Write-Host "  Workstation:  http://localhost:3005" -ForegroundColor Green
Write-Host "  API:          http://127.0.0.1:8400   (status: /system/status)" -ForegroundColor Green
Write-Host "  Site preview: http://127.0.0.1:8400/site/" -ForegroundColor Green
if ($model) { Write-Host "  AI model:     $($model.Matches[0].Groups[1].Value)" -ForegroundColor Green }
Write-Host "  Log:          $logFile"
if ($warnings.Count) {
    Write-Host ""
    Write-Host "  Needs your attention ($($warnings.Count)):" -ForegroundColor Yellow
    $warnings | ForEach-Object { Write-Host "   - $_" -ForegroundColor Yellow }
}
Write-Host ""
Write-Host "  Optional next steps: add API keys in the app's OPTIONS (docs/API-KEYS.md);"
Write-Host "  TOAST_MESH=1 in .env for the MESH tab; 'Update and Launch.cmd' to update later."
try { Stop-Transcript | Out-Null } catch { }

if (-not $NoLaunch) {
    & (Join-Path $PSScriptRoot "launch.ps1")
}
