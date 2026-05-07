$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$packageName = "BOR_web_package"
$packageDir = Join-Path $root $packageName
$zipPath = Join-Path $root "$packageName.zip"

$files = @(
    "run_app.bat",
    "requirements.txt",
    "holidays_config.json",
    "streamlit_app.py",
    "main.py",
    "config.py",
    "date_utils.py",
    "models.py",
    "shift_utils.py",
    "night_shift.py",
    "small_night_shift.py",
    "holiday_shift.py",
    "excel_handler.py"
)

if (Test-Path $packageDir) {
    Remove-Item -LiteralPath $packageDir -Recurse -Force
}
if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

New-Item -ItemType Directory -Path $packageDir | Out-Null

foreach ($file in $files) {
    $source = Join-Path $root $file
    if (-not (Test-Path $source)) {
        throw "Missing required file: $file"
    }
    Copy-Item -LiteralPath $source -Destination $packageDir
}

Compress-Archive -Path (Join-Path $packageDir "*") -DestinationPath $zipPath -Force

Write-Host ""
Write-Host "Package created:"
Write-Host $zipPath
Write-Host ""
Write-Host "Send this zip file to others. They can unzip it and double-click run_app.bat."
