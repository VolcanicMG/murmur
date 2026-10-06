# Launch murmur with the Windows venv; args pass through (e.g. --selftest, learn)
Set-Location \\wsl.localhost\Ubuntu-24.04\home\ethan\repos\murmur
& C:\Users\ethan\.murmur\venv\Scripts\python.exe -m murmur @args
