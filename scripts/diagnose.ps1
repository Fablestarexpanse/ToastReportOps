# Toast Report Workstation - read-only health report.
# Collects status, failed jobs, model state and recent logs into diagnostics.txt
# so you can attach it to a bug report or paste it to an assistant. Changes nothing.
# Values from .env (keys, passwords) and key=... query strings are blanked out.

$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$out = Join-Path $root "diagnostics.txt"

$profiles = @("--profile", "desktop")
$dotenv = Join-Path $root ".env"
$secrets = @()
if (Test-Path $dotenv) {
    if (Select-String -Path $dotenv -Pattern '^\s*TOAST_MESH\s*=\s*1\s*$' -Quiet) { $profiles += @("--profile", "mesh") }
    # Everything is treated as a secret except the few settings a report needs to be
    # readable, like which model is configured. An unknown name is always blanked.
    $public = '^(LLM_MODEL|EMBEDDING_MODEL|EMBEDDING_DIM|API_BIND|API_PORT|HEALTH_PORT|LOG_LEVEL|TOAST_DATA_DIR|TOAST_DB_PATH)$'
    foreach ($line in Get-Content $dotenv) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$') {
            $name = $Matches[1]
            $v = $Matches[2].Trim('"', "'")
            if ($v.Length -ge 6 -and $name -notmatch $public) { $secrets += $v }
        }
    }
}

$report = New-Object System.Collections.Generic.List[string]
function Section([string]$title, [scriptblock]$cmd) {
    Write-Host "  $title"
    $report.Add("")
    $report.Add("===== $title =====")
    try { $text = (& $cmd 2>&1 | ForEach-Object { "$_" }) -join "`n" } catch { $text = "ERROR: $_" }
    $report.Add($text)
}

Write-Host "Collecting diagnostics (read-only)..."
Section "version" { git log -1 --format="%h %cd %s" --date=short; git status --short }
Section "containers" { docker compose @profiles ps --format "table {{.Service}}\t{{.State}}\t{{.Status}}" }
Section "status" { docker compose @profiles exec -T api toast --pretty status }
Section "dead jobs" { docker compose @profiles exec -T api toast --pretty dead-jobs --limit 50 }
Section "models" { docker compose @profiles exec -T api python -m toast.modelsetup status }
Section "archive index" { docker compose @profiles exec -T api python -m toast.pipeline.archive_search status }
Section "gpu" { nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv }
foreach ($svc in (docker compose @profiles ps --services 2>$null)) {
    Section "logs: $svc (last 60 lines)" { docker compose @profiles logs --no-color --tail 60 $svc }
}

$text = $report -join "`n"
foreach ($s in $secrets) { $text = $text.Replace($s, "[REDACTED]") }
$text = [regex]::Replace($text, '(?i)((?:api_?key|key|token|password|secret)=)[^&\s"]+', '$1[REDACTED]')
[System.IO.File]::WriteAllText($out, $text, (New-Object System.Text.UTF8Encoding $false))

Write-Host ""
Write-Host "Saved: $out"
Write-Host "Skim it before sharing. Known secrets are blanked, but logs are logs."
