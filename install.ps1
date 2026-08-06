$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$executable = Join-Path $root "app\DailyPhoto.exe"

if (-not (Test-Path -LiteralPath $executable)) {
    throw "$executable was not found. Run build.ps1 first."
}

$service = New-Object -ComObject "Schedule.Service"
$service.Connect()
$folder = $service.GetFolder("\")
$task = $service.NewTask(0)

$task.RegistrationInfo.Description = "Capture one daily desk photo after logon or unlock"
$task.Settings.Enabled = $true
$task.Settings.StartWhenAvailable = $true
$task.Settings.DisallowStartIfOnBatteries = $false
$task.Settings.StopIfGoingOnBatteries = $false
$task.Settings.AllowHardTerminate = $true
$task.Settings.ExecutionTimeLimit = "PT10M"
$task.Settings.MultipleInstances = 2

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$logonTrigger = $task.Triggers.Create(9)
$logonTrigger.Id = "Logon"
$logonTrigger.UserId = $userId
$logonTrigger.Enabled = $true

$unlockTrigger = $task.Triggers.Create(11)
$unlockTrigger.Id = "SessionUnlock"
$unlockTrigger.UserId = $userId
$unlockTrigger.StateChange = 8
$unlockTrigger.Enabled = $true

$action = $task.Actions.Create(0)
$action.Path = $executable
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

Write-Host "Registered DailyPhoto for logon and session unlock."
