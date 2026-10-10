# Construit l'installeur Windows de lolclip :
#   1. moteur Python -> desktop/src-tauri/resources/backend/ (PyInstaller, environnement isolé .venv-build/)
#   2. application Tauri -> desktop/src-tauri/target/release/bundle/nsis/lolclip_<version>_x64-setup.exe
# Prérequis : Python 3.12, Node 20+, Rust (MSVC). ffmpeg et ManifestDownloader sont téléchargés s'ils manquent.
param([switch]$DownloadFfmpeg)
# 'Continue' : les outils (pip, PyInstaller, cargo) écrivent leurs messages sur stderr, ce que PowerShell 5.1
# prendrait pour des erreurs avec 'Stop' ; on vérifie plutôt leur code de sortie (Invoke-Native).
$ErrorActionPreference = 'Continue'
function Invoke-Native([string]$what, [scriptblock]$cmd) {
    & $cmd
    if ($LASTEXITCODE) { Write-Error "$what a échoué (code $LASTEXITCODE)" -ErrorAction Stop }
}
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$build = Join-Path $root 'build'
New-Item -ItemType Directory -Force (Join-Path $build 'bin') | Out-Null

# --- outils tiers ---
$md = Join-Path $root 'tools\ManifestDownloader.exe'
if (-not (Test-Path $md)) {
    Write-Host 'Téléchargement de ManifestDownloader'
    Invoke-WebRequest -ErrorAction Stop 'https://github.com/Morilli/ManifestDownloader/releases/latest/download/ManifestDownloader.exe' -OutFile $md
}
# ffmpeg « essentials » (gyan.dev) livré avec l'appli : bien plus léger que la version « full », et contient tout ce
# qu'utilise lolclip (ddagrab, NVENC, x264, lut3d). Téléchargé une fois (build/ffmpeg.zip), -DownloadFfmpeg le renouvelle.
$zip = Join-Path $build 'ffmpeg.zip'
if ($DownloadFfmpeg -or -not (Test-Path $zip)) {
    Write-Host 'Téléchargement de ffmpeg (gyan.dev, essentials)'
    Invoke-WebRequest -ErrorAction Stop 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $zip
}
$tmp = Join-Path $build 'ffmpeg'
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive $zip $tmp
Get-ChildItem $tmp -Recurse -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination (Join-Path $build 'bin') -Force
$filters = & (Join-Path $build 'bin\ffmpeg.exe') -hide_banner -filters 2>$null | Out-String
$encoders = & (Join-Path $build 'bin\ffmpeg.exe') -hide_banner -encoders 2>$null | Out-String
foreach ($need in 'ddagrab', 'lut3d', 'loudnorm') { if ($filters -notmatch $need) { throw "ffmpeg sans le filtre $need" } }
foreach ($need in 'hevc_nvenc', 'libx264') { if ($encoders -notmatch $need) { throw "ffmpeg sans l'encodeur $need" } }

# --- moteur Python ---
$venv = Join-Path $root '.venv-build'
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) { Invoke-Native 'venv' { python -m venv $venv } }
$py = Join-Path $venv 'Scripts\python.exe'
Invoke-Native 'pip' { & $py -m pip install --quiet --upgrade pip }
Invoke-Native 'pip' { & $py -m pip install --quiet -r requirements.txt pyinstaller }
Invoke-Native 'PyInstaller' { & $py -m PyInstaller packaging\lolclip-backend.spec --noconfirm --log-level WARN `
    --distpath (Join-Path $build 'dist') --workpath (Join-Path $build 'work') }
$res = Join-Path $root 'desktop\src-tauri\resources'
Remove-Item (Join-Path $res 'backend') -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $res | Out-Null
Copy-Item (Join-Path $build 'dist\lolclip-backend') (Join-Path $res 'backend') -Recurse

# --- application Tauri ---
Set-Location (Join-Path $root 'desktop')
Invoke-Native 'npm ci' { npm ci }
Invoke-Native 'tauri build' { npm run build }
Get-ChildItem 'src-tauri\target\release\bundle\nsis\*.exe' | ForEach-Object { Write-Host "Installeur : $($_.FullName)" }
