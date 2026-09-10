[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$TimelapseArguments
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$executable = Join-Path $root "app\DailyPhotoTimelapse.exe"

if (Test-Path -LiteralPath $executable) {
    & $executable @TimelapseArguments
    exit $LASTEXITCODE
}

$python = $env:DAILY_PHOTO_PYTHON
$pythonArguments = @()
if ([string]::IsNullOrWhiteSpace($python)) {
    $bundledPython = Join-Path $root ".build-python\python.exe"
    if (Test-Path -LiteralPath $bundledPython) {
        $python = $bundledPython
    } else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $pythonCommand) {
            $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
            $pythonArguments = @("-3")
        }
        if ($null -eq $pythonCommand) {
            throw "Python 3 was not found. Run build.ps1 first or set DAILY_PHOTO_PYTHON."
        }
        $python = $pythonCommand.Source
    }
}

$packages = Join-Path $root ".build-packages"
if (Test-Path -LiteralPath $packages) {
    $env:PYTHONPATH = $packages
}
& $python @pythonArguments (Join-Path $root "src\create_timelapse.py") @TimelapseArguments
exit $LASTEXITCODE
