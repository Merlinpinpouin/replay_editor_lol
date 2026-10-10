"""lolclip — point d'entrée.
  python lolclip.py                 -> lance l'appli (navigateur)
  python lolclip.py info <rofl>     -> métadonnées
  python lolclip.py state           -> état replay/caméra (jeu ouvert)
  python lolclip.py cam x y z yaw pitch [roll] [fov]
  python lolclip.py seek <t>
  python lolclip.py shot <out.jpg>  -> capture d'écran du jeu
  python lolclip.py api GET|POST <path> [json]
"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for _s in (sys.stdout, sys.stderr):  # console Windows : pseudos avec caractères spéciaux
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from app import camera, game, rofl, server


def main(a):
    if not a:
        return server.main()
    c = a[0]
    if c == "info":
        print(json.dumps(rofl.read(a[1]), indent=1, ensure_ascii=False))
    elif c == "state":
        print(json.dumps(game.state(), indent=1))
    elif c == "cam":
        v = [float(x) for x in a[1:]]
        r = {"cameraMode": "fps", "cameraAttached": False, "cameraPosition": camera.v3(v[0:3]),
             "cameraRotation": {"x": v[3], "y": v[4], "z": v[5] if len(v) > 5 else 0.0}}
        if len(v) > 6:
            r["fieldOfView"] = v[6]
        game.post("/replay/render", r)
        print(json.dumps(game.state()["render"]))
    elif c == "seek":
        print(game.seek(float(a[1]), paused=True))
    elif c == "shot":
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "ddagrab=output_idx=0:framerate=5",
                        "-frames:v", "1", "-vf", "hwdownload,format=bgra,scale=1280:-1", a[1]], check=True)
        print(a[1])
    elif c == "api":
        m, p = a[1].upper(), a[2]
        r = game.get(p) if m == "GET" else game.post(p, json.loads(a[3]) if len(a) > 3 else {})
        print(json.dumps(r, indent=1))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
