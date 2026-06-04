# SYN APSE — Conversor LIF: build del instalador Windows (.exe).
#
# Requisitos previos:
#   - Python 3.10+ en PATH
#   - Inno Setup 6 en PATH (o ajustar ISCC_PATH)
#         choco install innosetup
#     o descargar de https://jrsoftware.org/isdl.php
#   - assets/icon.ico  (lo genera assets/generate_icon.py)
#
# Salida:
#   dist/SYN-APSE-Conversor-LIF-Setup-{version}.exe
#
# Hooks de firma Authenticode listos (no se ejecutan si falta WIN_SIGN_CERT).

[CmdletBinding()]
param(
  [string]$Python = "python",
  [string]$IsccPath = "ISCC.exe"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$AppName = "SYN APSE Conversor LIF"
$AppFolder = "SYN_APSE_Conversor_LIF"   # nombre sin acentos para PyInstaller en Windows

# ---- Versionado (única fuente de verdad: core.py) ----
$Version = (Select-String -Path "core.py" -Pattern '^APP_VERSION\s*=\s*"([^"]+)"' |
            ForEach-Object { $_.Matches[0].Groups[1].Value } |
            Select-Object -First 1)
if (-not $Version) { throw "No pude leer APP_VERSION de core.py" }
Write-Host "[build] version: $Version" -ForegroundColor Cyan

# ---- venv + dependencias ----
if (-not (Test-Path ".venv")) {
  Write-Host "[build] creando .venv"
  & $Python -m venv .venv
}
$VenvPython = ".\.venv\Scripts\python.exe"
$VenvPip = ".\.venv\Scripts\pip.exe"

Write-Host "[build] instalando dependencias"
& $VenvPip install --upgrade pip | Out-Null
& $VenvPip install -r requirements.txt | Out-Null
& $VenvPip install "pyinstaller>=6.0" | Out-Null

# ---- Validar icono ----
Write-Host "[build] validando assets/icon.png + assets/icon.ico"
& $VenvPython -c @"
import sys
from pathlib import Path
from PIL import Image
import numpy as np

png = Path('assets/icon.png')
ico = Path('assets/icon.ico')
if not png.exists() or not ico.exists():
    sys.exit('[icon-validate] faltan assets — ejecuta: .venv/Scripts/python.exe assets/generate_icon.py')
img = Image.open(png)
if img.mode != 'RGBA':
    sys.exit(f'[icon-validate] {png} no es RGBA (mode={img.mode}).')
arr = np.asarray(img)
corners = [int(arr[0,0,3]), int(arr[0,-1,3]), int(arr[-1,0,3]), int(arr[-1,-1,3])]
if any(c > 8 for c in corners):
    sys.exit(f'[icon-validate] {png} tiene esquinas opacas (alphas={corners}).')
print('[icon-validate] OK')
"@
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# ---- Limpieza ----
Write-Host "[build] limpiando build/ dist/ previos"
if (Test-Path build)            { Remove-Item -Recurse -Force build }
if (Test-Path dist)             { Remove-Item -Recurse -Force dist }
if (Test-Path "$AppName.spec")  { Remove-Item -Force "$AppName.spec" }
if (Test-Path "$AppFolder.spec"){ Remove-Item -Force "$AppFolder.spec" }

# ---- PyInstaller (onedir, --windowed) ----
# En Windows el separador de --add-data es ';' (en macOS/Linux es ':').
Write-Host "[build] empaquetando con PyInstaller"
& $VenvPython -m PyInstaller `
  --name "$AppFolder" `
  --windowed `
  --icon "assets\icon.ico" `
  --add-data "web;web" `
  --noconfirm `
  --clean `
  --collect-all webview `
  --collect-all readlif `
  app.py

$AppDist = "dist\$AppFolder"
if (-not (Test-Path $AppDist)) { throw "PyInstaller no produjo $AppDist" }

# ---- WebView2 bootstrapper (lo embebe Inno Setup) ----
$WebView2Setup = "assets\MicrosoftEdgeWebview2Setup.exe"
if (-not (Test-Path $WebView2Setup)) {
  Write-Host "[build] descargando MicrosoftEdgeWebview2Setup.exe (bootstrapper Evergreen)"
  Invoke-WebRequest `
    -Uri "https://go.microsoft.com/fwlink/p/?LinkId=2124703" `
    -OutFile $WebView2Setup `
    -UseBasicParsing
}

# ---- Firma Authenticode (opcional) ----
if ($env:WIN_SIGN_CERT) {
  Write-Host "[build] firmando con $env:WIN_SIGN_CERT"
  & signtool sign /fd SHA256 /f $env:WIN_SIGN_CERT `
    /p $env:WIN_SIGN_PASS /t http://timestamp.digicert.com `
    "$AppDist\$AppFolder.exe"
} else {
  Write-Host "[build] (sin firma — define WIN_SIGN_CERT/WIN_SIGN_PASS para Authenticode)"
}

# ---- Inno Setup ----
if (-not (Get-Command $IsccPath -ErrorAction SilentlyContinue)) {
  $candidate = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
  if (Test-Path $candidate) { $IsccPath = $candidate }
}
if (-not (Get-Command $IsccPath -ErrorAction SilentlyContinue)) {
  throw "ISCC.exe no encontrado. Instala Inno Setup 6 (choco install innosetup)."
}

Write-Host "[build] ejecutando $IsccPath installer.iss"
& $IsccPath "/DAppVersion=$Version" "/DAppFolder=$AppFolder" "/DAppName=$AppName" installer.iss
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$Installer = "dist\SYN-APSE-Conversor-LIF-Setup-$Version.exe"
if (Test-Path $Installer) {
  Write-Host "[build] OK"
  Write-Host "  -> $Installer" -ForegroundColor Green
} else {
  throw "Inno Setup no produjo $Installer"
}
