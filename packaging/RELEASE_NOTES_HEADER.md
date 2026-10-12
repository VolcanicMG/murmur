## Install or update

1. Download `murmur-setup.exe` below and run it.
2. Approve the Windows permission prompt (murmur installs into Program Files).
3. Start murmur from the Start menu.

To update, run the new `murmur-setup.exe` over the old install: quit murmur from the tray first when setup asks. Your settings, history and models live in `%USERPROFILE%\.murmur` and are kept.

The first launch opens a short setup. murmur only starts with Windows if you tick that option in the installer.

**Portable alternative:** `murmur-windows.zip` is the same app without an installer. Unzip it (keep the `murmur` folder together) and run `murmur\murmur.exe`.

## Windows will warn you

This build is **not code-signed**: murmur has no code-signing certificate yet, so Windows SmartScreen shows "Windows protected your PC" the first time. Click **More info**, then **Run anyway**. The warning is about the missing signature, not a detected problem; the checks below prove the file is the one this repository built.

## Verify the download

Compare the checksum against `SHA256SUMS.txt` (PowerShell):

```powershell
(Get-FileHash .\murmur-setup.exe -Algorithm SHA256).Hash.ToLower()
Get-Content .\SHA256SUMS.txt
```

The hash must match the line for that file. While this repository is public, releases also carry a build attestation, which confirms the file was built by its release workflow (needs the [GitHub CLI](https://cli.github.com)):

```powershell
gh attestation verify .\murmur-setup.exe --repo @REPO@
```

Uninstalling (Settings › Apps) removes the program but leaves `%USERPROFILE%\.murmur`; delete that folder too if you want everything gone, including the downloaded model. Third-party licenses: `THIRD_PARTY_NOTICES.md` sits next to `murmur.exe`.

---
