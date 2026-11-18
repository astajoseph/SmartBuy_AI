# Registers (or updates) the Windows scheduled task that runs scripts/run_daily_local.ps1 every day.
#   powershell -ExecutionPolicy Bypass -File scripts\install_daily_task.ps1 [-Time 10:20]
# Remove it with:  Unregister-ScheduledTask -TaskName "SmartBuy daily price check" -Confirm:$false
param([string]$Time = "10:20")
$Script = Join-Path $PSScriptRoot "run_daily_local.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Script`""
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
# StartWhenAvailable: if the PC was off at $Time, run as soon as it is on again.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "SmartBuy daily price check" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Checks SmartBuy store prices (Amazon, Myntra and others) once a day." -Force | Out-Null
Get-ScheduledTask -TaskName "SmartBuy daily price check" | Select-Object TaskName, State,
    @{n = "NextRun"; e = { (Get-ScheduledTaskInfo $_).NextRunTime } }
