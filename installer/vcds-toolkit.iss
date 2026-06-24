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
; Install per-user by default so no admin rights are required.
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=VCDS-Toolkit-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=app.ico
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
