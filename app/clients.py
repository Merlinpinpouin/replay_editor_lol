"""Clients LoL capables de lire un replay : le jeu installé par le Riot Client (trouvé automatiquement, utilisé quand
il est du même patch que le replay), sinon un ancien client dans clients/<patch>/.
Téléchargement des anciens clients via ManifestDownloader + manifests archivés par Morilli/riot-manifests."""
import ctypes, glob, json, os, re, shutil, subprocess, threading, time, urllib.request
from .paths import CLIENTS, MANIFEST_DL, PROJECTS

MANIFEST_INDEX = "https://raw.githubusercontent.com/Morilli/riot-manifests/master/LoL/EUW1/windows/lol-game-client/{}.txt"
EXE = os.path.join("Game", "League of Legends.exe")
RIOT_DATA = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Riot Games")

_jobs = {}  # patch -> {"state", "files", "total", "log", "error"}


def _candidates():
    """Dossiers d'installation de LoL déclarés par le Riot Client (hors PBE)."""
    out = []
    yml = os.path.join(RIOT_DATA, "Metadata", "league_of_legends.live", "league_of_legends.live.product_settings.yaml")
    try:
        m = re.search(r'^product_install_full_path:\s*"?([^"\r\n]+)"?', open(yml, encoding="utf-8").read(), re.M)
        if m:
            out.append(m.group(1))
    except OSError:
        pass
    try:
        installs = json.load(open(os.path.join(RIOT_DATA, "RiotClientInstalls.json"), encoding="utf-8"))
        out += [p for p in installs.get("associated_client", {}) if "league of legends" in p.lower() and "pbe" not in p.lower()]
    except (OSError, ValueError):
        pass
    try:
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Riot Game league_of_legends.live") as k:
                    out.append(winreg.QueryValueEx(k, "InstallLocation")[0])
            except OSError:
                pass
    except ImportError:
        pass
    seen, res = set(), []
    for p in out:
        p = os.path.normpath(p.rstrip("/\\"))
        if p.lower() not in seen and os.path.isfile(os.path.join(p, EXE)):
            seen.add(p.lower())
            res.append(p)
    return res


def file_version(path):
    """Version d'un .exe (ressource VERSIONINFO), ex. '16.20.824.8524'."""
    v = ctypes.windll.version
    n = v.GetFileVersionInfoSizeW(path, None)
    if not n:
        return None
    buf = ctypes.create_string_buffer(n)
    if not v.GetFileVersionInfoW(path, 0, n, buf):
        return None
    p, ln = ctypes.c_void_p(), ctypes.c_uint()
    if not v.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(ln)):
        return None
    ms, ls = ctypes.cast(p.value + 8, ctypes.POINTER(ctypes.c_uint32 * 2)).contents
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


_MAIN = {"t": 0, "v": None}


def main_installs():
    """[(dossier, version)] du jeu installé, relu toutes les 30 s (le Riot Client peut le mettre à jour)."""
    if time.time() - _MAIN["t"] > 30:
        _MAIN["v"] = [(d, file_version(os.path.join(d, EXE))) for d in _candidates()]
        _MAIN["t"] = time.time()
    return _MAIN["v"]


def main_install():
    m = main_installs()
    return m[0][0] if m else None


def _main_for(patch):
    for d, v in main_installs():
        if v and ".".join(v.split(".")[:2]) == patch:
            return d
    return None


def is_main(patch):
    return _main_for(patch) is not None


def client_dir(patch):
    """Le jeu installé s'il est de ce patch, sinon l'ancien client clients/<patch>/."""
    d = os.path.join(CLIENTS, patch)
    if _ok(d):
        return d
    return _main_for(patch) or d


def _ok(d):
    return os.path.isfile(os.path.join(d, EXE)) and not os.path.exists(os.path.join(d, ".incomplete"))


def installed(patch):
    return _ok(client_dir(patch))


def list_installed():
    if not os.path.isdir(CLIENTS):
        return []
    return sorted(p for p in os.listdir(CLIENTS) if installed(p))


def manifest_url(version):
    # 16.19.823.0722 -> 16.19.8230722
    a, b, c, d = version.split(".")
    with urllib.request.urlopen(MANIFEST_INDEX.format(f"{a}.{b}.{c}{d}"), timeout=15) as r:
        return r.read().decode().strip()


def free_gb():
    os.makedirs(CLIENTS, exist_ok=True)  # (1er lancement de l'appli installée : dossier pas encore créé)
    return shutil.disk_usage(CLIENTS).free / 1e9


def job(patch):
    return _jobs.get(patch)


def install(version, threads=8):
    """Lance le téléchargement en arrière-plan ; suivre via job(patch)."""
    patch = ".".join(version.split(".")[:2])
    if patch in _jobs and _jobs[patch]["state"] == "running":
        return _jobs[patch]
    j = _jobs[patch] = {"state": "running", "files": 0, "total": None, "log": [], "error": None, "version": version}
    threading.Thread(target=_install, args=(version, patch, threads, j), daemon=True).start()
    return j


def _install(version, patch, threads, j):
    try:
        url = manifest_url(version)
        d = client_dir(patch)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, ".incomplete"), "w").close()
        # nombre de fichiers pour la progression
        try:
            out = subprocess.run([MANIFEST_DL, url, "-l", "en_US", "windows", "-n", "--print-manifest",
                                  os.path.join(d, "manifest.json")], capture_output=True, text=True, timeout=120)
            import json
            m = json.load(open(os.path.join(d, "manifest.json"), encoding="utf-8"))
            files = m.get("files") if isinstance(m, dict) else m
            j["total"] = len(files) if files else None
        except Exception:
            pass
        # "windows" DOIT être dans -l : Bootstrap.windows.wad.client et ShaderCache sont taggés langue "windows"
        p = subprocess.Popen([MANIFEST_DL, url, "-o", os.path.join(d, "Game"), "-t", str(threads),
                              "-l", "en_US", "windows", "-n"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        for line in p.stdout:
            line = line.strip()
            if line.startswith("Downloading file"):
                j["files"] += 1
            j["log"] = (j["log"] + [line])[-20:]
        if p.wait() != 0:
            raise RuntimeError(f"ManifestDownloader code {p.returncode}")
        _copy_config(d)
        os.remove(os.path.join(d, ".incomplete"))
        j["state"] = "done"
    except Exception as e:
        j["state"], j["error"] = "error", str(e)


def _copy_config(d):
    cfg = os.path.join(d, "Config")
    os.makedirs(cfg, exist_ok=True)
    main = main_install()
    if not main:
        enable_replay_api(os.path.join(cfg, "game.cfg"))
        return
    src = os.path.join(main, "Game", "Config") if os.path.isdir(os.path.join(main, "Game", "Config")) \
        else os.path.join(main, "Config")
    for name in ("game.cfg", "input.ini", "PersistedSettings.json"):
        s = os.path.join(src, name)
        if os.path.isfile(s) and not os.path.exists(os.path.join(cfg, name)):
            shutil.copy2(s, cfg)
    enable_replay_api(os.path.join(cfg, "game.cfg"))


def enable_replay_api(path):
    txt = open(path, encoding="utf-8-sig").read() if os.path.exists(path) else ""
    if re.search(r"^EnableReplayApi\s*=\s*1", txt, re.M):
        return
    txt = re.sub(r"^EnableReplayApi\s*=.*$\n?", "", txt, flags=re.M)
    if "[General]" in txt:
        txt = txt.replace("[General]", "[General]\nEnableReplayApi=1", 1)
    else:
        txt += "\n[General]\nEnableReplayApi=1\n"
    open(path, "w", encoding="utf-8").write(txt)


# Réglages imposés au client pour un rendu "propre" façon montage (lus au lancement du jeu)
CLEAN_SETTINGS = {
    "General": {"EnableReplayApi": "1"},
    # 60 i/s stables : sans limite, ~97 i/s irréguliers sur l'écran 175 Hz -> capture saccadée (3 = 25 i/s, 9 = sans limite)
    "Performance": {"FrameCapType": "5"},
    "HUD": {"ShowSummonerNames": "0", "HidePlayerNames": "1", "NameTagDisplay": "0", "ShowHealthBarShake": "0"},
}


def _set_cfg(path, settings):
    lines = open(path, encoding="utf-8-sig").read().splitlines() if os.path.exists(path) else []
    for section, kv in settings.items():
        try:
            i = lines.index(f"[{section}]")
        except ValueError:
            lines += ["", f"[{section}]"]
            i = len(lines) - 1
        for k, v in kv.items():
            j, found = i + 1, False
            while j < len(lines) and not lines[j].startswith("["):
                if lines[j].split("=")[0].strip() == k:
                    lines[j], found = f"{k}={v}", True
                j += 1
            if not found:
                lines.insert(i + 1, f"{k}={v}")
    open(path, "w", encoding="utf-8").write(chr(10).join(lines) + chr(10))


def _set_persisted(path, settings):
    import json
    if not os.path.exists(path):
        return
    d = json.load(open(path, encoding="utf-8-sig"))
    for f in d.get("files", []):
        if f.get("name") != "Game.cfg":
            continue
        for section, kv in settings.items():
            sec = next((x for x in f["sections"] if x["name"] == section), None)
            if sec is None:
                sec = {"name": section, "settings": []}
                f["sections"].append(sec)
            for k, v in kv.items():
                st = next((x for x in sec["settings"] if x["name"] == k), None)
                if st:
                    st["value"] = v
                else:
                    sec["settings"].append({"name": k, "value": v})
    json.dump(d, open(path, "w", encoding="utf-8"), indent=4)


CFG_FILES = ("game.cfg", "PersistedSettings.json")
BACKUP = os.path.join(PROJECTS, "_config_backup")  # réglages du jeu principal, remis après le replay


def prepare_config(patch):
    d = client_dir(patch)
    cfg = os.path.join(d, "Config")
    os.makedirs(cfg, exist_ok=True)
    if is_main(patch) and not d.startswith(CLIENTS):
        _backup(cfg)
    _set_cfg(os.path.join(cfg, "game.cfg"), CLEAN_SETTINGS)
    _set_persisted(os.path.join(cfg, "PersistedSettings.json"), CLEAN_SETTINGS)


def _backup(cfg):
    """Jeu principal : on sauvegarde ses réglages (une seule fois, tant qu'ils n'ont pas été remis) puis on les
    remettra à la fermeture du replay, pour que tes vraies parties gardent tes réglages."""
    os.makedirs(BACKUP, exist_ok=True)
    if not os.path.exists(os.path.join(BACKUP, "dir.txt")):
        for n in CFG_FILES:
            if os.path.isfile(os.path.join(cfg, n)):
                shutil.copy2(os.path.join(cfg, n), BACKUP)
        open(os.path.join(BACKUP, "dir.txt"), "w", encoding="utf-8").write(cfg)
    threading.Thread(target=_restore_when_closed, daemon=True).start()


def _game_running():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq League of Legends.exe", "/NH"], capture_output=True, text=True)
    return "League of Legends.exe" in r.stdout


def _restore_when_closed(start_timeout=180):
    t0 = time.time()
    while not _game_running() and time.time() - t0 < start_timeout:
        time.sleep(2)
    while _game_running():
        time.sleep(5)
    restore_config()


def restore_config():
    """Remet les réglages sauvegardés du jeu principal (aussi appelé au démarrage de l'appli, au cas où elle a été
    fermée pendant un replay)."""
    f = os.path.join(BACKUP, "dir.txt")
    if not os.path.exists(f) or _game_running():
        return False
    cfg = open(f, encoding="utf-8").read().strip()
    for n in CFG_FILES:
        if os.path.isfile(os.path.join(BACKUP, n)):
            shutil.copy2(os.path.join(BACKUP, n), os.path.join(cfg, n))
    shutil.rmtree(BACKUP, ignore_errors=True)
    return True
