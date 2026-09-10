$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$executable = Join-Path $root "app\DailyPhoto.exe"

if (-not (Test-Path -LiteralPath $executable)) {
    throw "$executable was not found. Run build.ps1 first."
}

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$command = '"{0}" --startup' -f $executable
New-Item -Path $runKey -Force | Out-Null
New-ItemProperty -Path $runKey -Name "DailyPhoto" -PropertyType String -Value $command -Force | Out-Null

# Remove the scheduled-task registration used by older DailyPhoto versions.
Unregister-ScheduledTask -TaskName "DailyPhoto" -Confirm:$false -ErrorAction SilentlyContinue

Write-Host "DailyPhoto will now start when you sign in."
