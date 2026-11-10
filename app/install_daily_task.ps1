<#
  OPTIONAL - NOT NEEDED FOR NORMAL USE. Nothing in Packrat runs this script automatically.

  Registers a Windows Task Scheduler job:
    -What update     (default) a daily incremental update of your site archive (new posts since last run)
    -What monitors   checks your monitors every hour and downloads what they found, while Packrat is closed
                     (while Packrat is open it checks them itself)

  Run once from the Packrat folder (normal PowerShell, no admin needed):
      .\app\install_daily_task.ps1 -Username YOUR_NAME -Time "03:30"             # archive update, e6ai
      .\app\install_daily_task.ps1 -Username YOUR_NAME -Site e621 -Out "D:\e621-archive"
      .\app\install_daily_task.ps1 -What monitors
  Remove later with:
      Unregister-ScheduledTask -TaskName "Packrat daily update" -Confirm:$false
      Unregister-ScheduledTask -TaskName "Packrat monitors" -Confirm:$false
  Note: the task runs only while you are logged on (no stored password).
#>
param(
    [ValidateSet("update", "monitors")]
    [string]$What = "update",
    [string]$Username = "",
    [string]$Time = "03:30",
    [string]$Out = "",
    [ValidateSet("e6ai", "e621")]
    [string]$Site = "e6ai"
)
# In the zip this script is in app\; the install folder (archive, data, .venv, ini) is its parent
$root = $PSScriptRoot
if ((Split-Path -Leaf $PSScriptRoot) -eq "app" -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot "packrat"))) {
    $root = Split-Path -Parent $PSScriptRoot
}
$script = Join-Path $PSScriptRoot "run.ps1"
# The task runs hidden, so a missing Python would fail silently (exit 9 in Task Scheduler's "Last Run
# Result"). Warn now if the first start has not set up .venv yet.
if (-not (Test-Path -LiteralPath (Join-Path $root ".venv\Scripts\python.exe"))) {
    Write-Host "Note: .venv does not exist yet. Double-click 'Start Packrat.bat' once first," -ForegroundColor Yellow
    Write-Host "      so Python is found and set up while you can see any message (exit code 9 = no suitable Python)."
}
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 12)
if ($What -eq "monitors") {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Mode monitors" `
        -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) `
        -RepetitionInterval (New-TimeSpan -Hours 1)
    Register-ScheduledTask -TaskName "Packrat monitors" -Action $action -Trigger $trigger `
        -Settings $settings -Description "Packrat: check monitors and download new images" -Force
    Write-Host "Monitors are checked every hour. Log: $root\data\logs\packrat.log"
    exit 0
}
if (-not $Username) {
    Write-Host "Give your username on the site: .\app\install_daily_task.ps1 -Username YOUR_NAME" -ForegroundColor Yellow
    exit 2
}
if (-not $Out) { $Out = Join-Path $root "archive" }
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Username `"$Username`" -Site $Site -Out `"$Out`" -Mode update -OnLowDisk stop" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings.WakeToRun = $true
Register-ScheduledTask -TaskName "Packrat daily update" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Incremental Packrat site archive run ($Site, new posts since last run)" -Force
Write-Host "Scheduled daily at $Time. Logs: $Out\logs"
