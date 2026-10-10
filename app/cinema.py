"""Réalisation caméra : deux styles au choix.

- "cinema" : plan bas incliné qui tourne autour du joueur (modèles par type d'action, variantes tirées au hasard,
  graine = replay + action) ;
- "top" : la vue normale de LoL (angle et focale relevés sur la caméra du jeu), nord en haut, suivi fluide.

Dans les deux cas : suivi serré du joueur (positions mesurées toutes les ~50 ms de jeu, lissage court et sans
retard), cadrage joueur + victime à l'approche d'un kill, petit zoom au kill, et si le kill a lieu à distance
(sort longue portée) la caméra va centrer la victime 0.5 s avant le kill, sans dézoomer, puis revient."""
import hashlib, math, random
import numpy as np
from . import camera, tracking

KEY_DT = 0.05  # une clé caméra toutes les 50 ms de jeu -> trajectoire sans cassure
LONG_KILL = 1000.0  # distance joueur-victime au kill au-delà de laquelle la caméra va chercher la victime
MODES = ("cinema", "top")

FOG = {"r": 0.90, "g": 0.84, "b": 0.74, "a": 1.0}

TEMPLATES = {
    # Référence (infos/) : plans serrés sur le joueur, angle bas. spread_k faible : on reste sur le star plutôt que
    # d'élargir pour tout le combat. pre/post = valeurs par défaut (réglables dans l'interface).
    "multikill": {"dist": (1450, 1700), "spread_k": 0.25, "pitch": (49, 54), "roll": (3, 6), "fov": 44,
                  "drift": (15, 30), "pre": 4.0, "post": 1.5},
    # duel / solo kill : plus serré, plus bas, orbite lente continue
    "solo": {"dist": (1200, 1450), "spread_k": 0.2, "pitch": (47, 52), "roll": (3, 6), "fov": 42,
             "drift": (35, 55), "pre": 3.5, "post": 1.5},
    # teamfight : un peu plus large et posé
    "teamfight": {"dist": (1800, 2050), "spread_k": 0.35, "pitch": (52, 57), "roll": (2, 4), "fov": 46,
                  "drift": (10, 20), "pre": 4.0, "post": 1.5},
}

# Effets par style : zoom au kill (fraction de distance), largeur du zoom (s), rotation autour du joueur (°)
EFFECTS = {
    "cinema": {"push": 0.24, "push_big": 0.45, "push_w": 0.45, "push_w_big": 0.8,
               "orbit": 12.0, "orbit_big": 60.0, "orbit_t": 0.9, "orbit_t_big": 1.8, "smooth": 0.3},
    "top": {"push": 0.13, "push_big": 0.24, "push_w": 0.4, "push_w_big": 0.7,
            "orbit": 0.0, "orbit_big": 0.0, "orbit_t": 1.0, "orbit_t_big": 1.0, "smooth": 0.25},
}


def variant(name, seed):
    """Tire une variante concrète d'un modèle (reproductible avec la même graine)."""
    t = TEMPLATES[name]
    r = random.Random(seed)
    v = {k: (r.uniform(*val) if isinstance(val, tuple) else val) for k, val in t.items()}
    v["template"] = name
    v["yaw0"] = r.choice([-40, -25, -10, 10, 25, 40]) + r.uniform(-5, 5)
    v["drift"] *= r.choice([-1, 1])
    v["roll"] *= r.choice([-1, 1])
    v["seed"] = seed
    return v


def seed_for(*parts):
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def bump(x, w):
    return math.exp(-(x / w) ** 2)


def kill_targets(s, follow, kill_events, t0, t1):
    """Kills du passage vus depuis le joueur suivi : victime, sa position au kill, distance au joueur."""
    out = []
    if follow not in s.d:
        return out
    for e in kill_events:
        k, v = e["EventTime"], e.get("VictimName")
        if not (t0 - 0.5 <= k <= t1 + 0.5) or v not in s.d or v == follow:
            continue
        vp, fp = s.pos(v, k - 0.05), s.pos(follow, k - 0.05)
        out.append({"t": k, "victim": v, "vpos": vp, "dist": float(math.hypot(vp[0] - fp[0], vp[2] - fp[2]))})
    return out


LONG_LEAD = 0.5  # kill à distance : la caméra est arrivée sur la victime 0.5 s AVANT le kill (on voit l'action)
LONG_HOLD = 0.5  # ... et y reste 0.5 s après
LONG_BACK = 0.6  # durée du retour vers le joueur
LONG_CHAIN = 5.0  # deux kills à distance espacés de moins de 5 s : la caméra reste sur l'action entre les deux
CAM_SPEED = 4000.0  # vitesse du trajet joueur -> victime (unités/s), entre 0.35 et 0.8 s


def long_kill_weights(s, kts, ts, t_end):
    """Kills à distance : (poids 0..1 de la poursuite à chaque clé, point visé, victimes à garder à l'image).
    La caméra file vers la victime pour y être LONG_LEAD s avant le kill (victime au centre, zoom normal), la suit
    si elle bouge, reste LONG_HOLD s après le kill puis revient vers le joueur ; si le clip finit juste après, elle
    reste sur l'action. Deux kills à distance rapprochés sont fusionnés (pas d'aller-retour entre les deux)."""
    n = len(ts)
    w, vp, who = np.zeros(n), [None] * n, [None] * n
    longs = sorted((kt for kt in kts if kt["dist"] > LONG_KILL), key=lambda kt: kt["t"])  # proches : opponents()
    xs = []
    for kt in longs:
        k = kt["t"]
        travel = float(np.clip(kt["dist"] / CAM_SPEED, 0.35, 0.8))
        nxt = [o["t"] for o in longs if o["t"] > k + 0.05]
        # fin du clip proche, ou autre kill à distance dans les LONG_CHAIN s : pas de retour vers le joueur entre les
        # deux (allers-retours joueur <-> victimes illisibles)
        stay = t_end - (k + LONG_HOLD) < 1.2 or (nxt and nxt[0] - k < LONG_CHAIN)
        x = np.array([_ease((t - (k - LONG_LEAD - travel)) / travel) for t in ts])
        if not stay:
            x *= 1 - np.array([_ease((t - (k + LONG_HOLD)) / LONG_BACK) for t in ts])
        xs.append(x)
        w = np.maximum(w, x)
    for i, t in enumerate(ts):
        # point visé : moyenne des victimes, chacune pesant surtout juste avant SON kill -> victime centrée à sa mort,
        # puis glissement vers la victime suivante (kills enchaînés)
        on = [j for j, x in enumerate(xs) if x[i] > 0]
        if on:
            g = np.array([math.exp(-((t - longs[j]["t"]) / 0.5) ** 2) + 1e-6 for j in on])
            ps = np.array([s.pos(longs[j]["victim"], min(t, longs[j]["t"] - 0.05)) for j in on], float)
            vp[i] = (g[:, None] * ps).sum(0) / g.sum()
            # victimes à garder à l'image (celle qui meurt + la suivante si son kill est proche)
            who[i] = [p for p, x in zip(ps, g / g.max()) if x > 0.15]
    if not w.any():
        return w, vp, who
    w = np.clip(tracking.smooth(_hold_max(w, int(0.6 / KEY_DT)), KEY_DT, 0.2), 0, 1)
    # position visée aussi entre deux kills (la plus proche dans le temps)
    idx = [i for i in range(n) if vp[i] is not None]
    for i in range(n):
        if vp[i] is None:
            j = min(idx, key=lambda j: abs(j - i))
            vp[i], who[i] = vp[j], who[j]
    return w, vp, who


OPP_RADIUS = 1500.0  # adversaires pris en compte autour du joueur suivi
OPP_SHIFT = 0.4  # le centre de l'image glisse de 40 % vers les adversaires (joueur en bas, adversaires en haut)
FIT = (1.35, 350.0)  # distance caméra minimale = 1.35 x écart joueur/adversaires + 350 (tous à l'image, même en carré)


def opponents(s, follow, teams, ts, fpos, kts):
    """Adversaires que le joueur combat, à chaque clé : (point moyen Nx3, présence 0..1, écart au joueur N).
    Adversaires vivants à moins de OPP_RADIUS, les plus proches comptent plus ; la victime d'un kill compte 4x plus
    dans les 2.5 s qui précèdent le kill (c'est elle qu'on est en train de taper)."""
    me = teams.get(follow)
    foes = [n for n in s.names() if n != follow and (me is None or teams.get(n) != me)]
    E, pres = [], []
    for i, t in enumerate(ts):
        f = fpos[i]
        acc, wsum = np.zeros(3), 0.0
        for n in foes:
            if s.dead(n, t):
                continue
            p = s.pos(n, t)
            d = math.hypot(p[0] - f[0], p[2] - f[2])
            if d > OPP_RADIUS:
                continue
            w = 1.0 / (1.0 + (d / 600.0) ** 2)
            for kt in kts:
                if kt["victim"] == n and kt["dist"] <= LONG_KILL and kt["t"] - 2.5 <= t <= kt["t"] + 0.1:
                    w *= 4.0
            acc += w * p
            wsum += w
        E.append(acc / wsum if wsum else np.array(f, float))
        pres.append(min(1.0, wsum * 1.5))
    E = np.array(E)
    E = np.stack([tracking.smooth(E[:, j], KEY_DT, 0.45) for j in range(3)], 1)
    pres = np.clip(tracking.smooth(np.array(pres), KEY_DT, 0.35), 0, 1)
    sep = np.hypot(E[:, 0] - fpos[:, 0], E[:, 2] - fpos[:, 2])
    return E, pres, sep


def _in_frame(cam, yaw, pitch, fov, p, aspect, margin=0.85):
    y, pi = math.radians(yaw), math.radians(pitch)
    fwd = np.array([math.sin(y) * math.cos(pi), -math.sin(pi), math.cos(y) * math.cos(pi)])
    right = np.array([math.cos(y), 0.0, -math.sin(y)])
    up = np.cross(right, fwd)
    r = np.asarray(p, float) + np.array([0.0, 100.0, 0.0]) - cam  # milieu du corps
    z = r @ fwd
    if z <= 50:
        return False
    th = math.tan(math.radians(fov / 2)) * margin
    return abs(r @ right) / z < th * aspect and abs(r @ up) / z < th


def fit_distance(tgt, yaw, pitch, fov, points, aspect, d0, d_max=4200.0):
    """Plus petite distance >= d0 qui garde tous les points à l'image (recherche par dichotomie)."""
    def ok(d):
        cam = np.array(camera.orbit_pos(tgt, d, yaw, pitch))
        return all(_in_frame(cam, yaw, pitch, fov, p, aspect) for p in points)
    if not points or ok(d0):
        return d0
    if not ok(d_max):
        return d_max
    lo, hi = d0, d_max
    for _ in range(12):
        mid = 0.5 * (lo + hi)
        lo, hi = (lo, mid) if ok(mid) else (mid, hi)
    return hi


def must_see(s, follow, teams, kts, t, f, radius=1300.0):
    """Points à garder à l'image au temps t : le joueur suivi, les adversaires vivants proches, la victime du
    prochain kill (dans les 2.5 s qui le précèdent)."""
    me = teams.get(follow)
    pts = [f]
    for n in s.names():
        if n == follow or (me is not None and teams.get(n) == me) or s.dead(n, t):
            continue
        p = s.pos(n, t)
        d = math.hypot(p[0] - f[0], p[2] - f[2])
        near_kill = any(kt["victim"] == n and kt["dist"] <= LONG_KILL and kt["t"] - 2.5 <= t <= kt["t"] for kt in kts)
        if d < radius or near_kill:
            pts.append(p)
    return pts


def _hold_max(v, n):
    """Maximum glissant (n clés de chaque côté) : la caméra recule un peu AVANT qu'un adversaire n'entre au bord."""
    return np.array([v[max(0, i - n):i + n + 1].max() for i in range(len(v))])


def _face_yaw(base, fpos, E, pres, sep, side, strength=0.7, max_rate=40.0, sigma=1.5):
    """Yaw final : base (dérive du plan) tourné vers les adversaires, caméra derrière le joueur.
    La direction est la MOYENNE sur ~sigma s du vecteur joueur -> adversaires (un champion qui dash autour de sa
    cible faisait basculer l'angle d'un côté à l'autre : la caméra tournait de ±100° plusieurs fois par clip).
    Continu (aucun saut de 360°), limité à max_rate °/s, puis adouci (pas de rotation à vitesse constante saccadée)."""
    w = pres * np.clip((sep - 150.0) / 300.0, 0, 1)  # trop près : direction peu fiable
    dx = tracking.smooth(w * (E[:, 0] - fpos[:, 0]) / np.maximum(sep, 1.0), KEY_DT, sigma)
    dz = tracking.smooth(w * (E[:, 2] - fpos[:, 2]) / np.maximum(sep, 1.0), KEY_DT, sigma)
    conf = np.clip(np.hypot(dx, dz), 0, 1)  # direction stable = vecteurs moyens alignés
    des = np.degrees(np.unwrap(np.arctan2(dx, dz))) + side
    diff = np.degrees(np.unwrap(np.radians(((des - base + 180) % 360) - 180)))
    yaw = base + strength * conf * diff
    out = yaw.copy()
    lim = max_rate * KEY_DT
    for i in range(1, len(out)):
        out[i] = out[i - 1] + float(np.clip(yaw[i] - out[i - 1], -lim, lim))
    return tracking.smooth(out, KEY_DT, 0.4)


def segment_sequence(trk, follow, seg, v, mode="cinema", top=None, final=True, seg_index=0, kill_events=(),
                     big_finish=False):
    t0, t1 = seg["start"], seg["end"]
    fx_ = EFFECTS[mode]
    s = tracking.Series(trk)
    ts, centers, spread = tracking.star_path(s, follow, t0 - 0.6, t1 + 0.6, dt=KEY_DT, sigma=fx_["smooth"])
    kts = kill_targets(s, follow, kill_events, t0, t1)
    k_last = max((kt["t"] for kt in kts), default=None)

    # 1) cible et effets à chaque clé. Kill à distance : la caméra va centrer la victime avant le kill, sans dézoomer
    # (dézoomer pour voir joueur + victime laissait l'action minuscule, au bord de l'image en carré)
    wl, vps, seen = long_kill_weights(s, kts, ts, t1)
    tgts, push, orbit = [], [], []
    for i, t in enumerate(ts):
        c = np.array(centers[i], float)
        if wl[i] > 0:
            c = c + wl[i] * (vps[i] - c) * np.array([1, 0.3, 1])
        p = o = 0.0
        for kt in kts:
            k = kt["t"]
            big = big_finish and final and k == k_last
            a = fx_["push_big"] if big else fx_["push"]
            if kt["dist"] > LONG_KILL:
                a *= 0.5  # la poursuite est déjà l'effet
            p = max(p, a * bump(t - k - 0.1, fx_["push_w_big"] if big else fx_["push_w"]))
            ot = fx_["orbit_t_big"] if big else fx_["orbit_t"]
            o += (fx_["orbit_big"] if big else fx_["orbit"]) * _ease((t - k + 0.3) / ot)
        tgts.append(c); push.append(p); orbit.append(o)
    tgts = np.array(tgts)
    push, orbit = map(np.array, (push, orbit))
    # adversaires combattus : le centre glisse vers eux et la caméra recule pour les garder à l'image
    teams = {p["name"]: p.get("team") for p in trk.get("players", [])}
    fpos = np.array(centers)
    E, pres, sep = opponents(s, follow, teams, ts, fpos, kts)
    k_sh = OPP_SHIFT * pres * (1 - wl)
    tgts = tgts + (k_sh[:, None] * (E - fpos)) * np.array([1, 0.3, 1])
    need = pres * (FIT[0] * sep + FIT[1]) * (1 - wl)  # pendant une poursuite : plus besoin de cadrer le joueur
    # léger lissage de la cible finale (le cadrage ajoute des positions mesurées moins souvent)
    tgts = np.stack([tracking.smooth(tgts[:, j], KEY_DT, 0.1) for j in range(3)], 1)

    # 2) clés caméra
    seq = camera.Sequence()
    T = ts[-1] - ts[0]
    if mode == "top":
        top = top or camera.TOP_DEFAULT
        aspect = v.get("aspect", 1.0)
        dd = []
        for i, t in enumerate(ts):
            d = max(top["dist"], 0.8 * need[i]) * (1 - push[i])
            # pendant une poursuite de kill à distance, on cadre les victimes (pas le joueur)
            # (pendant le trajet : ni l'un ni l'autre, zoom normal)
            pts = must_see(s, follow, teams, kts, t, fpos[i]) if wl[i] < 0.05 else seen[i] if wl[i] > 0.9 else []
            d = fit_distance(tgts[i], 0.0, top["pitch"], top["fov"], pts or [], aspect, d)
            dd.append(d)
        # recul anticipé et retour lent : pas de pompage du zoom
        dd = tracking.smooth(_hold_max(np.array(dd), 10), KEY_DT, 0.4)
        tgts = np.stack([tracking.smooth(tgts[:, j], KEY_DT, 0.25) for j in range(3)], 1)
        for i, t in enumerate(ts):
            pos = camera.orbit_pos(tgts[i], float(dd[i]), 0.0, top["pitch"])
            seq.cam_rot(t, pos, 0.0, top["pitch"], 0.0, top["fov"], "linear")
        return seq, [kt["t"] for kt in kts if kt["dist"] > LONG_KILL]

    yaw0 = v["yaw0"] + (70.0 if seg_index % 2 else 0.0)  # cut visible entre segments
    rsign = 1 if v["roll"] > 0 else -1
    osign = 1 if v["drift"] > 0 else -1
    # caméra derrière le joueur, tournée vers les adversaires (légèrement de biais pour le style), sinon dérive du plan
    base = yaw0 + v["drift"] * ((ts - ts[0]) / max(T, 1e-3))
    yaws = _face_yaw(base, fpos, E, pres, sep, side=12.0 * osign) + osign * orbit
    z = v.get("zoom", 1.0)
    aspect = v.get("aspect", 1.0)
    keys = []
    for i, t in enumerate(ts):
        c, p = tgts[i], push[i]
        d = max(v["dist"] + v["spread_k"] * spread[i] * (1 - wl[i]), need[i])
        d = float(np.clip(d, 800 * z, 3400 * z)) * (1 - p * (1 - pres[i] * 0.5))  # zoom plus doux si combat
        yaw = yaws[i]
        pitch = v["pitch"] - 5 * p
        fov = v["fov"] - 4 * p  # zoom : on se rapproche ET on resserre légèrement la focale
        fx, fz = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
        tgt = (c[0] + 120 * fx * (1 - wl[i]), c[1] + 90, c[2] + 120 * fz * (1 - wl[i]))
        if wl[i] < 0.05:  # (pendant une poursuite de kill à distance, la cible est la victime)
            d = fit_distance(tgt, yaw, pitch, fov, must_see(s, follow, teams, kts, t, fpos[i]), aspect, d)
        elif wl[i] > 0.9 and seen[i]:  # arrivée sur les victimes (pendant le trajet : zoom normal)
            d = fit_distance(tgt, yaw, pitch, fov, seen[i], aspect, d)
        keys.append([t, tgt, d, yaw, pitch, fov, p])
    # la distance ne fait que suivre les besoins (maximum glissant + lissage) : pas d'effet de pompe
    dd = tracking.smooth(_hold_max(np.array([k[2] for k in keys]), 6), KEY_DT, 0.25)
    for i, (t, tgt, _, yaw, pitch, fov, p) in enumerate(keys):
        d = float(dd[i])
        roll = v["roll"] * (0.6 + 0.4 * math.sin(t * 0.6)) + 2 * p * rsign
        pos = camera.orbit_pos(tgt, d, yaw, pitch)
        seq.cam_rot(t, pos, yaw, pitch, roll, fov, "linear")
        if i % 4 == 0:
            seq.key("depthFogStart", t, d * 1.6, "linear")
            seq.key("depthFogEnd", t, d * 4.5, "linear")
    seq.key("depthFogEnabled", ts[0], True, "snap")
    seq.key("depthFogColor", ts[0], FOG, "snap")
    seq.key("depthFogIntensity", ts[0], 0.3, "snap")
    return seq, [kt["t"] for kt in kts if kt["dist"] > LONG_KILL]


def direct(play, tracks, seed, mode="cinema", top=None, zoom=1.0, aspect=1.0):
    """-> (plans par segment, variante). play["follow"] = joueur suivi (par défaut l'auteur de l'action).
    zoom : multiplie la distance caméra (< 1 = plus près)."""
    v = variant(play["template"], seed)
    v["mode"] = mode
    v["zoom"] = zoom
    v["aspect"] = aspect  # largeur/hauteur de l'image finale (cadrage : tout le monde doit tenir dedans)
    v["dist"] *= zoom
    if top is not None:
        top = dict(top, dist=top["dist"] * zoom)
    follow = play.get("follow") or play["star"]
    shots = []
    n = len(play["segments"])
    big = play["label"] in ("quadra", "penta")
    for i, (seg, trk) in enumerate(zip(play["segments"], tracks)):
        seq, longs = segment_sequence(trk, follow, seg, v, mode=mode, top=top, final=(i == n - 1), seg_index=i,
                                      kill_events=play.get("kill_events", []), big_finish=big)
        shots.append({"segment": seg, "sequence": seq, "long_kills": longs})
    return shots, v


# --- Transition entre deux actions (clips combinés) : la caméra avance dans un nuage de brouillard (ajouté au montage,
# voir fogfx), coupe franche quand l'écran est couvert, et ressort du nuage en avançant sur l'action suivante. Aucun
# trajet de caméra d'une action à l'autre (un glissement sur la carte donnait un dézoom bizarre, surtout sur un
# trajet court).
DOLLY = 0.22  # avancée de la caméra pendant l'entrée dans le nuage (fraction de sa distance au sol)


def dolly(seq, t_a, t_b, entering, amount=DOLLY):
    """Avancée de la caméra sur [t_a, t_b] (clés existantes déplacées le long de l'axe de visée).
    entering : fin de l'action A, la caméra accélère vers l'avant ; sinon début de B : elle part de plus loin et
    ralentit en arrivant à sa place (même vitesse que A au moment de la coupe : le mouvement continue)."""
    rots = {round(k["time"], 4): k["value"] for k in seq.tracks.get("cameraRotation", [])}
    span = t_b - t_a
    for k in seq.tracks.get("cameraPosition", []):
        t, r = k["time"], rots.get(round(k["time"], 4))
        if r is None or not (t_a - 1e-6 <= t <= t_b + 1e-6):
            continue
        u = (t - t_a) / span
        # décalage vers l'arrière (fraction de la distance) : 0 -> -amount en accélérant, ou +amount' -> 0 en freinant
        off = -amount * u * u if entering else amount * (1 - u) ** 2
        p = k["value"]
        yaw, pitch = math.radians(r["x"]), math.radians(r["y"])
        dist = max(400.0, (p["y"] - 100.0) / max(0.3, math.sin(pitch)))
        fwd = (math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch))
        k["value"] = {"x": p["x"] - fwd[0] * off * dist, "y": p["y"] - fwd[1] * off * dist,
                      "z": p["z"] - fwd[2] * off * dist}
    return seq
