"""Repérage exact des champions via la Replay API.

Astuce : une séquence avec selectionName + selectionOffset place la caméra libre à
position_du_champion + offset ; en relisant /replay/render.cameraPosition on obtient la position monde exacte
(ça marche même en pause). On joue le passage au ralenti et on échantillonne en boucle :
le joueur suivi à CHAQUE tour (~50 ms de jeu entre deux mesures), les autres à tour de rôle."""
import math, time
import numpy as np
from . import camera, game

PROBE_Y = 3000.0
TRACK_SPEED = 0.3  # vitesse de lecture au début du repérage (ajustée ensuite, voir STAR_DT)
STAR_DT = 0.065  # écart visé (s de jeu) entre deux mesures du joueur suivi : la lecture accélère si le PC suit
MAX_SPEED = 1.0
FORMAT = 2  # version du format des fichiers de repérage (les anciens, trop espacés, sont refaits)


_SELECT = {}  # pseudo -> nom à donner au jeu pour sélectionner le joueur


def _select_key(name):
    """Avec les pseudos masqués (HidePlayerNames), le jeu identifie les joueurs par leur nom affiché, c'est-à-dire
    le nom du champion (summonerName = « Mordekaiser ») : sélectionner par le pseudo ne fait alors plus rien.
    Sur certains clients summonerName vaut « Pseudo#TAG » : le jeu ne sélectionne alors que par le pseudo seul."""
    if name not in _SELECT:
        for p in game.players():
            sn = p.get("summonerName")
            key = (sn if sn and "#" not in sn else None) or p.get("riotIdGameName") or p.get("championName")
            for n in (p.get("riotIdGameName"), p.get("riotId"), p.get("summonerName")):
                if n:
                    _SELECT[n] = key
    return _SELECT.get(name, name)


def _probe(name, wait=0.06, stale=None):
    """Position de <name>. stale : position de la mesure précédente (autre joueur) -> au lieu d'attendre <wait> s,
    on relit dès que la caméra a bougé (1 à 2 images du jeu), au plus 0.15 s."""
    game.post("/replay/sequence", {
        "selectionName": [{"time": 0, "value": _select_key(name), "blend": "snap"}],
        "selectionOffset": [{"time": 0, "value": {"x": 0, "y": PROBE_Y, "z": 0}, "blend": "snap"}]})
    if stale is None:
        time.sleep(wait)
    else:
        time.sleep(0.02)
    end = time.perf_counter() + 0.15
    while True:
        p = game.get("/replay/render")["cameraPosition"]
        pos = p["x"], p["y"] - PROBE_Y, p["z"]
        if stale is None or math.dist(pos, stale) >= 1.0 or time.perf_counter() > end:
            return pos
        time.sleep(0.008)


# Pendant le repérage la caméra saute d'un champion à l'autre plusieurs fois par seconde : on n'affiche rien
# (la position de la caméra est calculée par le jeu même quand rien n'est dessiné).
HIDE = {"environment": False, "characters": False, "particles": False, "interfaceAll": False,
        "healthBarChampions": False}
SHOW = {"environment": True, "characters": True, "particles": True}


def hide(on=True):
    try:
        game.post("/replay/render", HIDE if on else SHOW)
    except Exception:
        pass


def player_names():
    return [{"name": p.get("riotIdGameName") or p.get("summonerName"), "champion": p.get("championName"),
             "team": p.get("team")} for p in game.players()]


def nearby(star, t, radius=3500.0):
    """Champions à moins de radius du star au temps t (relevé en pause) -> on ne suivra qu'eux."""
    game.post("/replay/render", {"cameraMode": "fps", "cameraAttached": True})
    hide()
    game.seek(max(0.0, t), paused=True)
    names = [p["name"] for p in player_names()]
    try:
        pos = {n: _probe(n) for n in names}
    finally:
        hide(False)
    sx, _, sz = pos[star]
    return [n for n, (x, y, z) in pos.items() if math.hypot(x - sx, z - sz) < radius]


def speed_for(span):
    """Vitesse de départ du repérage (elle s'ajuste ensuite à la vitesse réelle des mesures) : un long passage
    tolère des mesures plus espacées (lissage de la caméra)."""
    return TRACK_SPEED if span <= 20 else min(0.5, TRACK_SPEED + 0.2 * (span - 20) / 20)


def track(start, end, star, speed=TRACK_SPEED, progress=None, names=None, hidden=True):
    """-> {"players": [...], "samples": {name: [[t, x, y, z, dead], ...]}}
    Le star est mesuré à chaque tour de boucle, un autre champion (à tour de rôle) ensuite. Une mesure identique à
    la précédente d'un AUTRE joueur = le jeu n'avait pas encore déplacé la caméra : on remesure."""
    players = [p for p in player_names() if names is None or p["name"] in names or p["name"] == star]
    samples = {p["name"]: [] for p in players}
    others = [p["name"] for p in players if p["name"] != star]
    game.post("/replay/render", {"cameraMode": "fps", "cameraAttached": True})
    if hidden:
        hide()
    game.seek(max(0.0, start - 1.0), paused=True)
    game.post("/replay/playback", {"speed": speed, "paused": False})
    dead, t_dead, k = {}, 0.0, 0
    last = None  # (nom, position) de la mesure précédente
    # vitesse ajustée en continu : mesures du joueur suivi tous les ~STAR_DT s de jeu (plus si le passage est long)
    target = STAR_DT * speed / TRACK_SPEED
    loops, t_adj = [], time.perf_counter()
    try:
        while True:
            if time.time() - t_dead > 0.4:
                try:
                    dead = {p.get("riotIdGameName"): bool(p.get("isDead"))
                            for p in game.get("/liveclientdata/playerlist")}
                except Exception:
                    pass
                t_dead = time.time()
            batch = [star] + ([others[k % len(others)]] if others else [])
            k += 1
            # un seul relevé du temps de jeu par tour : l'instant de chaque mesure en est déduit (lecture à vitesse
            # constante), au lieu d'une requête de plus avant chaque mesure
            w0 = time.perf_counter()
            t = game.get("/replay/playback")["time"]
            for n in batch:
                stale = last[1] if last and last[0] != n else None
                pos = _probe(n, stale=stale)
                if stale is not None and math.dist(pos, stale) < 1.0:
                    continue  # la caméra n'a pas bougé : mesure périmée
                last = (n, pos)
                samples[n].append([t + (time.perf_counter() - w0 - 0.01) * speed, *pos, dead.get(n, False)])
            loops.append(time.perf_counter() - w0)
            if time.perf_counter() - t_adj > 1.0 and loops:
                new = float(min(MAX_SPEED, max(0.2, target / (sum(loops) / len(loops)))))
                if abs(new - speed) / speed > 0.1:
                    speed = new
                    game.post("/replay/playback", {"speed": speed})
                loops, t_adj = [], time.perf_counter()
            if t > end + 0.5:
                break
            if progress:
                progress(min(1.0, (t - start) / max(1e-3, end - start)))
    finally:
        game.post("/replay/playback", {"speed": 1.0, "paused": True})
        camera.clear()
        game.post("/replay/render", {"cameraAttached": False})
        if hidden:
            hide(False)
    return {"format": FORMAT, "players": players, "samples": samples, "start": start, "end": end}


class Series:
    """Positions interpolables (tableaux numpy précalculés : on interroge des milliers d'instants)."""

    def __init__(self, trk):
        self.d = {}
        for n, s in trk["samples"].items():
            if len(s) < 2:
                continue
            a = np.array([r[:4] for r in s], float)
            o = np.argsort(a[:, 0], kind="stable")
            a = a[o]
            dead = np.array([bool(s[i][4]) for i in o])
            a, dead = _clean(a, dead)
            self.d[n] = (a[:, 0], a[:, 1:4], dead)

    def names(self):
        return list(self.d)

    def pos(self, n, t):
        ts, xyz, _ = self.d[n]
        return np.array([np.interp(t, ts, xyz[:, i]) for i in range(3)])

    def dead(self, n, t):
        ts, _, dead = self.d[n]
        i = int(np.clip(np.searchsorted(ts, t), 0, len(ts) - 1))
        return bool(dead[i])

    def death_time(self, n, after):
        """Premier instant >= after où n est mort (None s'il reste en vie)."""
        ts, _, dead = self.d[n]
        m = (ts >= after) & dead
        return float(ts[m][0]) if m.any() else None


def _clean(a, dead):
    """Retire les mesures aberrantes : hauteur hors carte, ou point isolé loin de ses deux voisins (une mesure
    ratée), alors qu'un vrai déplacement (flash, dash) reste à la nouvelle position ensuite."""
    ok = (a[:, 2] > -400) & (a[:, 2] < 1200)
    a, dead = a[ok], dead[ok]
    if len(a) < 3:
        return a, dead
    keep = np.ones(len(a), bool)
    for i in range(1, len(a) - 1):
        dp = math.hypot(*(a[i, [1, 3]] - a[i - 1, [1, 3]]))
        dn = math.hypot(*(a[i, [1, 3]] - a[i + 1, [1, 3]]))
        span = math.hypot(*(a[i + 1, [1, 3]] - a[i - 1, [1, 3]]))
        if dp > 450 and dn > 450 and span < 0.3 * min(dp, dn):
            keep[i] = False
    return a[keep], dead[keep]


def positions_at(trk, t):
    """{name: (x, y, z, dead)} interpolé au temps t."""
    s = trk if isinstance(trk, Series) else Series(trk)
    out = {}
    for n in s.names():
        x, y, z = s.pos(n, t)
        out[n] = (float(x), float(y), float(z), s.dead(n, t))
    return out


def smooth(v, dt, sigma):
    if sigma <= 0 or len(v) < 3:
        return v
    r = max(1, int(3 * sigma / dt))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) * dt / sigma) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(v, r, mode="edge"), k, "valid")


def star_path(s, star, t0, t1, dt=0.05, sigma=0.3, sigma_y=1.0, radius=1500.0):
    """Position lissée du joueur suivi (lissage gaussien non causal : pas de retard sur le joueur).
    Mort : on reste sur sa dernière position. Renvoie (ts, centres Nx3, étendue N) ; l'étendue = distance du champion
    vivant le plus éloigné à moins de radius (pour élargir le plan pendant un combat)."""
    ts = np.arange(t0, t1 + dt / 2, dt)
    pts, spr = [], []
    ref = None
    for t in ts:
        if star in s.d and not s.dead(star, t):
            ref = s.pos(star, t)
        if ref is None and star in s.d:
            ref = s.pos(star, t)
        c = ref if ref is not None else np.zeros(3)
        ds = []
        for n in s.names():
            if n == star or s.dead(n, t):
                continue
            p = s.pos(n, t)
            d = math.hypot(p[0] - c[0], p[2] - c[2])
            if d < radius:
                ds.append(d)
        pts.append(c)
        spr.append(max(ds) if ds else 300.0)
    pts = np.array(pts)
    out = np.stack([smooth(pts[:, 0], dt, sigma), smooth(pts[:, 1], dt, sigma_y), smooth(pts[:, 2], dt, sigma)], 1)
    return ts, out, smooth(np.array(spr), dt, 1.0)


def check(trk, star):
    """Repérage plausible ? (la sonde peut cesser de répondre : positions figées/identiques ou sous le sol)."""
    s = trk["samples"].get(star) or []
    if len(s) < 3:
        return f"{star} non repéré"
    a = np.array([r[:4] for r in s], float)
    if np.median(a[:, 2]) < -300 or np.median(a[:, 2]) > 1000:
        return f"hauteur aberrante pour {star} ({a[:, 2].min():.0f}..{a[:, 2].max():.0f})"
    firsts = {tuple(np.round(v[0][1:4])) for v in trk["samples"].values() if v}
    if len(trk["samples"]) > 1 and len(firsts) == 1:
        return "tous les champions à la même position"
    return None
