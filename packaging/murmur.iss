; Inno Setup script: per-machine install into Program Files (admin). Built by the release workflow after PyInstaller:
;   ISCC /DAppVersion=x.y.z packaging\murmur.iss   ->  dist\murmur-setup.exe
; Paths are relative to this file. User data lives in %USERPROFILE%\.murmur and is never touched here.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; AppId is how an update finds the previous install: never change it.
AppId={{1845CF78-9E2B-4EBC-BD58-895A0BA100F8}
AppName=murmur
AppVersion={#AppVersion}
AppVerName=murmur {#AppVersion}
AppPublisher=VolcanicMG
AppPublisherURL=https://github.com/VolcanicMG/murmur
DefaultDirName={autopf}\murmur
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; murmur's single-instance mutex (win.py): setup and uninstall ask the user to quit a running murmur first.
AppMutex=Local\murmur-single-instance
CloseApplications=no
LicenseFile=..\LICENSE
SetupIconFile=murmur.ico
UninstallDisplayIcon={app}\murmur.exe
UninstallDisplayName=murmur
VersionInfoVersion={#AppVersion}
OutputDir=..\dist
OutputBaseFilename=murmur-setup
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "&Start murmur when I sign in to Windows"; Flags: unchecked

[InstallDelete]
; A PyInstaller folder must be replaced whole: files an older version shipped would otherwise linger.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\murmur\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\murmur"; Filename: "{app}\murmur.exe"
Name: "{autodesktop}\murmur"; Filename: "{app}\murmur.exe"; Tasks: desktopicon
Name: "{commonstartup}\murmur"; Filename: "{app}\murmur.exe"; Tasks: startup

[Run]
Filename: "{app}\murmur.exe"; Description: "Start murmur"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
; Remove the Explorer right-click verb murmur registers for the user at tray start.
Filename: "{app}\murmur.exe"; Parameters: "--shell-menu off"; Flags: runhidden; RunOnceId: "shellmenu"
