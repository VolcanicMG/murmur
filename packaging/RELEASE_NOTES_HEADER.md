## Install

1. Download `murmur-windows.zip` below.
2. Unzip it (keep the `murmur` folder together) and run `murmur\murmur.exe`.

The first launch opens a short setup. There is no autostart; start murmur yourself when you want it.

## Windows will warn you

This build is **not code-signed**: murmur has no code-signing certificate yet, so Windows SmartScreen shows "Windows protected your PC" the first time. Click **More info**, then **Run anyway**. The warning is about the missing signature, not a detected problem; the checks below prove the file is the one this repository built.

## Verify the download

Compare the checksum against `SHA256SUMS.txt` (PowerShell):

```powershell
(Get-FileHash .\murmur-windows.zip -Algorithm SHA256).Hash.ToLower()
Get-Content .\SHA256SUMS.txt
```

The two hashes must match. While this repository is public, releases also carry a build attestation, which confirms the zip was built by its release workflow (needs the [GitHub CLI](https://cli.github.com)):

```powershell
gh attestation verify .\murmur-windows.zip --repo @REPO@
```

Third-party licenses: `THIRD_PARTY_NOTICES.md` sits next to `murmur.exe`.

---

