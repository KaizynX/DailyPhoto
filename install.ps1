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

# The tray process normally receives unlock notifications itself. This task is a
# recovery path: if that process has exited, the next unlock starts it again.
$service = New-Object -ComObject "Schedule.Service"
$service.Connect()
$folder = $service.GetFolder("\")
$task = $service.NewTask(0)
$task.RegistrationInfo.Description = "Recover DailyPhoto on session unlock"
$task.Settings.Enabled = $true
$task.Settings.StartWhenAvailable = $true
$task.Settings.DisallowStartIfOnBatteries = $false
$task.Settings.StopIfGoingOnBatteries = $false
$task.Settings.AllowHardTerminate = $true
$task.Settings.ExecutionTimeLimit = "PT0S"
# TASK_INSTANCES_IGNORE_NEW = 2
$task.Settings.MultipleInstances = 2

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$unlockTrigger = $task.Triggers.Create(11)
$unlockTrigger.Id = "SessionUnlockRecovery"
$unlockTrigger.UserId = $userId
$unlockTrigger.StateChange = 8
$unlockTrigger.Enabled = $true

$action = $task.Actions.Create(0)
$action.Path = $executable
$action.Arguments = "--startup"
$action.WorkingDirectory = $root

# TASK_CREATE_OR_UPDATE = 6; TASK_LOGON_INTERACTIVE_TOKEN = 3
$null = $folder.RegisterTaskDefinition(
    "DailyPhoto",
    $task,
    6,
    $null,
    $null,
    3,
    $null
)

Write-Host "DailyPhoto will start at sign-in and recover automatically on unlock."
