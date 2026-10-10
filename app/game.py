"""Client de la Replay API (https://127.0.0.1:2999) + lancement du replay."""
import http.client, json, os, ssl, subprocess, threading, time
from .paths import TOOLS
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


def launch(rofl, patch):
    """Lance le replay avec le bon client. Ouvre une invite UAC (arrêt temporaire de vgk)."""
    d = clients.client_dir(patch)
    if not clients.installed(patch):
        raise RuntimeError(f"client {patch} non installé")
    clients.prepare_config(patch)
    script = os.path.join(TOOLS, "launch_replay.ps1")
    args = f"-NoProfile -ExecutionPolicy Bypass -File \"{script}\" -Rofl \"{rofl}\" -ClientDir \"{d}\""
    ps = f"Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList '{args.replace(chr(39), chr(39) * 2)}'"
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def close_replay(timeout=20):
    """Ferme le jeu : demande au script de lancement (resté en veille, en admin) de le fermer ; il vérifie ensuite
    que Vanguard tourne. Les réglages du jeu principal sont remis par clients.restore_config à la fermeture."""
    open(os.path.join(TOOLS, "close_replay.flag"), "w").close()
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
