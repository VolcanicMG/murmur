# Launch murmur with the Windows venv; args pass through (e.g. --selftest, learn).
# No args = tray app on pythonw (no console window); with args use python so stdout works.
Set-Location $PSScriptRoot
$py = if ($args.Count) { "python.exe" } else { "pythonw.exe" }
& "$env:USERPROFILE\.murmur\venv\Scripts\$py" -m murmur @args
