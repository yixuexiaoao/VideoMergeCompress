[Setup]
AppId={{8DC85959-8D03-4F3B-A93C-D2F0D0A1D99C}
AppName=VideoMergeCompress
AppVersion=0.1.2
DefaultDirName={localappdata}\Programs\VideoMergeCompress
DefaultGroupName=VideoMergeCompress
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\release
OutputBaseFilename=VideoMergeCompress-0.1.2-Setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\VideoMergeCompress.exe
[Files]
Source: "..\dist\VideoMergeCompress\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"
[Icons]
Name: "{group}\VideoMergeCompress"; Filename: "{app}\VideoMergeCompress.exe"
Name: "{group}\Uninstall VideoMergeCompress"; Filename: "{uninstallexe}"
[Run]
Filename: "{app}\VideoMergeCompress.exe"; Description: "Launch VideoMergeCompress"; Flags: nowait postinstall skipifsilent
