; Inno Setup 6 manifest for the Windows x64 bundle.
; Build the PyInstaller executable first; this script never embeds credentials.

#define MyAppName "WH6成交采集器"
#ifndef MyAppVersion
#define MyAppVersion "0.2.1"
#endif
#ifndef MyAppReleaseDir
#define MyAppReleaseDir "..\\releases\\0.2.1"
#endif
#ifndef MyAppOutputBaseFilename
#define MyAppOutputBaseFilename "WH6成交采集器-0.2.1-Setup"
#endif
#define MyAppPublisher "LTM WEB"
#define MyAppExeName "WH6成交采集器.exe"
#define BuildDir "..\\dist"

[Setup]
AppId={{B7C23B59-4E4E-4E4F-BD49-6D77A0DF9A1F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir={#MyAppReleaseDir}
OutputBaseFilename={#MyAppOutputBaseFilename}
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Uninstallable=yes
CloseApplications=force
RestartApplications=no
CloseApplicationsFilter=WH6成交采集器.exe
; Application data and device token live in %LOCALAPPDATA%\WH6成交采集器,
; outside this program directory, so uninstall does not remove local queue data.

[Files]
Source: "{#BuildDir}\WH6成交采集器.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
