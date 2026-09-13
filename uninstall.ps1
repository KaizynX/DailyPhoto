$ErrorActionPreference = "Stop"

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
Remove-ItemProperty -Path $runKey -Name "DailyPhoto" -ErrorAction SilentlyContinue

# Remove the unlock recovery task (including the legacy task with this name).
Unregister-ScheduledTask -TaskName "DailyPhoto" -Confirm:$false -ErrorAction SilentlyContinue

Write-Host "DailyPhoto autostart was removed. Photos and application files were kept."
