"""API locale du client LoL (LCU) quand il est ouvert : lancer un replay comme le bouton « Regarder » de
l'historique (le client lance le jeu lui-même : pas besoin de couper Vanguard ni de droits admin), lire les rangs,
et changer les réglages du jeu le temps du replay.
Quand le client est ouvert, il renvoie les réglages du jeu vers le compte à la fermeture de la partie : modifier
les fichiers de config ne suffit pas (et risquerait d'écraser les vrais réglages) -> on passe par son API et on remet
les valeurs d'origine à la fermeture du replay (sauvegarde dans projects/_lcu_settings_backup.json)."""
import base64, json, os, ssl, threading, time, urllib.error, urllib.request
from .paths import PROJECTS

_CTX = ssl._create_unverified_context()
BACKUP = os.path.join(PROJECTS, "_lcu_settings_backup.json")


def _lockfile():
    from . import clients
    for d in [clients.main_install(), r"C:\Riot Games\League of Legends"]:
        f = os.path.join(d or "", "lockfile")
        if d and os.path.isfile(f):
            try:
                _, _, port, pw, proto = open(f).read().strip().split(":")
                return f"{proto}://127.0.0.1:{port}", base64.b64encode(f"riot:{pw}".encode()).decode()
            except (OSError, ValueError):
                continue
    return None


def req(method, path, data=None, timeout=6):
    lf = _lockfile()
    if not lf:
        raise ConnectionError("client LoL fermé")
    base, auth = lf
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(base + path, body, {"Authorization": "Basic " + auth,
                                                   "Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(r, context=_CTX, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"client LoL : {method} {path} -> {e.code} {e.read()[:200]!r}")
    except (urllib.error.URLError, OSError) as e:
        raise ConnectionError(f"client LoL injoignable : {e}")


def running():
    try:
        req("GET", "/riotclient/region-locale", timeout=3)
        return True
    except Exception:
        return False


# --- réglages du jeu le temps du replay ---------------------------------------------------------------------------

def _typed(old, v):
    """Valeur du même type que celle du client (booléen, nombre ou texte)."""
    if isinstance(old, bool):
        return str(v) in ("1", "true", "True")
    if isinstance(old, int):
        return int(v)
    if isinstance(old, float):
        return float(v)
    return v


def apply_settings(settings):
    """settings : {section: {clé: valeur}} (comme clients.CLEAN_SETTINGS). Sauvegarde les valeurs actuelles puis
    les remplace ; remises par restore_settings() à la fermeture du jeu."""
    cur = req("GET", "/lol-game-settings/v1/game-settings") or {}
    if not os.path.exists(BACKUP):  # (déjà sauvegardé et pas encore remis : on garde la 1re sauvegarde)
        old = {s: {k: cur.get(s, {}).get(k) for k in kv if k in cur.get(s, {})} for s, kv in settings.items()}
        os.makedirs(PROJECTS, exist_ok=True)
        json.dump(old, open(BACKUP, "w", encoding="utf-8"), indent=1)
    patch = {s: {k: _typed(cur.get(s, {}).get(k), v) for k, v in kv.items()} for s, kv in settings.items()}
    req("PATCH", "/lol-game-settings/v1/game-settings", patch)


def restore_settings():
    if not os.path.exists(BACKUP):
        return True
    try:
        old = json.load(open(BACKUP, encoding="utf-8"))
        req("PATCH", "/lol-game-settings/v1/game-settings", {s: kv for s, kv in old.items() if kv})
        os.remove(BACKUP)
        return True
    except Exception:
        return False  # client fermé : remis au prochain démarrage de l'appli avec le client ouvert


def _restore_when_closed(game_running, start_timeout=180):
    t0 = time.time()
    while not game_running() and time.time() - t0 < start_timeout:
        time.sleep(2)
    while game_running():
        time.sleep(5)
    time.sleep(3)
    restore_settings()


# --- lancement d'un replay ----------------------------------------------------------------------------------------

def watch(rofl_path, settings, game_running):
    """Fait lancer le replay par le client. -> None si c'est parti, sinon le message d'erreur."""
    name = os.path.splitext(os.path.basename(rofl_path))[0]  # EUW1-8008591457
    try:
        game_id = int(name.split("-")[-1])
    except ValueError:
        return f"nom de replay inattendu : {name}"
    try:
        folder = req("GET", "/lol-replays/v1/rofls/path")
        if folder and os.path.isdir(folder) and not os.path.exists(os.path.join(folder, os.path.basename(rofl_path))):
            import shutil
            shutil.copy2(rofl_path, folder)  # le client ne lit que son dossier de replays
        try:
            req("POST", "/lol-replays/v1/rofls/scan")
        except Exception:
            pass
        meta = req("GET", f"/lol-replays/v1/metadata/{game_id}") or {}
        state = meta.get("state")
        if state and state not in ("watch", "download", "downloading", "found"):
            return f"le client ne peut pas lire ce replay (état : {state})"
        apply_settings(settings)
        threading.Thread(target=_restore_when_closed, args=(game_running,), daemon=True).start()
        req("POST", f"/lol-replays/v1/rofls/{game_id}/watch", {"componentType": "replay-button_match-history"})
        return None
    except Exception as e:
        return str(e)
