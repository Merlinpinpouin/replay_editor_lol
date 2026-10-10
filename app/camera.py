"""Caméra cinématique via /replay/sequence (le jeu interpole lui-même les keyframes -> mouvement fluide).

Repère du jeu : x/z = sol (0..~14800), y = hauteur. cameraRotation = {x: yaw, y: pitch, z: roll} en degrés.
Les conventions de signe sont calibrées en live (voir CONV) ; look_at() s'appuie dessus."""
import math, time
from . import game

# Convention calibrée en live : yaw 0 = regarde vers +z ? pitch positif = vers le bas ?
CONV = {"yaw_offset": 0.0, "yaw_sign": 1.0, "pitch_sign": 1.0}

BLENDS = ("linear", "snap", "smoothStep", "smootherStep", "quadraticEaseIn", "quadraticEaseOut",
          "quadraticEaseInOut", "cubicEaseIn", "cubicEaseOut", "cubicEaseInOut")


def look_at(pos, target, roll=0.0):
    dx, dy, dz = target[0] - pos[0], target[1] - pos[1], target[2] - pos[2]
    yaw = math.degrees(math.atan2(dx, dz)) * CONV["yaw_sign"] + CONV["yaw_offset"]
    pitch = math.degrees(math.atan2(-dy, math.hypot(dx, dz))) * CONV["pitch_sign"]
    return {"x": yaw, "y": pitch, "z": roll}


def orbit_pos(target, dist, yaw_deg, pitch_deg):
    """Position de caméra à <dist> de target, vue selon yaw/pitch (pitch = angle vers le bas)."""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    h = dist * math.cos(pitch)
    return (target[0] - h * math.sin(yaw), target[1] + dist * math.sin(pitch), target[2] - h * math.cos(yaw))


def v3(p):
    return {"x": float(p[0]), "y": float(p[1]), "z": float(p[2])}


def unwrap(yaw, prev):
    """Angle équivalent à yaw le plus proche de prev. Le jeu interpole les clés de rotation tels quels : passer de
    179° à -179° lui fait faire un tour complet (caméra qui pointe n'importe où, voire vers le ciel, sur une frame)."""
    if prev is None:
        return yaw
    return yaw + 360.0 * round((prev - yaw) / 360.0)


class Sequence:
    """Construit un payload /replay/sequence. Les temps sont en secondes de replay."""

    def __init__(self):
        self.tracks = {}
        self._yaw = None

    def key(self, track, t, value, blend="smoothStep"):
        self.tracks.setdefault(track, []).append({"time": float(t), "value": value, "blend": blend})
        return self

    def cam(self, t, pos, target, roll=0.0, fov=None, blend="smoothStep"):
        r = look_at(pos, target, roll)
        return self.cam_rot(t, pos, r["x"], r["y"], roll, fov, blend)

    def cam_rot(self, t, pos, yaw, pitch, roll=0.0, fov=None, blend="smoothStep"):
        """Clé caméra avec angles explicites (yaw continu d'une clé à l'autre, pitch borné : jamais vers le ciel)."""
        yaw = unwrap(float(yaw), self._yaw)
        self._yaw = yaw
        pitch = float(min(85.0, max(20.0, pitch)))
        self.key("cameraPosition", t, v3(pos), blend)
        self.key("cameraRotation", t, {"x": yaw, "y": pitch, "z": float(roll)}, blend)
        if fov is not None:
            self.key("fieldOfView", t, float(fov), blend)
        return self

    def speed(self, t, s, blend="smoothStep"):
        return self.key("playbackSpeed", t, float(s), blend)

    def dof(self, t, near, mid, far, width=None, blend="smoothStep"):
        self.key("depthOfFieldEnabled", t, True, "snap")
        self.key("depthOfFieldNear", t, float(near), blend)
        self.key("depthOfFieldMid", t, float(mid), blend)
        self.key("depthOfFieldFar", t, float(far), blend)
        if width is not None:
            self.key("depthOfFieldWidth", t, float(width), blend)
        return self

    def payload(self):
        return {k: sorted(v, key=lambda e: e["time"]) for k, v in self.tracks.items()}


def apply(seq):
    game.post("/replay/render", {"cameraMode": "fps", "cameraAttached": False})
    return game.post("/replay/sequence", seq.payload() if isinstance(seq, Sequence) else seq)


def clear():
    try:
        game.post("/replay/sequence", {})
    except Exception:
        pass


def set_cam(pos, rot=None, target=None, fov=None, roll=0.0):
    r = {"cameraMode": "fps", "cameraAttached": False, "cameraPosition": v3(pos)}
    if target is not None:
        r["cameraRotation"] = look_at(pos, target, roll)
    elif rot is not None:
        r["cameraRotation"] = {"x": rot[0], "y": rot[1], "z": rot[2] if len(rot) > 2 else 0.0}
    if fov is not None:
        r["fieldOfView"] = fov
    return game.post("/replay/render", r)


# Caméra standard de LoL (mode "top") si la calibration en direct échoue : nord en haut, ~56° vers le bas
TOP_DEFAULT = {"pitch": 56.0, "dist": 1900.0, "fov": 40.0}


def calibrate_top(select_name, star_pos):
    """Lit l'angle, la distance et la focale de la caméra normale du jeu (mode "top" attaché au joueur), pour que la
    vue du dessus soit exactement celle de LoL. star_pos : position du joueur au même instant (jeu en pause)."""
    try:
        clear()
        game.post("/replay/render", {"cameraMode": "top", "selectionName": select_name, "cameraAttached": True})
        time.sleep(0.8)
        r = game.get("/replay/render")
        p, rot = r["cameraPosition"], r["cameraRotation"]
        pitch = abs(float(rot["y"]))
        h = p["y"] - star_pos[1]
        dist = h / math.sin(math.radians(pitch)) if pitch > 1 else 0
        fov = float(r.get("fieldOfView") or TOP_DEFAULT["fov"])
        if 30 <= pitch <= 85 and 900 <= dist <= 4000 and 20 <= fov <= 90:
            return {"pitch": pitch, "dist": dist, "fov": fov, "calibrated": True}
    except Exception:
        pass
    finally:
        try:
            game.post("/replay/render", {"cameraMode": "fps", "cameraAttached": False})
        except Exception:
            pass
    return dict(TOP_DEFAULT, calibrated=False)


# Rendu "propre" façon référence : pas de HUD, barres de vie des champions seulement.
CLEAN_RENDER = {
    "interfaceAll": True, "interfaceReplay": False, "interfaceScore": False, "interfaceScoreboard": False,
    "interfaceFrames": False, "interfaceMinimap": False, "interfaceTimeline": False, "interfaceChat": False,
    "interfaceTarget": False, "interfaceQuests": False, "interfaceKillCallouts": False,
    # annonces du jeu : masquées, sauf pendant un quadra/penta (activées en direct par capture.record_live)
    "interfaceAnnounce": False,
    "interfaceNeutralTimers": False,
    "healthBarChampions": True, "healthBarMinions": False, "healthBarStructures": False, "healthBarWards": False,
    "healthBarPets": False, "floatingText": False, "outlineSelect": False, "outlineHover": False, "banners": False,
    "fogOfWar": False, "environment": True, "characters": True, "particles": True,
    "depthFogEnabled": True, "depthOfFieldEnabled": False,
}
