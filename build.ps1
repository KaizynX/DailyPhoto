$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = $env:DAILY_PHOTO_PYTHON
$pythonArguments = @()

if ([string]::IsNullOrWhiteSpace($python)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
        $pythonArguments = @("-3")
    }
    if ($null -eq $pythonCommand) {
        throw "Python 3 was not found. Install Python or set DAILY_PHOTO_PYTHON to its executable path."
    }
    $python = $pythonCommand.Source
} elseif (-not (Test-Path -LiteralPath $python)) {
    throw "The Python executable configured in DAILY_PHOTO_PYTHON was not found: $python"
}

$packages = Join-Path $root ".build-packages"
$dist = Join-Path $root "app"
$work = Join-Path $root ".build"

if (-not (Test-Path -LiteralPath (Join-Path $packages "cv2")) -or
    -not (Test-Path -LiteralPath (Join-Path $packages "PyInstaller"))) {
    & $python @pythonArguments -m pip install --disable-pip-version-check --target $packages `
        "opencv-python-headless==4.12.0.88" "pyinstaller==6.15.0"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install build dependencies."
    }
}

$env:PYTHONPATH = $packages
& $python @pythonArguments -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onefile `
    --name "DailyPhoto" `
    --distpath $dist `
    --workpath $work `
    --specpath $work `
    --paths $packages `
    --collect-all cv2 `
    (Join-Path $root "src\daily_photo.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

Write-Host "Built: $(Join-Path $dist 'DailyPhoto.exe')"
