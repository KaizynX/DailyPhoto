$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
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
            throw "Python 3 was not found. Install Python or set DAILY_PHOTO_PYTHON to its executable path."
        }
        $python = $pythonCommand.Source
    }
} elseif (-not (Test-Path -LiteralPath $python)) {
    throw "The Python executable configured in DAILY_PHOTO_PYTHON was not found: $python"
}

$packages = Join-Path $root ".build-packages"
$dist = Join-Path $root "app"
$work = Join-Path $root ".build"
$model = Join-Path $root "models\face_detection_yunet_2023mar.onnx"
$pythonPrefix = (& $python @pythonArguments -c "import sys; print(sys.base_prefix)").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($pythonPrefix)) {
    throw "Failed to locate the Python runtime directory."
}
$tclData = Join-Path $pythonPrefix "tcl\tcl8.6"
$tkData = Join-Path $pythonPrefix "tcl\tk8.6"

if (-not (Test-Path -LiteralPath $model)) {
    throw "Face detection model was not found: $model"
}
if (-not (Test-Path -LiteralPath (Join-Path $tclData "init.tcl")) -or
    -not (Test-Path -LiteralPath (Join-Path $tkData "tk.tcl"))) {
    throw "Tcl/Tk runtime files were not found next to Python: $python"
}

if (-not (Test-Path -LiteralPath (Join-Path $packages "cv2")) -or
    -not (Test-Path -LiteralPath (Join-Path $packages "PyInstaller")) -or
    -not (Test-Path -LiteralPath (Join-Path $packages "pystray"))) {
    & $python @pythonArguments -m pip install --disable-pip-version-check --target $packages `
        "opencv-python-headless==4.12.0.88" "pillow==11.3.0" `
        "pystray==0.19.5" "pyinstaller==6.15.0"
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
    --add-data "$model;models" `
    --add-data "$tclData;_tcl_data" `
    --add-data "$tkData;_tk_data" `
    (Join-Path $root "src\daily_photo.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

& $python @pythonArguments -m PyInstaller `
    --noconfirm `
    --clean `
    --console `
    --onefile `
    --name "DailyPhotoTimelapse" `
    --distpath $dist `
    --workpath $work `
    --specpath $work `
    --paths $packages `
    --collect-all cv2 `
    --add-data "$model;models" `
    (Join-Path $root "src\create_timelapse.py")
if ($LASTEXITCODE -ne 0) {
    throw "Timelapse PyInstaller build failed."
}

Write-Host "Built: $(Join-Path $dist 'DailyPhoto.exe')"
Write-Host "Built: $(Join-Path $dist 'DailyPhotoTimelapse.exe')"
