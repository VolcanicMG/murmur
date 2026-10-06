# Launch murmur with the Windows venv; args pass through (e.g. --selftest, learn).
# No args = tray app on pythonw (no console window); with args use python so stdout works.
Set-Location \\wsl.localhost\Ubuntu-24.04\home\ethan\repos\murmur
$py = if ($args.Count) { "python.exe" } else { "pythonw.exe" }
& "C:\Users\ethan\.murmur\venv\Scripts\$py" -m murmur @args
