; MNT Installer Script for Inno Setup
; Production NSE equity trading system

#define MyAppName "MNT"
#define MyAppVersion "1.0.1"
#define MyAppPublisher "MNT Trading"
#define MyAppExeName "MNT.exe"
#define MyAppURL "https://github.com/sp159713-eng/MNT"

[Setup]
; Basic app info
AppId={{A3F8B2C1-4D5E-6F7A-8B9C-0D1E2F3A4B5C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Output settings
OutputDir=installer_output
OutputBaseFilename=MNT_Setup_{#MyAppVersion}
; Compression
Compression=lzma2
SolidCompression=yes
; Modern look
WizardStyle=modern
; Privileges
PrivilegesRequired=lowest
; Architecture
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Main executable and all dependencies from dist\MNT\
Source: "dist\MNT\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "artifacts\production.joblib"; DestDir: "{app}\artifacts"; Flags: onlyifdoesntexist
; NOTE: Don't use "Flags: ignoreversion" on any shared system files

[Dirs]
; Create empty directories for data that will be generated
Name: "{app}\artifacts"; Permissions: users-full
Name: "{app}\data_cache"; Permissions: users-full

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up generated data on uninstall
Type: filesandordirs; Name: "{app}\artifacts"
Type: filesandordirs; Name: "{app}\data_cache"

[Code]
procedure InitializeWizard();
var
  InfoPage: TOutputMsgMemoWizardPage;
begin
  InfoPage := CreateOutputMsgMemoPage(wpWelcome,
    'Important Information', 'Please read before continuing',
    'Review the notes below, then click Next to continue.',
    'MNT is a production trading system for NSE equity.' + #13#10 + #13#10 +
    'Key features:' + #13#10 +
    '• LightGBM ranker with +46bp excess return' + #13#10 +
    '• Equal-weighted 6 names, monthly rebalance' + #13#10 +
    '• Paper trading by default, live orders disarmed' + #13#10 + #13#10 +
    'Data folders (artifacts\ and data_cache\) will be created next to the executable. ' +
    'These contain models and cached market data that persist between runs.' + #13#10 + #13#10 +
    'CAUTION: This is a real trading system. Verify paper trading mode before connecting to a live broker.');

  InfoPage.RichEditViewer.Height := ScaleY(150);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsWin64 then
  begin
    MsgBox('This application requires a 64-bit version of Windows.', mbError, MB_OK);
    Result := False;
  end;
end;
