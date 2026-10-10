"""Orchestration automatique : replay ouvert -> clips finaux (1440p60), sans intervention.
Pendant la capture, le jeu doit rester au premier plan : ne pas toucher au PC."""
import json, os, threading, time, traceback
from . import banner as banners, camera, capture, cinema, cover, director, fogfx, game, montage, music, tracking
from .paths import RENDERS, PROJECTS

STATE = {"step": "idle", "progress": 0.0, "log": [], "results": [], "error": None, "plays": [], "actions": [], "kills": []}


def log(msg):
    STATE["log"] = (STATE["log"] + [f"{time.strftime('%H:%M:%S')} {msg}"])[-80:]
    try:
        print(msg, flush=True)
    except Exception:  # console Windows : certains pseudos (ø, ł...) ne s'affichent pas
        pass


def step(name, p=None):
    STATE["step"] = name
    if p is not None:
        STATE["progress"] = p
    log(name)


def busy():
    return STATE["step"] not in ("idle", "done", "error")


def run_async(info, n=3, focus=None, music=None, ids=None, mode="cinema", follow=None, pre=None, post=None,
              clips=None, close=True, loudness=None, fmt="cinema", grade=True, combine=False, zoom=1.0,
              follows=None, fog=None, banner=True):
    """clips : passages choisis sur la timeline [{start, end, follow}] ; ids : actions cochées dans la liste ;
    sinon les n meilleures. follow : joueur suivi (sinon l'auteur). close : fermer le jeu à la fin.
    fmt : format de sortie (montage.FORMATS) ; grade : filtre couleur ; zoom : distance caméra (x) ;
    combine : une seule vidéo, actions reliées par une traversée du brouillard.
    music : nom d'un morceau (music.tracks()) ; son drop est calé sur le 1er kill.
    follows : {id d'action: joueur suivi} (une caméra par action, pour enchaîner deux joueurs) ;
    fog : densité (0..1) des volutes du brouillard entre deux actions des clips combinés ;
    banner : encadré du joueur suivi (région, rang, pseudo, champion) au début de chaque action."""
    if busy():
        return False
    STATE.update(log=[], results=[], error=None, plays=[], progress=0.0)
    opts = {"n": n, "focus": focus, "ids": ids or [], "mode": mode if mode in cinema.MODES else "cinema",
            "follow": follow, "pre": pre, "post": post, "clips": clips or [], "close": close,
            "loudness": loudness, "fmt": fmt if fmt in montage.FORMATS else "cinema", "grade": bool(grade),
            "combine": bool(combine), "music": music or None, "zoom": float(min(1.6, max(0.5, zoom or 1.0))),
            "follows": follows or {}, "banner": bool(banner), "info": info,
            "fog": float(min(1.0, max(0.0, 0.8 if fog is None else fog)))}
    threading.Thread(target=_run, args=(info, opts), daemon=True).start()
    return True


def analyze_async(info):
    """Analyse seule (balayage des événements) -> STATE["actions"] pour la liste de l'interface."""
    if busy():
        return False
    STATE.update(log=[], error=None, progress=0.0)
    threading.Thread(target=_analyze, args=(info,), daemon=True).start()
    return True


def _pdir(info):
    name = os.path.splitext(os.path.basename(info["path"]))[0]
    pdir = os.path.join(PROJECTS, name)
    os.makedirs(pdir, exist_ok=True)
    return name, pdir


def load_actions(info):
    """Actions déjà connues (analyse faite lors d'une séance précédente), sans ouvrir le jeu."""
    f = os.path.join(PROJECTS, os.path.splitext(os.path.basename(info["path"]))[0], "events.json")
    _set_events(director.normalize(json.load(open(f, encoding="utf-8")), info.get("players")) if os.path.exists(f) else [])
    return STATE["actions"]


def _set_events(events):
    """Actions (liste) + kills un par un (timeline) pour l'interface."""
    STATE["actions"] = _public(director.all_actions(events)) if events else []
    STATE["kills"] = [{"t": e["EventTime"], "killer": e.get("KillerName"), "victim": e.get("VictimName"),
                       "assisters": e.get("Assisters", [])} for e in events if e["EventName"] == "ChampionKill"]


def _public(actions):
    keys = ("id", "label", "star", "kills", "time", "template", "score")
    return [{**{k: a[k] for k in keys},
             "victims": [e.get("VictimName") for e in a["kill_events"]],
             "assisters": sorted({x for e in a["kill_events"] for x in e.get("Assisters", [])})} for a in actions]


def _analyze(info):
    try:
        name, pdir = _pdir(info)
        step("Attente du replay")
        length = game.wait_ready(600)["length"]
        _set_events(_events(pdir, length, info))
        step("done", 1.0)
        log(f"{len(STATE['actions'])} actions trouvées")
    except Exception as e:
        STATE["error"] = str(e)
        log(traceback.format_exc())
        STATE["step"] = "error"


def _events(pdir, length, info):
    f = os.path.join(pdir, "events.json")
    if os.path.exists(f):
        ev = json.load(open(f, encoding="utf-8"))
    else:
        step("Analyse des actions du replay", 0.0)
        ev = director.scan_events(length, lambda x: STATE.update(progress=x))
        json.dump(ev, open(f, "w", encoding="utf-8"), indent=1)
    return director.normalize(ev, info.get("players"))


def _tracks(play, cdir):
    """Repérage des champions du passage (mis en cache, clé = fenêtre + joueur suivi)."""
    follow = play["follow"]
    key = cinema.seed_for(follow, *[(round(sg["start"], 1), round(sg["end"], 1)) for sg in play["segments"]])
    tr_file = os.path.join(cdir, f"track{tracking.FORMAT}_{key:08x}.json")
    if os.path.exists(tr_file):
        return json.load(open(tr_file))
    span = sum(sg["end"] - sg["start"] + 2 for sg in play["segments"])
    cover.show(f"Repérage des champions · {play['label']} de {play['star']}")
    speed = tracking.speed_for(span)
    log(f"  repérage des champions ({span:.0f} s de jeu, ~{span / speed / 60:.1f} min)")
    base = STATE["step"]
    done = [0.0]  # secondes de jeu déjà repérées (segments précédents)
    t_rep = time.time()

    def progress(x, sg_len):
        cur = done[0] + x * sg_len
        el = time.time() - t_rep
        left = el * (span - cur) / cur if cur > 1 else span / speed
        STATE["step"] = f"{base} · repérage {100 * cur / span:.0f} % (reste ~{left:.0f} s)"
        cover.say(f"Repérage des champions · {100 * cur / span:.0f} %  (reste ~{left:.0f} s)")

    names = tracking.nearby(follow, play["segments"][0]["start"])
    # tueurs et victimes aussi, même loin (kill à longue distance : la caméra va les chercher)
    names = sorted(set(names) | {e[k] for e in play.get("kill_events", []) for k in ("VictimName", "KillerName")
                                  if e.get(k)} | {follow})
    tracks = []
    for sg in play["segments"]:
        n = sg["end"] - sg["start"] + 2
        tracks.append(tracking.track(sg["start"] - 1, sg["end"] + 1, follow, names=names, speed=speed,
                                     progress=lambda x, n=n: progress(x, n)))
        done[0] += n
    STATE["step"] = base
    bad = [tracking.check(t, follow) for t in tracks]
    if any(bad):  # au cas où le jeu ne calcule plus la caméra quand rien n'est affiché : repérage visible
        log("  repérage masqué invalide, nouvel essai à l'écran")
        cover.hide()
        tracks = [tracking.track(sg["start"] - 1, sg["end"] + 1, follow, names=names, hidden=False)
                  for sg in play["segments"]]
        bad = [tracking.check(t, follow) for t in tracks]
    if any(bad):
        raise RuntimeError("repérage des champions invalide : " + "; ".join(b for b in bad if b))
    json.dump(tracks, open(tr_file, "w"))
    return tracks


def _top_calibration(pdir, follow, t):
    """Angle/distance/focale de la caméra normale de LoL, relevés une fois par replay."""
    f = os.path.join(pdir, "topcam.json")
    if os.path.exists(f):
        return json.load(open(f))
    cover.show("Réglage de la vue du dessus")
    game.seek(t, paused=True)
    star = tracking._probe(follow)
    cal = camera.calibrate_top(tracking._select_key(follow), star)
    log(f"  vue du dessus : angle {cal['pitch']:.1f}°, distance {cal['dist']:.0f}, focale {cal['fov']:.0f}"
        + ("" if cal["calibrated"] else " (valeurs par défaut)"))
    if cal["calibrated"]:
        json.dump(cal, open(f, "w"))
    return cal


def prepare(play, pdir, name, o):
    """Repérage + plans caméra d'une action -> {play, shots, cdir, tag}."""
    mode, follow = o["mode"], play["follow"]
    tag = f"{play['label']}_{int(play['start'])}"
    cdir = os.path.join(pdir, tag)
    os.makedirs(cdir, exist_ok=True)
    # écran noir par-dessus le jeu pendant le repérage (caméra qui saute d'un champion à l'autre, carte masquée),
    # affiché seulement si le repérage n'est pas déjà en cache
    try:
        tracks = _tracks(play, cdir)
        top = _top_calibration(pdir, follow, play["start"]) if mode == "top" else None
    finally:
        cover.hide()
    seed = cinema.seed_for(name, play["start"], play["template"])
    w, h, _ = montage.FORMATS[o["fmt"]]
    # l'image finale est recadrée au centre d'une capture plein écran : son ratio limite la largeur visible
    shots, v = cinema.direct(play, tracks, seed, mode=mode, top=top, zoom=o["zoom"], aspect=w / h)
    if mode == "top":
        log(f"  vue du dessus, suivi de {follow}")
    else:
        log(f"  plan '{v['template']}' (yaw {v['yaw0']:.0f}°, dérive {v['drift']:.0f}°, roulis {v['roll']:.0f}°),"
            f" suivi de {follow}")
    longs = [k for sh in shots for k in sh["long_kills"]]
    if longs:
        log(f"  {len(longs)} kill(s) à distance : la caméra suit jusqu'à la victime")
    for sh in shots:
        sh.setdefault("lead", 0.0)
    return {"play": play, "shots": shots, "cdir": cdir, "tag": tag}


def link(preps):
    """Clips combinés : la fin de chaque action se prolonge de fogfx.T_IN s (la caméra avance dans le brouillard) et
    l'action suivante commence fogfx.T_OUT s plus tôt (elle sort du brouillard en avançant). Pas de trajet de caméra
    entre les deux : la coupe se fait quand le brouillard couvre tout l'écran."""
    for a, b in zip(preps, preps[1:]):
        last = a["shots"][-1]
        sg = last["segment"] = dict(last["segment"])
        cinema.dolly(last["sequence"], sg["end"], sg["end"] + fogfx.T_IN, entering=True)
        sg["end"] += fogfx.T_IN
        first = b["shots"][0]
        lead = min(0.6, fogfx.T_OUT)  # (clés caméra disponibles 0.6 s avant le segment)
        t0 = first["segment"]["start"] - lead
        cinema.dolly(first["sequence"], t0, t0 + fogfx.T_OUT, entering=False,
                     amount=cinema.DOLLY * fogfx.T_OUT / fogfx.T_IN)  # même vitesse de part et d'autre de la coupe
        first["lead"] = lead
        b["fog"] = True
        log(f"  transition {a['tag']} -> {b['tag']} : traversée du brouillard")


def capture_prep(prep, mode):
    """Capture à l'écran de tous les segments d'une action -> [(capture, début d'une nouvelle action après le
    brouillard ?, action si c'est sa 1re capture)]."""
    play, parts = prep["play"], []
    render = {"depthFogEnabled": False} if mode == "top" else None
    # annonces du jeu (bandeau + voix) uniquement autour des 4e et 5e kills d'un quadra/penta
    # (activées 1.5 s avant : le jeu met ~1 s à afficher l'interface)
    ann = [(k - 1.5, k + 7.0) for k in play.get("announce", [])]
    for i, sh in enumerate(prep["shots"]):
        log(f"  capture {prep['tag']} segment {i + 1}/{len(prep['shots'])}"
            f" (temps réel : ~{sh['segment']['end'] - sh['segment']['start'] + sh['lead'] + 12:.0f} s)")
        sg = sh["segment"]
        raw = capture.record_live(sg["start"] - sh["lead"], sg["end"], os.path.join(prep["cdir"], f"cap{i}.mkv"),
                                  sh["sequence"], render=render, announce=ann)
        parts.append((raw, i == 0 and prep.get("fog", False), prep["play"] if i == 0 else None))
    return parts


def first_kill(prep):
    """Instant (s, depuis le début de la vidéo de cette action) du 1er kill ; sans kill : 2 s après le début."""
    sh = prep["shots"][0]
    t0 = sh["segment"]["start"] - sh["lead"]
    kills = prep["play"].get("kills") or []
    return (min(kills) - t0) if kills else sh["lead"] + 2.0


def _music(o, sync):
    """(fichier, instant du morceau au début de la vidéo) pour que le drop tombe à <sync> s, ou None."""
    if not o["music"]:
        return None
    path = music.path_for(o["music"])
    if not path:
        log(f"  musique introuvable : {o['music']} (clip sans musique)")
        return None
    d = music.drop(path)
    log(f"  musique : {os.path.basename(path)} (drop à {d:.2f} s calé sur le 1er kill, à {sync:.2f} s)")
    return path, d - sync


def _banners(parts, o):
    """Encadrés de présentation : [(vidéo, instant d'apparition dans le montage, y)], un par action, sur le joueur
    suivi. Apparaît après la sortie du brouillard, disparaît avant le brouillard suivant."""
    if not o["banner"]:
        return []
    info = o.get("info") or {}
    by = {p.get("name"): p for p in info.get("players", [])}
    w, h, _ = montage.FORMATS[o["fmt"]]
    durs = []
    for raw, *_ in parts:
        m = json.load(open(raw + ".json"))
        durs.append(m["end"] - m["start"])
    starts = [k for k, (_, _, play) in enumerate(parts) if play]
    out = []
    for j, k in enumerate(starts):
        play = parts[k][2]
        p = by.get(play["follow"]) or {}
        t0 = sum(durs[:k])
        span = sum(durs[k:starts[j + 1] if j + 1 < len(starts) else len(parts)])
        delay = fogfx.T_OUT - 0.1 if parts[k][1] else banners.DELAY
        tail = fogfx.T_IN + 0.3 if j + 1 < len(starts) else 0.5
        dur = span - delay - tail
        if dur < 1.5:
            continue
        rank = p.get("rank") or {}
        try:
            f, y = banners.cached(w, h, play["follow"], p.get("champion") or play["follow"], rank.get("tier"),
                                  info.get("region", ""), dur)
        except Exception as e:
            log(f"  encadré de {play['follow']} impossible : {e}")
            continue
        out.append((f, t0 + delay, y))
        log(f"  encadré : {play['follow']} ({p.get('champion')}, {rank.get('tier') or 'rang inconnu'})")
    return out


def finish(parts, out, o, sync=0.0):
    log(f"  montage {os.path.basename(out)}")
    montage.assemble([p for p, *_ in parts], out, grade=o["grade"], flash=(o["mode"] == "cinema"),
                     loudness=o["loudness"], fmt=o["fmt"], fogs=[k for k, (_, f, _) in enumerate(parts) if f],
                     music=_music(o, sync), fog_strength=o["fog"], banners=_banners(parts, o))
    for p, *_ in parts if not os.environ.get("LOLCLIP_KEEP_RAW") else []:  # les captures sont lourdes
        for f in (p, p + ".json", os.path.splitext(p)[0] + ".wav", os.path.splitext(p)[0] + ".wav.json"):
            try:
                os.remove(f)
            except OSError:
                pass
    return out


def _out_name(name, tag, follow, o):
    who = "".join(c for c in follow if c.isalnum())[:16]
    return os.path.join(RENDERS, f"{name}_{tag}_{who}_{o['mode']}_{o['fmt']}.mp4")


def make_clip(play, pdir, name, o):
    t_start = time.time()
    prep = prepare(play, pdir, name, o)
    parts = capture_prep(prep, o["mode"])
    os.makedirs(RENDERS, exist_ok=True)
    out = finish(parts, _out_name(name, prep["tag"], play["follow"], o), o, first_kill(prep))
    log(f"  -> {os.path.basename(out)} en {time.time() - t_start:.0f}s")
    return out


def _run(info, o):
    try:
        name, pdir = _pdir(info)
        step("Attente du replay")
        length = game.wait_ready(600)["length"]
        events = _events(pdir, length, info)
        _set_events(events)
        if o["clips"]:
            plays = [director.from_range(events, c["start"], c["end"], c.get("follow") or o["follow"])
                     for c in o["clips"]]
        elif o["ids"]:
            by_id = {a["id"]: a for a in director.all_actions(events)}
            plays = [director.build(by_id[i], o["pre"], o["post"], o["follows"].get(i) or o["follow"],
                                    force=bool(o["follows"].get(i))) for i in o["ids"] if i in by_id]
        else:
            plays = director.pick_plays(events, o["n"], o["focus"], o["pre"], o["post"], o["follow"])
        if not plays:
            raise RuntimeError("aucune action trouvée dans ce replay")
        STATE["plays"] = [{k: p[k] for k in ("label", "star", "follow", "start", "end", "template")} for p in plays]
        os.makedirs(RENDERS, exist_ok=True)
        if o["combine"] and len(plays) > 1:
            plays.sort(key=lambda p: p["start"])  # ordre de la partie
            preps = []
            for k, play in enumerate(plays):
                step(f"Préparation {k + 1}/{len(plays)} : {play['label']} de {play['star']}, suivi de {play['follow']}",
                     0.4 * k / len(plays))
                preps.append(prepare(play, pdir, name, o))
            link(preps)
            parts = []
            for k, prep in enumerate(preps):
                step(f"Capture {k + 1}/{len(preps)} : {prep['tag']}", 0.4 + 0.5 * k / len(preps))
                parts += capture_prep(prep, o["mode"])
            step("Montage de la vidéo combinée", 0.9)
            tag = f"combo{len(plays)}_{int(plays[0]['start'])}"
            STATE["results"].append(finish(parts, _out_name(name, tag, plays[0]["follow"], o), o,
                                           first_kill(preps[0])))
        else:
            for k, play in enumerate(plays):
                step(f"Clip {k + 1}/{len(plays)} : {play['label']} de {play['star']}, suivi de {play['follow']}",
                     k / len(plays))
                STATE["results"].append(make_clip(play, pdir, name, o))
        if o["close"]:
            log("fermeture du jeu (Vanguard reste actif)")
            game.close_replay()
        step("done", 1.0)
    except Exception as e:
        STATE["error"] = str(e)
        log(traceback.format_exc())
        STATE["step"] = "error"
