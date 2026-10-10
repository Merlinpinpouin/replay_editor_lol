# Moteur lolclip empaqueté (PyInstaller, dossier unique) : lolclip-backend.exe + _internal/
#   python -m PyInstaller packaging/lolclip-backend.spec --noconfirm
# Fichiers livrés (lecture seule, voir app/paths.py : ROOT = sys._MEIPASS) : interface, LUT, scripts Windows, ffmpeg.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
BIN = os.path.join(ROOT, "build", "bin")  # ffmpeg.exe / ffprobe.exe copiés par scripts/build.ps1

datas = [
    (os.path.join(ROOT, "app", "static"), os.path.join("app", "static")),
    (os.path.join(ROOT, "assets", "reference.cube"), "assets"),
    (os.path.join(ROOT, "assets", "vignette_1440.png"), "assets"),
]
for f in ("launch_replay.ps1", "focus_game.ps1", "gameaudio.exe", "ManifestDownloader.exe"):
    p = os.path.join(ROOT, "tools", f)
    if os.path.exists(p):
        datas.append((p, "tools"))
for f in ("ffmpeg.exe", "ffprobe.exe"):
    p = os.path.join(BIN, f)
    if os.path.exists(p):
        datas.append((p, "bin"))

a = Analysis(
    [os.path.join(ROOT, "lolclip.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=["tkinter"],  # écran de repérage (app/cover.py, lancé avec --cover)
    excludes=["matplotlib", "scipy", "pandas", "IPython"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="lolclip-backend", console=False,
          icon=os.path.join(ROOT, "desktop", "src-tauri", "icons", "icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="lolclip-backend")
