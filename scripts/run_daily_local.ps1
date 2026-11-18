# SmartBuy daily price check from this PC (Amazon and Myntra block GitHub's servers, so this cannot run there).
#   1. store_check   : fetch every registered store page of every product and record its price
#   2. discovery     : read Amazon / Myntra seller lists (--no-search: web search runs in GitHub's smartbuy-discover)
#   3. sale_dates    : weekly web search for announced dates of upcoming sales (finds wait for admin approval)
#
# Installed by scripts/install_daily_task.ps1 as the Windows scheduled task "SmartBuy daily price check".
# Missed runs (PC off) start as soon as the PC is back on. Log: logs/daily-local.log (last ~5,000 lines kept).
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$Log = Join-Path $Root "logs\daily-local.log"
$Lock = Join-Path $Root "logs\.daily-local.lock"
New-Item -ItemType Directory -Force (Join-Path $Root "logs") | Out-Null

# one run at a time
try { $lockStream = [System.IO.File]::Open($Lock, "OpenOrCreate", "ReadWrite", "None") }
catch { Add-Content $Log "$(Get-Date -Format s) previous run still active; skipped"; exit 0 }

$Py = Join-Path $Root "backend\.venv\Scripts\python.exe"
Set-Location (Join-Path $Root "backend")
$failed = $false
function Run-Step($name, [string[]]$pyArgs) {
    Add-Content $Log "=== $(Get-Date -Format s) $name start"
    # cmd's redirect keeps Python's UTF-8 output as-is (PowerShell 5.1's >> would write UTF-16)
    $env:PYTHONIOENCODING = "utf-8"
    cmd /c "`"$Py`" $($pyArgs -join ' ') >> `"$Log`" 2>&1"
    if ($LASTEXITCODE -ne 0) { Add-Content $Log "=== $(Get-Date -Format s) $name FAILED ($LASTEXITCODE)"; $script:failed = $true }
    else { Add-Content $Log "=== $(Get-Date -Format s) $name ok" }
}
try {
    Run-Step "store_check" @("-m", "app.workers.store_check")
    Run-Step "discovery" @("-m", "app.workers.discovery", "--no-search")
    Run-Step "sale_dates" @("-m", "app.workers.sale_dates")        # weekly: skips itself if it ran < 6 days ago
}
finally {
    $lockStream.Close()
    $lines = Get-Content $Log -Tail 5000
    Set-Content -Path $Log -Value $lines -Encoding utf8
}
if ($failed) { exit 1 }
