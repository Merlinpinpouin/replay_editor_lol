"""Emplacements.

ROOT : fichiers livrés avec l'appli (interface, LUT, scripts) — en lecture seule une fois installée.
DATA : données de l'utilisateur (anciens clients, analyses, vidéos, musiques, réglages, caches), dans l'ordre :
  1. variable d'environnement LOLCLIP_DATA ;
  2. "data_dir" de %LOCALAPPDATA%/lolclip/config.json (pour garder un dossier existant, ex. D:/jeux/LOL/lolclip) ;
  3. depuis les sources : le dossier du projet ; appli installée : %LOCALAPPDATA%/lolclip."""
import json, os, sys

FROZEN = getattr(sys, "frozen", False)  # exécutable PyInstaller (appli de bureau)
ROOT = getattr(sys, "_MEIPASS", None) if FROZEN else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPDATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "lolclip")


def _data_dir():
    if os.environ.get("LOLCLIP_DATA"):
        return os.environ["LOLCLIP_DATA"]
    try:
        d = json.load(open(os.path.join(APPDATA, "config.json"), encoding="utf-8")).get("data_dir")
        if d:
            return d
    except (OSError, ValueError):
        pass
    return APPDATA if FROZEN else ROOT


DATA = _data_dir()
TOOLS = os.path.join(ROOT, "tools")
ASSETS = os.path.join(ROOT, "assets")
BIN = os.path.join(ROOT, "bin")  # ffmpeg / ffprobe livrés avec l'appli (sinon ceux du PATH)
CLIENTS = os.path.join(DATA, "clients")
RENDERS = os.path.join(DATA, "renders")
PROJECTS = os.path.join(DATA, "projects")
MUSIC = os.path.join(DATA, "music")
CACHE = os.path.join(DATA, "cache")
RUN = os.path.join(DATA, "run")  # journal et fichier de fermeture du script de lancement
REPLAYS = os.path.join(os.path.expanduser("~"), "Documents", "League of Legends", "Replays")
MANIFEST_DL = os.path.join(TOOLS, "ManifestDownloader.exe")

if os.path.isdir(BIN) and BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = BIN + os.pathsep + os.environ.get("PATH", "")
