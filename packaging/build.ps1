# Build the murmur onedir app on Windows (CI and local): .\packaging\build.ps1 [-Python python] [-Dist dist]
# Mirrors packaging/build.sh's PyInstaller invocation (no WSL staging: the repo is already on a Windows disk).
param([string]$Python = "python", [string]$Dist = "dist")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
& $Python -m PyInstaller --noconfirm --clean --distpath $Dist --workpath build murmur.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
$app = Join-Path $Dist "murmur"
if (-not ((Test-Path (Join-Path $app "murmur.exe")) -and (Test-Path (Join-Path $app "_internal")))) { throw "no onedir app at $app" }
Write-Output "built $app"
