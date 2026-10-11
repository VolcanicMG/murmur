# Build the murmur onedir app on Windows (CI and local): .\packaging\build.ps1 [-Python python] [-Dist dist]
# Mirrors packaging/build.sh's PyInstaller invocation (no WSL staging: the repo is already on a Windows disk).
param([string]$Python = "python", [string]$Dist = "dist")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path "packaging/models/parakeet/config.json")) {
    & $Python -m murmur fetch-model  # ~640 MB, once; the spec bundles the folder
    if ($LASTEXITCODE -ne 0) { throw "fetch-model failed ($LASTEXITCODE)" }
}
if (-not (Test-Path "packaging/models/silero/silero_vad.onnx")) {
    & $Python -m murmur fetch-vad  # 2 MB Silero VAD for hands-free endpointing
    if ($LASTEXITCODE -ne 0) { throw "fetch-vad failed ($LASTEXITCODE)" }
}
& $Python -m PyInstaller --noconfirm --clean --distpath $Dist --workpath build murmur.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
$app = Join-Path $Dist "murmur"
if (-not ((Test-Path (Join-Path $app "murmur.exe")) -and (Test-Path (Join-Path $app "_internal")))) { throw "no onedir app at $app" }
Copy-Item LICENSE, THIRD_PARTY_NOTICES.md $app  # next to murmur.exe
Write-Output "built $app"
