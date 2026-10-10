"""Enregistre le replay ouvert À PARTIR DE LA POSITION ACTUELLE, sans seek (un seek casse le
verrouillage caméra fait à la main dans le jeu). S'arrête quand le temps du replay atteint <end_s>.
Usage: python record.py <end_s> <out.webm> [--lossless] [--render render.json]"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lolapi

ap = argparse.ArgumentParser()
ap.add_argument("end", type=float); ap.add_argument("out")
ap.add_argument("--lossless", action="store_true"); ap.add_argument("--render")
a = ap.parse_args()

render = {"interfaceAll": False, "healthBarMinions": False, "healthBarStructures": False, "healthBarWards": False,
          "healthBarPets": False, "floatingText": False, "outlineSelect": False, "outlineHover": False, "banners": False}
if a.render:
    render.update(json.load(open(a.render, encoding="utf-8")))
lolapi.post("/replay/render", render)
lolapi.post("/replay/recording", {"recording": True, "path": a.out, "codec": "webm", "framesPerSecond": 60,
                                 "enforceFrameRate": True, "lossless": a.lossless, "replaySpeed": 1.0})
lolapi.post("/replay/playback", {"paused": False})
t0 = lolapi.get("/replay/playback")["time"]
print("start", round(t0, 1), flush=True)
while True:
    time.sleep(0.5)
    t = lolapi.get("/replay/playback")["time"]
    if t >= a.end:
        break
lolapi.post("/replay/recording", {"recording": False})
lolapi.post("/replay/playback", {"paused": True})
lolapi.post("/replay/render", {"interfaceAll": True})
print("stop", round(t, 1), a.out, flush=True)
