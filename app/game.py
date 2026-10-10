"""Client de la Replay API (https://127.0.0.1:2999) + lancement du replay."""
import http.client, json, os, ssl, subprocess, threading, time
from .paths import RUN, TOOLS
from . import clients

BASE = "https://127.0.0.1:2999"
_CTX = ssl._create_unverified_context()


class NotRunning(Exception):
    pass


_LOCAL = threading.local()


def _conn(timeout):
    """Connexion gardée ouverte (une par thread) : en ouvrir une par requête coûtait une poignée de main TLS à
    chaque fois, le repérage des champions en enchaîne des milliers."""
    c = getattr(_LOCAL, "conn", None)
    if c is None:
        c = _LOCAL.conn = http.client.HTTPSConnection("127.0.0.1", 2999, context=_CTX, timeout=timeout)
    c.timeout = timeout
    if c.sock is not None:
        c.sock.settimeout(timeout)
    return c


def _req(method, path, data=None, timeout=10):
    body = json.dumps(data).encode() if data is not None else None
    for attempt in (0, 1):
        reused = getattr(_LOCAL, "conn", None) is not None and _LOCAL.conn.sock is not None
        c = _conn(timeout)
        try:
            c.request(method, path, body, {"Content-Type": "application/json"})
            r = c.getresponse()
            raw = r.read().decode("utf-8")
            break
        except (OSError, http.client.HTTPException) as e:
            c.close()
            _LOCAL.conn = None
            if attempt or not reused:  # pas seulement une connexion gardée que le jeu a fermée : jeu fermé
                raise NotRunning(str(e))
    if r.status >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status}: {raw[:300]}")
    return json.loads(raw) if raw.strip() else None


def get(path):
    return _req("GET", path)


def post(path, data):
    return _req("POST", path, data)


def delete(path):
    return _req("DELETE", path)


def running():
    try:
        get("/replay/game")
        return True
    except Exception:
        return False


def wait_ready(timeout=300):
    """Attend que le replay soit chargé (l'API répond et le temps avance/est lisible)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            p = get("/replay/playback")
            if p.get("length", 0) > 0:
                return p
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError("replay pas prêt")


def seek(t, paused=True, timeout=90):
    post("/replay/playback", {"time": t, "paused": paused})
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.3)
        p = get("/replay/playback")
        if not p["seeking"] and abs(p["time"] - t) < 1.5:
            return p
    raise TimeoutError(f"seek {t}")


LAUNCH = {"mode": None, "state": "idle", "error": None, "t": 0}  # dernier lancement (affiché dans l'interface)


def launch(rofl, patch):
    """Lance le replay avec le bon client.
    - Client LoL ouvert et replay du même patch : le client lance le replay lui-même (comme « Regarder » dans
      l'historique) ; lancer le jeu à côté échouait quand le client était ouvert.
    - Sinon : lancement direct du jeu (invite UAC : arrêt temporaire de Vanguard, voir tools/launch_replay.ps1).
    Un fil vérifie ensuite que le replay a bien démarré (LAUNCH["error"] sinon)."""
    from . import lcu
    d = clients.client_dir(patch)
    if not clients.installed(patch):
        raise RuntimeError(f"client {patch} non installé")
    LAUNCH.update(state="launching", error=None, t=time.time())
    if clients.is_main(patch) and lcu.running():
        cfg = os.path.join(d, "Config")
        if os.path.isdir(cfg):
            clients.enable_replay_api(os.path.join(cfg, "game.cfg"))
        err = lcu.watch(rofl, clients.CLEAN_SETTINGS, clients._game_running)
        if err:
            LAUNCH.update(state="error", error=f"{err} (ferme le client LoL et réessaie)")
            raise RuntimeError(LAUNCH["error"])
        LAUNCH["mode"] = "client"
        threading.Thread(target=_check_started, daemon=True).start()
        return "client"
    LAUNCH["mode"] = "direct"
    clients.prepare_config(patch)
    script = os.path.join(TOOLS, "launch_replay.ps1")
    os.makedirs(RUN, exist_ok=True)  # journal + fichier de fermeture (le dossier de l'appli n'est pas modifiable)
    args = (f"-NoProfile -ExecutionPolicy Bypass -File \"{script}\" -Rofl \"{rofl}\" -ClientDir \"{d}\" "
            f"-RunDir \"{RUN}\"")
    ps = f"Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList '{args.replace(chr(39), chr(39) * 2)}'"
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    threading.Thread(target=_check_started, daemon=True).start()
    return "direct"


def _check_started(timeout=120):
    """Le replay doit répondre (Replay API) dans les 2 min ; sinon message clair dans l'interface."""
    end = time.time() + timeout
    while time.time() < end:
        if running():
            LAUNCH.update(state="ok", error=None)
            return
        time.sleep(2)
    if clients._game_running():
        LAUNCH.update(state="error", error="le jeu est lancé mais la Replay API ne répond pas "
                                           "(EnableReplayApi pas pris en compte ?)")
    else:
        LAUNCH.update(state="error", error="le replay ne s'est pas lancé (autorisation administrateur refusée, "
                                           "ou client LoL qui bloque : ferme-le et réessaie)")


def close_replay(timeout=20):
    """Ferme le jeu : demande au script de lancement (resté en veille, en admin) de le fermer ; il vérifie ensuite
    que Vanguard tourne. Les réglages du jeu principal sont remis par clients.restore_config à la fermeture."""
    if LAUNCH.get("mode") == "client":  # lancé par le client LoL : pas en admin, on peut le fermer d'ici
        kill()
        end = time.time() + timeout
        while time.time() < end and clients._game_running():
            time.sleep(1)
        return not clients._game_running()
    os.makedirs(RUN, exist_ok=True)
    open(os.path.join(RUN, "close_replay.flag"), "w").close()
    end = time.time() + timeout
    while time.time() < end:
        if not clients._game_running():
            return True
        time.sleep(1)
    kill()  # script de lancement absent (jeu lancé autrement) : essai sans droits admin
    return not clients._game_running()


def kill():
    subprocess.run(["taskkill", "/IM", "League of Legends.exe", "/F"], capture_output=True)


def state():
    """Instantané utile : temps, caméra, rendu."""
    p = get("/replay/playback")
    r = get("/replay/render")
    keep = ("cameraMode", "cameraPosition", "cameraRotation", "fieldOfView", "selectionName", "cameraAttached",
            "interfaceAll", "depthOfFieldEnabled")
    return {"time": p["time"], "paused": p["paused"], "speed": p["speed"], "length": p["length"],
            "render": {k: r.get(k) for k in keep}}


def players():
    try:
        return get("/liveclientdata/playerlist")
    except Exception:
        return []
