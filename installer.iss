; installer.iss - Inno Setup script template
; Usage: Open this file in Inno Setup Compiler and build to create an installer.

[Setup]
AppName=SMTP Unlock Tool
AppVersion=1.0
AppPublisher=SMTP Unlock Tool
DefaultDirName={autopf}\SMTP Unlock Tool
DefaultGroupName=SMTP Unlock Tool
DisableProgramGroupPage=yes
WizardStyle=modern
OutputDir=installer
OutputBaseFilename=SMTP_Unlock_Setup
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
UninstallDisplayIcon={app}\smtp_unlock_gui.exe

[Files]
; Copy all files from dist\smtp_unlock into installer
Source: "dist\smtp_unlock\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SMTP Unlock Tool"; Filename: "{app}\smtp_unlock_gui.exe"
Name: "{autodesktop}\SMTP Unlock Tool"; Filename: "{app}\smtp_unlock_gui.exe"
Name: "{group}\Uninstall SMTP Unlock Tool"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\smtp_unlock_gui.exe"; Description: "Launch SMTP Unlock Tool"; Flags: nowait postinstall skipifsilent
