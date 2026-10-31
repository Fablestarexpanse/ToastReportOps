# Toast Report Workstation - download the AI models safely.
#
# Starts the model server if needed, shows exactly what would be downloaded, asks
# first, then downloads with these protections (see docs\MODELS.md):
#   * official Ollama library models only, no community uploads;
#   * each model is checked against a pinned fingerprint BEFORE and AFTER download;
#   * models too big for your graphics card are skipped;
#   * each model is test-run afterwards.
# Interrupted downloads resume - just run it again.
#
# Options:  -Profile starter | small-8gb | bakeoff-12gb | bakeoff-24gb   (default: starter)
#           -Yes          don't ask before downloading
#           -VramGb N     your graphics card memory, if it can't be detected

param(
    [string]$Profile = "starter",
    [switch]$Yes,
    [double]$VramGb = 0
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Run a native command without PowerShell 5.1 treating its stderr progress as an error.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments, [switch]$Quiet)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = & $Exe @Arguments 2>&1 | ForEach-Object {
            $text = "$_"
            if (-not $Quiet) { Write-Host $text }
            $text
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    $script:LastOutput = ($lines -join "`n")
    return $code
}

function Stop-Models($message) {
    Write-Host ""
    Write-Host "MODEL SETUP STOPPED: $message" -ForegroundColor Red
    exit 1
}

Write-Host "[1/4] checking Docker..." -ForegroundColor DarkYellow
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Stop-Models "Docker is not installed." }
if ((Invoke-Native docker @("info") -Quiet) -ne 0) { Stop-Models "Docker isn't running. Start Docker Desktop and try again." }

Write-Host "[2/4] starting the model server..." -ForegroundColor DarkYellow
if ((Invoke-Native docker @("compose", "--profile", "desktop", "up", "-d", "llm", "api") -Quiet) -ne 0) {
    Write-Host $script:LastOutput -ForegroundColor DarkGray
    Stop-Models "couldn't start the stack. Run it once with ToastReport.cmd, then try again."
}
$ready = $false
$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) {
    if ((Invoke-Native docker @("compose", "--profile", "desktop", "exec", "-T", "llm", "ollama", "list") -Quiet) -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $ready) { Stop-Models "the model server didn't start. Look at: docker compose logs llm --tail 50" }

# graphics card memory, so models that can't fit are skipped
if ($VramGb -le 0 -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    $mib = (& nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
    if ($mib -match "^\s*\d+") { $VramGb = [math]::Round([double]$mib / 1024, 1) }
}
$vramArgs = @()
if ($VramGb -gt 0) {
    $vramArgs = @("--vram-gb", "$VramGb")
    Write-Host "    graphics card memory: $VramGb GB" -ForegroundColor DarkGray
} else {
    Write-Host "    couldn't detect graphics card memory - not checking fit (use -VramGb N)" -ForegroundColor Yellow
}

$setup = @("compose", "--profile", "desktop", "exec", "-T", "api", "python", "-m", "toast.modelsetup")

Write-Host "[3/4] what would be downloaded ($Profile):" -ForegroundColor DarkYellow
if ((Invoke-Native docker ($setup + @("plan", "--profile", $Profile) + $vramArgs)) -ne 0) { Stop-Models "see the message above." }
if (-not $Yes) {
    $answer = Read-Host "Download and verify these now? [Y/n]"
    if ($answer -and $answer -notmatch "^[Yy]") { Write-Host "Cancelled - nothing was downloaded."; exit 0 }
}

Write-Host "[4/4] downloading and verifying (can take a while; safe to interrupt and re-run)..." -ForegroundColor DarkYellow
$code = Invoke-Native docker ($setup + @("install", "--profile", $Profile) + $vramArgs)
if ($code -eq 1) { Stop-Models "see the message above." }
if ($code -ne 0) {
    Write-Host ""
    Write-Host "Finished with warnings - read the WARNING lines above before relying on these models." -ForegroundColor Yellow
}

# the configured model should be one we just verified; offer to fix .env if not
$chat = "qwen3.5:9b"
if ((Invoke-Native docker ($setup + @("chat-model", "--profile", $Profile)) -Quiet) -eq 0) {
    $chat = $script:LastOutput.Trim().Split("`n")[-1].Trim()
}
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    $line = (Get-Content $envFile | Where-Object { $_ -match "^LLM_MODEL=" } | Select-Object -First 1)
    if ($line -and $line.Trim() -ne "LLM_MODEL=$chat") {
        Write-Host ""
        Write-Host "Your .env uses:  $line" -ForegroundColor Yellow
        Write-Host "This profile's verified model is:  LLM_MODEL=$chat" -ForegroundColor Yellow
        Write-Host "(Models from community uploaders are not verified by this installer.)" -ForegroundColor Yellow
        $fix = Read-Host "Switch .env to $chat? A backup is saved as .env.bak [y/N]"
        if ($fix -match "^[Yy]") {
            Copy-Item $envFile "$envFile.bak" -Force
            $updated = Get-Content $envFile | ForEach-Object {
                if ($_ -match "^LLM_MODEL=") { "LLM_MODEL=$chat" } else { $_ }
            }
            [IO.File]::WriteAllLines($envFile, $updated)
            Write-Host "Updated .env (backup: .env.bak). Restart the app for it to take effect." -ForegroundColor Green
            Write-Host "Note: a model chosen in OPTIONS inside the app overrides .env." -ForegroundColor DarkGray
        }
    }
}
Write-Host ""
Write-Host "Models are ready." -ForegroundColor Green
