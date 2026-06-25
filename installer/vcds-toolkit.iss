; Inno Setup script for the VCDS Toolkit desktop GUI.
;
; Prerequisite: build the PyInstaller bundle first so "dist\VCDS Toolkit\" exists:
;     pyinstaller installer\vcds_gui.spec --clean --noconfirm
;
; Then compile the installer (version can be overridden from the command line):
;     iscc installer\vcds-toolkit.iss /DMyAppVersion=0.1.0
;
; Produces: installer\Output\VCDS-Toolkit-Setup-<version>.exe

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#define MyAppName "VCDS Toolkit"
#define MyAppPublisher "DeltaModTech"
#define MyAppExeName "VCDS Toolkit.exe"
#define MyAppURL "https://github.com/JWalen/VAGScanner"

; Path to the PyInstaller one-folder output (relative to this script).
#define BuildDir "..\dist\VCDS Toolkit"

[Setup]
AppId={{B8E5B7B2-4C2A-4F1E-9C3D-VCDSTOOLKIT01}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Always install per-user (no admin needed) so every install/upgrade lands in the
; SAME location and replaces cleanly — avoids parallel per-user/per-machine copies.
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=VCDS-Toolkit-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=app.ico
; Let a silent update close the running app (the updater relaunches it itself).
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The entire PyInstaller bundle.
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
{ Remove any previous install (either per-user or per-machine) before installing,
  so an upgrade never ends up as a parallel copy alongside the old version. }
function GetUninstaller(RootKey: Integer): String;
var
  s: String;
begin
  s := '';
  RegQueryStringValue(RootKey,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B8E5B7B2-4C2A-4F1E-9C3D-VCDSTOOLKIT01}_is1',
    'UninstallString', s);
  Result := s;
end;

procedure RunPrevUninstaller(RootKey: Integer);
var
  s: String;
  rc: Integer;
begin
  s := GetUninstaller(RootKey);
  if s = '' then
    Exit;
  if (Length(s) >= 2) and (s[1] = '"') then
    s := Copy(s, 2, Length(s) - 2);
  Exec(s, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, rc);
end;

function InitializeSetup(): Boolean;
begin
  RunPrevUninstaller(HKEY_CURRENT_USER);
  RunPrevUninstaller(HKEY_LOCAL_MACHINE);
  Result := True;
end;
