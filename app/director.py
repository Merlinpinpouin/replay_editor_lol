"""Le "réalisateur" : trouve les actions du replay et choisit le passage à monter."""
import time
from . import game

SCAN_STEP = 30  # eventdata ne renvoie que les événements récents -> on balaie le replay par seeks


def _key(e):
    # les EventID sont renumérotés à chaque seek -> on déduplique sur le contenu
    return (e["EventName"], round(e["EventTime"], 1), e.get("KillerName"), e.get("VictimName"),
            e.get("TurretKilled"), e.get("InhibKilled"), e.get("KillStreak"))


def dedupe(events):
    return sorted({_key(e): e for e in events}.values(), key=lambda e: e["EventTime"])


def scan_events(length, progress=None):
    """Balaye tout le replay et renvoie la liste des événements (dédupliqués par EventID)."""
    seen = {}
    t = SCAN_STEP
    while t < length + SCAN_STEP:
        tt = min(t, length - 5)
        try:
            game.seek(tt, paused=True)
        except TimeoutError:
            pass
        time.sleep(0.4)
        try:
            for e in game.get("/liveclientdata/eventdata").get("Events", []):
                seen[_key(e)] = e
        except Exception:
            pass
        if progress:
            progress(tt / length)
        t += SCAN_STEP
    return sorted(seen.values(), key=lambda e: e["EventTime"])


def champion_map():
    """riotId / summonerName -> {champion, team}."""
    m = {}
    for p in game.players():
        info = {"champion": p.get("championName"), "team": p.get("team")}
        for k in ("riotIdGameName", "summonerName", "riotId"):
            if p.get(k):
                m[p[k]] = info
                m[p[k].split("#")[0]] = info
    return m


def fights(events, gap=12.0):
    """Regroupe les kills de champions en combats (kills espacés de moins de <gap> s)."""
    kills = [e for e in events if e["EventName"] == "ChampionKill"]
    out, cur = [], []
    for k in kills:
        if cur and k["EventTime"] - cur[-1]["EventTime"] > gap:
            out.append(cur)
            cur = []
        cur.append(k)
    if cur:
        out.append(cur)
    return out


def score_fight(f, events, focus=None):
    s = len(f) * 10.0
    t0, t1 = f[0]["EventTime"], f[-1]["EventTime"]
    for e in events:
        if t0 - 1 <= e["EventTime"] <= t1 + 1:
            if e["EventName"] == "Multikill":
                s += 15 * e.get("KillStreak", 2)
            elif e["EventName"] == "Ace":
                s += 15
    killers = {}
    for k in f:
        killers[k["KillerName"]] = killers.get(k["KillerName"], 0) + 1
    best = max(killers.values())
    s += best * 8  # une seule personne qui fait le carnage = plus "montage"
    if focus:
        s += 25 * sum(1 for k in f if k["KillerName"] == focus or focus in k.get("Assisters", []))
    return s


PENTA_GAP = 10.0  # seul un penta saute ses temps morts (> 10 s sans kill) : partout ailleurs, le passage est continu


def _segments(kill_times, pre=4.0, post=1.5, gap=float("inf")):
    """Découpe en segments continus ; les temps morts > gap sont sautés (cut au montage)."""
    segs = []
    for t in sorted(kill_times):
        if segs and t - segs[-1]["kills"][-1] <= gap:
            segs[-1]["kills"].append(t)
        else:
            segs.append({"kills": [t]})
    for i, sg in enumerate(segs):
        sg["start"] = sg["kills"][0] - (pre if i == 0 else 2.2)
        sg["end"] = sg["kills"][-1] + post
    return segs


_ALIAS = {"monkeyking": "wukong", "nunuwillump": "nunu", "renataglasc": "renata"}


def _nkey(name):
    k = "".join(c for c in (name or "").lower() if c.isalnum())
    return _ALIAS.get(k, k)


def normalize(events, players):
    """Avec les pseudos masqués dans le jeu, les événements nomment les joueurs par leur champion (« Kai'Sa ») :
    on remet partout le pseudo (comme dans le .rofl et le repérage), sinon filtre, suivi et cadrage ne collent pas."""
    by = {}
    for p in players or []:
        if p.get("name"):
            by[_nkey(p["name"])] = p["name"]
            if p.get("champion"):
                by.setdefault(_nkey(p["champion"]), p["name"])

    def fix(n):
        if not n:
            return n
        k = _nkey(n)
        if k in by:
            return by[k]
        hit = [v for c, v in by.items() if c and (c.startswith(k) or k.startswith(c))]
        return hit[0] if len(hit) == 1 else n

    out = []
    for e in events:
        e = dict(e)
        for f in ("KillerName", "VictimName"):
            if f in e:
                e[f] = fix(e[f])
        if "Assisters" in e:
            e["Assisters"] = [fix(a) for a in e["Assisters"]]
        out.append(e)
    return out


def _champion(name):
    """Tueur = un joueur (pas une tour ni un sbire : exécutions)."""
    return bool(name) and not name.startswith(("Turret_", "Minion", "SRU_", "Barracks"))


LABELS = {2: "double", 3: "triple", 4: "quadra", 5: "penta"}
ANNOUNCE_TAIL = 4.9  # secondes gardées après le dernier kill d'un quadra/penta (annonce du jeu)


def plays(events, focus=None):
    """Toutes les actions candidates, typées par modèle de plan : multikill / solo / teamfight."""
    kills = [e for e in events if e["EventName"] == "ChampionKill"]
    out = []
    for m in (e for e in events if e["EventName"] == "Multikill"):
        k, who, T = m.get("KillStreak", 2), m.get("KillerName"), m["EventTime"]
        mine = [e for e in kills if e["KillerName"] == who and e["EventTime"] <= T + 0.5][-k:]
        if len(mine) < k:
            continue
        helpers = sum(len(e.get("Assisters", [])) for e in mine)
        tpl = "multikill" if k >= 3 or helpers > 2 else "solo"
        out.append({"star": who, "kills": [e["EventTime"] for e in mine], "score": 60 * k + (15 if helpers <= 1 else 0),
                    "label": LABELS.get(k, f"{k}kills"), "template": tpl})
    for e in kills:  # kills en solo (aucun assistant)
        if not e.get("Assisters") and _champion(e.get("KillerName")):
            out.append({"star": e["KillerName"], "kills": [e["EventTime"]], "score": 45, "label": "solo",
                        "template": "solo"})
    for f in fights(events):
        killers = {}
        for k in f:
            killers[k["KillerName"]] = killers.get(k["KillerName"], 0) + 1
        if len(f) >= 4 and len(killers) >= 2:
            star = max(killers, key=killers.get)
            out.append({"star": star, "kills": [k["EventTime"] for k in f], "score": score_fight(f, events),
                        "label": f"teamfight", "template": "teamfight"})
    for p in out:
        if focus and p["star"] == focus:
            p["score"] += 100
    out.sort(key=lambda p: -p["score"])
    return out


def all_actions(events):
    """Toutes les actions qu'on peut monter, triées par temps (liste affichée dans l'interface) :
    multikills, kills en solo, teamfights, puis tous les autres kills (avec assistance)."""
    kills = [e for e in events if e["EventName"] == "ChampionKill"]
    out = plays(events)
    covered = {(p["star"], round(t, 1)) for p in out if p["label"] != "solo" for t in p["kills"]}
    # un kill solo déjà compris dans un multikill du même joueur fait doublon
    out = [p for p in out if not (p["label"] == "solo" and (p["star"], round(p["kills"][0], 1)) in covered)]
    covered |= {(p["star"], round(p["kills"][0], 1)) for p in out if p["label"] == "solo"}
    for e in kills:
        if _champion(e.get("KillerName")) and (e["KillerName"], round(e["EventTime"], 1)) not in covered:
            out.append({"star": e["KillerName"], "kills": [e["EventTime"]], "score": 20, "label": "kill",
                        "template": "solo"})
    seen, uniq = set(), []
    for p in out:
        key = (p["label"], p["star"], tuple(round(t, 1) for t in p["kills"]))
        if key in seen:
            continue
        seen.add(key)
        p["id"] = f"{p['label']}_{p['star']}_{int(p['kills'][0] * 10)}"
        p["kill_events"] = _kill_events(kills, p)
        p["time"] = p["kills"][0]
        uniq.append(p)
    uniq.sort(key=lambda p: p["time"])
    return uniq


def _kill_events(kills, p):
    return [min(kills, key=lambda e: (abs(e["EventTime"] - t), e.get("KillerName") != p["star"])) for t in p["kills"]]


def build(p, pre=None, post=None, follow=None, force=False):
    """Complète une action avec ses segments (pre/post en secondes, sinon valeurs du modèle de plan).
    follow : joueur à suivre. Réglage général (force=False) : appliqué seulement si ce joueur fait des kills dans
    l'action ; sinon on suit l'auteur (suivre une Lux qui n'a que des assists pendant le triplé d'une Vayne faisait
    faire à la caméra des allers-retours entre Lux et chaque victime). force=True : choix fait pour cette action."""
    from .cinema import TEMPLATES
    tpl = TEMPLATES[p["template"]]
    p = dict(p)
    pre = float(pre) if pre is not None else tpl["pre"]
    post = float(post) if post is not None else tpl["post"]
    if p["label"] in ("quadra", "penta"):  # garder le bandeau + la voix de l'annonce en entier
        post = max(post, ANNOUNCE_TAIL)
    p["segments"] = _segments(p["kills"], pre=pre, post=post, gap=PENTA_GAP if p["label"] == "penta" else float("inf"))
    if "kill_events" not in p:
        p["kill_events"] = []
    p["start"], p["end"] = p["segments"][0]["start"], p["segments"][-1]["end"]
    killers = {p["star"]} | {e.get("KillerName") for e in p.get("kill_events", [])}
    p["follow"] = follow if follow and (force or follow in killers) else p["star"]
    if p["label"] in ("quadra", "penta"):
        p["announce"] = p["kills"][3:]
    return p


def from_range(events, start, end, follow=None):
    """Passage choisi sur la timeline : [start, end] joué d'une traite (aucun saut).
    Joueur suivi : celui demandé, sinon le joueur qui fait le plus de kills dans le passage.
    Effets caméra : sur les kills où le joueur suivi est tueur ou assistant."""
    start, end = float(min(start, end)), float(max(start, end))
    kills = [e for e in events if e["EventName"] == "ChampionKill" and start <= e["EventTime"] <= end]
    if not follow:
        by = {}
        for e in kills:
            if _champion(e.get("KillerName")):
                by[e["KillerName"]] = by.get(e["KillerName"], 0) + 1
        follow = max(by, key=by.get) if by else None
    if not follow:
        raise RuntimeError(f"passage {start:.0f}-{end:.0f} s : aucun kill, choisis le joueur à suivre")
    mine = [e for e in kills if e.get("KillerName") == follow or follow in e.get("Assisters", [])]
    own = [e for e in mine if e.get("KillerName") == follow]
    n = len(own)
    # vrai multikill seulement si le jeu l'a annoncé (sinon : « 4kills » = 4 kills espacés dans le passage)
    streak = max((m.get("KillStreak", 0) for m in events if m["EventName"] == "Multikill"
                  and m.get("KillerName") == follow and start <= m["EventTime"] <= end + 1), default=0)
    label = (LABELS[streak] if streak in LABELS else f"{n}kills" if n >= 2 else "kill" if n == 1
             else "teamfight" if len(kills) >= 4 else "passage")
    template = "multikill" if n >= 3 else ("teamfight" if len(kills) >= 4 else "solo")
    announce = [e["EventTime"] for m in events if m["EventName"] == "Multikill" and m.get("KillerName") == follow
                and m.get("KillStreak", 0) >= 4 and start <= m["EventTime"] <= end + 1
                for e in own if abs(e["EventTime"] - m["EventTime"]) < 0.6]
    return {"star": follow, "follow": follow, "label": label, "template": template,
            "kills": [e["EventTime"] for e in own] or [e["EventTime"] for e in mine],
            "kill_events": mine, "announce": announce,
            "segments": [{"start": start, "end": end, "kills": [e["EventTime"] for e in mine]}],
            "start": start, "end": end}


def pick_plays(events, n=3, focus=None, pre=None, post=None, follow=None):
    """Les n meilleures actions qui ne se chevauchent pas, avec leurs segments."""
    kills = [e for e in events if e["EventName"] == "ChampionKill"]
    chosen = []
    for p in plays(events, focus):
        p["kill_events"] = _kill_events(kills, p)
        p = build(p, pre, post, follow)
        if any(p["start"] < c["end"] + 5 and p["end"] > c["start"] - 5 for c in chosen):
            continue
        chosen.append(p)
        if len(chosen) >= n:
            break
    return chosen


def pick_highlight(events, focus=None):
    ps = pick_plays(events, 1, focus)
    return ps[0] if ps else None
