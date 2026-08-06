$ErrorActionPreference = "Stop"

$service = New-Object -ComObject "Schedule.Service"
$service.Connect()
$folder = $service.GetFolder("\")

try {
    $folder.DeleteTask("DailyPhoto", 0)
    Write-Host "Removed the DailyPhoto task. Photos and application files were kept."
}
catch {
    if ($_.Exception.Message -match "cannot find") {
        Write-Host "The DailyPhoto task does not exist."
    }
    else {
        throw
    }
}
