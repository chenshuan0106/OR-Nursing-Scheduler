$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$pyInstaller = Join-Path $root ".venv\Scripts\pyinstaller.exe"
$spec = Join-Path $root "BOR_web_app.spec"
$distApp = Join-Path $root "dist\BOR_web_app"
$zipPath = Join-Path $root "BOR_web_app_windows.zip"

if (-not (Test-Path $venvPython)) {
    throw "Missing .venv. Please run run_app.bat once first, or create the virtual environment."
}

& $venvPython -m pip install -r (Join-Path $root "requirements.txt")
& $venvPython -m pip install pyinstaller

if (Test-Path (Join-Path $root "build")) {
    Remove-Item -LiteralPath (Join-Path $root "build") -Recurse -Force
}
if (Test-Path $distApp) {
    Remove-Item -LiteralPath $distApp -Recurse -Force
}
if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

& $pyInstaller --clean --noconfirm $spec

Copy-Item -LiteralPath (Join-Path $root "holidays_config.json") -Destination $distApp -Force

Start-Sleep -Seconds 3
Compress-Archive -Path (Join-Path $distApp "*") -DestinationPath $zipPath -Force

Write-Host ""
Write-Host "EXE package created:"
Write-Host $zipPath
Write-Host ""
Write-Host "Send this zip file. The recipient can unzip it and double-click BOR_web_app.exe."
