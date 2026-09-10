$ErrorActionPreference = "Stop"

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
Remove-ItemProperty -Path $runKey -Name "DailyPhoto" -ErrorAction SilentlyContinue

# Also remove the scheduled-task registration used by older versions.
Unregister-ScheduledTask -TaskName "DailyPhoto" -Confirm:$false -ErrorAction SilentlyContinue

Write-Host "DailyPhoto autostart was removed. Photos and application files were kept."
