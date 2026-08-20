# Build the Shruti desktop exe. One command, from anywhere:
#   .\desktop\build.ps1
# Produces desktop\dist\Shruti\Shruti.exe and release\Shruti-Desktop-win64.zip.
#
# Uses its own venv (.venv-desktop) so dev-only packages never leak into the
# bundle. Needs: uv, Node 20+, and ffmpeg (winget Gyan.FFmpeg) on the machine.
$ErrorActionPreference = "Continue"
$env:Path = "$env:LOCALAPPDATA\Microsoft\WinGet\Links;" + [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

Write-Host "[1/5] Web build..." -ForegroundColor Cyan
Push-Location apps\web
if (-not (Test-Path node_modules)) { npm install --no-fund --no-audit }
npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "web build failed" }
Pop-Location

Write-Host "[2/5] Desktop venv (CPU-only)..." -ForegroundColor Cyan
$venv = Join-Path $repo "desktop\.venv-desktop"
if (-not (Test-Path $venv)) { uv venv $venv --python 3.12 }
uv pip install --python "$venv\Scripts\python.exe" -r desktop\requirements.txt `
    -e packages\core -e apps\api -e apps\worker
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "[3/5] Bundling ffmpeg..." -ForegroundColor Cyan
$ffdir = Join-Path $repo "desktop\ffmpeg"
New-Item -ItemType Directory -Force $ffdir | Out-Null
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
if (-not $ffmpeg -or -not $ffprobe) { throw "ffmpeg/ffprobe not found - winget install Gyan.FFmpeg" }
Copy-Item $ffmpeg.Source, $ffprobe.Source $ffdir -Force

Write-Host "[4/5] PyInstaller..." -ForegroundColor Cyan
& "$venv\Scripts\pyinstaller.exe" --noconfirm --clean `
    --distpath desktop\dist --workpath desktop\buildwork desktop\shruti.spec
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

Write-Host "[5/5] Zipping..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force (Join-Path $repo "release") | Out-Null
$zip = Join-Path $repo "release\Shruti-Desktop-win64.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $repo "desktop\dist\Shruti") -DestinationPath $zip
Write-Host ""
Write-Host "Done:" -ForegroundColor Green
Write-Host "  run it now:  desktop\dist\Shruti\Shruti.exe"
Write-Host "  share this:  release\Shruti-Desktop-win64.zip (unzip anywhere, run Shruti.exe)"
