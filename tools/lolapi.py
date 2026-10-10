"""Petit client pour la Replay API / Live Client Data API de League (https://127.0.0.1:2999)."""
import json, ssl, time, urllib.request

BASE = "https://127.0.0.1:2999"
_CTX = ssl._create_unverified_context()


def get(path):
    with urllib.request.urlopen(BASE + path, context=_CTX, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, data):
    req = urllib.request.Request(BASE + path, json.dumps(data).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=_CTX, timeout=10) as r:
        return json.loads(r.read().decode("utf-8") or "null")


def seek(t, paused=True, timeout=60):
    post("/replay/playback", {"time": t, "paused": paused})
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.5)
        p = get("/replay/playback")
        if not p["seeking"] and abs(p["time"] - t) < 2:
            return p
    raise TimeoutError(f"seek {t}")
