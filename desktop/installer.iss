; DoosraSetup.exe: installs desktop/dist/Doosra for the current user (no admin prompt).
; Built by desktop/build.py, which passes the version:
;   ISCC /DAppVersion=2.4.0 desktop\installer.iss   ->  desktop\dist\DoosraSetup-2.4.0.exe
;
; The app lives in %LOCALAPPDATA%\Programs\Doosra; what it downloads and saves (the cricket
; database, models, chats) lives in %LOCALAPPDATA%\Doosra and is kept on uninstall unless the
; person says otherwise.

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z (desktop/build.py does)
#endif
#ifndef SourceDir
  #define SourceDir "dist\Doosra"
#endif

[Setup]
AppId={{6C3E8F2A-4D1B-4B7E-9A55-D005A0C1CE7A}
AppName=Doosra
AppVersion={#AppVersion}
AppVerName=Doosra {#AppVersion}
AppPublisher=Doosra
AppPublisherURL=https://github.com/sarthak213/Doosra
AppSupportURL=https://github.com/sarthak213/Doosra/issues
DefaultDirName={autopf}\Doosra
DefaultGroupName=Doosra
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=dist
OutputBaseFilename=DoosraSetup-{#AppVersion}
SetupIconFile=assets\doosra.ico
UninstallDisplayIcon={app}\Doosra.exe
UninstallDisplayName=Doosra
WizardStyle=modern
WizardImageFile=assets\wizard-large-164.bmp,assets\wizard-large-328.bmp
WizardSmallImageFile=assets\wizard-small-55.bmp,assets\wizard-small-110.bmp
Compression=lzma2/max
SolidCompression=yes
; The running app holds the mutex DoosraDesktopApp (desktop/launcher.py): see InitializeSetup below.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; files from an older version (the app folder is replaced as a whole on update)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\Doosra"; Filename: "{app}\Doosra.exe"; Comment: "Cricket analytics, with an AI that runs on this PC"
Name: "{autodesktop}\Doosra"; Filename: "{app}\Doosra.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Doosra.exe"; Description: "{cm:LaunchProgram,Doosra}"; Flags: nowait postinstall skipifsilent
; an update started from inside the app (/UPDATE=1) opens the new version when it's done
Filename: "{app}\Doosra.exe"; Flags: nowait; Check: IsUpdate

[Code]
const
  WebView2Key = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2KeyUser = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Bootstrapper = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

var
  DownloadPage: TDownloadWizardPage;

function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;

// Doosra takes a few seconds to close fully (it unloads its .NET window last).
function WaitForDoosraToClose(Seconds: Integer): Boolean;
var
  I: Integer;
begin
  I := 0;
  while CheckForMutexes('DoosraDesktopApp') and (I < Seconds * 4) do
  begin
    Sleep(250);
    I := I + 1;
  end;
  Result := not CheckForMutexes('DoosraDesktopApp');
end;

// Setup and uninstall need Doosra closed. Asked interactively; a silent run (an update from inside
// the app, which has just closed itself) waits for it instead.
function DoosraClosed(Silent: Boolean): Boolean;
begin
  if Silent then
  begin
    Result := WaitForDoosraToClose(120);
    exit;
  end;
  Result := True;
  while CheckForMutexes('DoosraDesktopApp') do
  begin
    if MsgBox('Doosra is open. Close it, then click OK to continue.', mbError, MB_OKCANCEL) = IDCANCEL then
    begin
      Result := False;
      exit;
    end;
    WaitForDoosraToClose(20);
  end;
end;

function InitializeSetup: Boolean;
begin
  Result := DoosraClosed(WizardSilent or IsUpdate);
end;

function InitializeUninstall: Boolean;
begin
  Result := DoosraClosed(UninstallSilent);
end;

function HasWebView2: Boolean;
var
  Version: String;
begin
  Result := (RegQueryStringValue(HKLM, WebView2Key, 'pv', Version) or
             RegQueryStringValue(HKCU, WebView2KeyUser, 'pv', Version))
            and (Version <> '') and (Version <> '0.0.0.0');
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), 'Getting Microsoft Edge WebView2, which Doosra''s window needs...', nil);
end;

// Windows 11 and up-to-date Windows 10 have WebView2 already; only fetch Microsoft's small installer if not.
function NextButtonClick(CurPageID: Integer): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if (CurPageID = wpReady) and not HasWebView2 then
  begin
    DownloadPage.Clear;
    DownloadPage.Add(WebView2Bootstrapper, 'MicrosoftEdgeWebview2Setup.exe', '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
        if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_HIDE,
                    ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
          MsgBox('Microsoft Edge WebView2 could not be installed (code ' + IntToStr(ResultCode) + '). ' +
                 'Doosra will be installed, but its window needs WebView2: get it from ' +
                 'https://developer.microsoft.com/microsoft-edge/webview2/', mbInformation, MB_OK);
      except
        MsgBox('Microsoft Edge WebView2 could not be downloaded: ' + GetExceptionMessage + #13#10 +
               'Doosra will be installed, but its window needs WebView2: get it from ' +
               'https://developer.microsoft.com/microsoft-edge/webview2/', mbInformation, MB_OK);
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

// Uninstall: the app is removed; the downloaded database and models, chats and settings are kept
// unless the person chooses to delete them (a reinstall then starts where they left off).
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\Doosra');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('Also delete Doosra''s data?' + #13#10#13#10 +
                'This is the cricket database, the downloaded AI models, and your chats, projects and ' +
                'settings, in:' + #13#10 + DataDir + #13#10#13#10 +
                'Keep them if you might install Doosra again.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
