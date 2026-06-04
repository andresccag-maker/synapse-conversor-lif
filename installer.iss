; SYN APSE — Conversor LIF · Inno Setup script
;
; Genera el instalador Windows: dist\SYN-APSE-Conversor-LIF-Setup-{version}.exe
;
; Se invoca desde build_windows.ps1 con los #define inyectados:
;   /DAppVersion=0.3.0
;   /DAppFolder=SYN_APSE_Conversor_LIF
;   /DAppName="SYN APSE Conversor LIF"
;
; Características:
;   - Atajo en menú Inicio (siempre) y en escritorio (opcional).
;   - Detecta WebView2 Evergreen Runtime; si falta, lanza el bootstrapper
;     oficial de Microsoft (incluido en assets\).
;   - Desinstalable desde "Aplicaciones y características".

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppFolder
  #define AppFolder  "SYN_APSE_Conversor_LIF"
#endif
#ifndef AppName
  #define AppName    "SYN APSE Conversor LIF"
#endif

#define AppPublisher "Axiom Bio"
#define AppURL       "https://axiombio.tech/apps/lif-converter"
#define AppExeName   AppFolder + ".exe"

[Setup]
AppId={{B5F2A1A0-3D60-4B7A-8E1F-1E2C9D3F4A50}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppFolder}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=
OutputDir=dist
OutputBaseFilename=SYN-APSE-Conversor-LIF-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
MinVersion=10.0.17763

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; App bundle de PyInstaller (carpeta completa)
Source: "dist\{#AppFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Bootstrapper de WebView2 — descargado por build_windows.ps1 si falta (ver [Code])
; ExternalSize solo aplica con Flags:external; aquí el bootstrapper se compila
; dentro del .exe (descargado por build_windows.ps1) y se borra tras instalar.
Source: "assets\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: WebView2NotInstalled

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Instala WebView2 si no está (pywebview lo necesita en Windows).
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; \
  Parameters: "/silent /install"; \
  StatusMsg: "Instalando Microsoft Edge WebView2 Runtime..."; \
  Check: WebView2NotInstalled; Flags: waituntilterminated
; Lanza la app al final si el usuario quiere.
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
  Flags: nowait postinstall skipifsilent

[Code]
function WebView2NotInstalled: Boolean;
var
  Version: String;
begin
  // Clave canónica del Evergreen Runtime (HKLM 64-bit primero, luego 32-bit, luego HKCU).
  Result := True;
  if RegQueryStringValue(HKLM64, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'pv', Version) and (Version <> '') then
    Result := False
  else if RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'pv', Version) and (Version <> '') then
    Result := False
  else if RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'pv', Version) and (Version <> '') then
    Result := False;
end;
