"""Capture haute qualité : duplication d'écran DXGI (ddagrab) + NVENC quasi sans perte, en temps réel.
L'enregistreur intégré du jeu est soit très compressé (webm), soit ~12x plus lent que le temps réel (png).

Le jeu tourne à vitesse constante (REC_SPEED) pendant la capture (0.2x à 48 i/s -> 240 i/s de temps de jeu).
Synchronisation : on démarre la capture jeu en pause, on lance la lecture, on remet en pause à la fin ;
les premières / dernières images qui bougent donnent l'alignement exact image <-> temps de jeu."""
import json, os, shutil, subprocess, threading, time
import numpy as np
from . import camera, game
from .paths import TOOLS

CAP_FPS = 48  # avec REC_SPEED 0.2 -> 240 i/s de jeu, et le jeu n'a besoin de rendre que 48 i/s


def focus_game():
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    os.path.join(TOOLS, "focus_game.ps1")], capture_output=True)


def record(start, end, out, seq=None, speed=0.2, render=None):
    out = os.path.abspath(out)
    camera.clear()
    game.seek(start - 0.3, paused=True)
    game.post("/replay/render", {**camera.CLEAN_RENDER, **(render or {})})
    if seq is not None:
        camera.apply(seq)
    focus_game()
    time.sleep(0.6)
    t_a = game.get("/replay/playback")["time"]
    ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                           "-i", f"ddagrab=output_idx=0:framerate={CAP_FPS}:draw_mouse=0",
                           "-c:v", "hevc_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "constqp", "-qp", "12",
                           out], stdin=subprocess.PIPE)
    time.sleep(1.2)  # quelques images figées = repère de synchro
    game.post("/replay/playback", {"speed": speed, "paused": False})
    deadline = time.time() + (end - start) / speed * 2 + 30
    while time.time() < deadline:
        time.sleep(0.05)
        if game.get("/replay/playback")["time"] >= end:
            break
    game.post("/replay/playback", {"paused": True})
    t_b = game.get("/replay/playback")["time"]
    time.sleep(1.0)
    ff.communicate(b"q", timeout=60)
    game.post("/replay/playback", {"speed": 1.0})
    sync = _sync(out, t_a, t_b)
    json.dump(sync, open(out + ".json", "w"))
    return out


LIVE_FPS = 60


AUDIO_EXE = os.path.join(TOOLS, "gameaudio.exe")


def game_pid():
    r = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-Process 'League of Legends').Id"],
                       capture_output=True, text=True)
    return int(r.stdout.split()[0])


def record_live(start, end, out, seq=None, render=None, preroll=8.0, announce=(), hide_bars=()):
    """Enregistrement simple, comme OBS : capture d'écran à 60 i/s fixes pendant que le replay joue à vitesse
    normale, puis on coupe [start, end]. Aucune image n'est triée, recalée ni remplacée au montage.
    Le jeu est bloqué à 60 i/s (FrameCapType=5). Son : uniquement celui du jeu (tools/gameaudio.exe).
    Pré-roulage de 8 s après le seek : après un seek, le replay recale les positions des champions pendant plusieurs
    secondes (sauts d'~500 ms visibles au début du clip avec seulement 1.5 s de marge).
    hide_bars : fenêtres de temps de jeu sans barres de vie (transitions : l'interface ne prend pas le brouillard)."""
    out = os.path.abspath(out)
    camera.clear()
    t_pre = start - preroll
    game.seek(t_pre, paused=True)
    game.post("/replay/render", {**camera.CLEAN_RENDER, **(render or {})})
    game.post("/replay/playback", {"speed": 1.0})
    if seq is not None:
        camera.apply(seq)
    focus_game()
    time.sleep(0.6)
    wav = os.path.splitext(out)[0] + ".wav"
    aud = subprocess.Popen([AUDIO_EXE, str(game_pid()), wav], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True)
    aud.stdout.readline()  # "ready"
    ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                           "-i", f"ddagrab=output_idx=0:framerate={LIVE_FPS}:draw_mouse=0",
                           # qualité quasi sans perte (fichier temporaire) : p4/hq tient les 60 i/s sur la RTX 3070
                           "-c:v", "hevc_nvenc", "-preset", "p4", "-tune", "hq", "-rc", "constqp", "-qp", "10", out],
                          stdin=subprocess.PIPE)
    time.sleep(1.2)  # images figées au début = repère du départ
    g_start = game.get("/replay/playback")["time"]
    game.post("/replay/playback", {"paused": False})
    log = []  # (horloge, temps de jeu) : sert à caler le son
    deadline = time.time() + (end - t_pre) * 3 + 30
    shown = False  # annonces du jeu affichées seulement dans les fenêtres <announce> (quadra/penta)
    bars = True
    while time.time() < deadline:
        t0 = time.perf_counter()
        g = game.get("/replay/playback")["time"]
        want = any(a <= g <= b for a, b in announce)
        if want != shown:
            game.post("/replay/render", {"interfaceAnnounce": want})
            shown = want
        want_bars = not any(a <= g <= b for a, b in hide_bars)
        if want_bars != bars:
            game.post("/replay/render", {"healthBarChampions": want_bars})
            bars = want_bars
        log.append((0.5 * (t0 + time.perf_counter()), g))
        if g >= end + 0.5:  # marge : pas de gel final
            break
        time.sleep(0.02)
    game.post("/replay/playback", {"paused": True})
    if shown:
        game.post("/replay/render", {"interfaceAnnounce": False})
    if not bars:
        game.post("/replay/render", {"healthBarChampions": True})
    time.sleep(1.0)
    ff.communicate(b"q", timeout=60)
    aud.communicate("q\n", timeout=60)
    i0 = _first_moving(out)  # 1re image où le jeu bouge = instant du départ (temps de jeu g_start)
    # on coupe au TEMPS de la vidéo (horodatages), pas au numéro d'image : ddagrab laisse parfois des trous de
    # 33-50 ms, et compter les images faisait dériver/accélérer le montage
    pts = sorted(float(x) for x in subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "packet=pts_time", "-of", "csv=p=0", out],
        capture_output=True, text=True).stdout.split())
    ss = pts[i0] - pts[0] + (start - g_start)
    s = {"mode": "live", "fps": LIVE_FPS, "ss": ss, "start": float(start), "end": float(end),
         # images ratées par le jeu (ciel, image vide ; 1 à 3 images) : temps vidéo, remplacées au montage
         "glitches": [pts[i] - pts[0] for i in find_glitches(out) if i < len(pts)]}
    if os.path.exists(wav + ".json"):
        am = json.load(open(wav + ".json"))
        # à vitesse 1 : temps de jeu = horloge + c ; c estimé (médiane) sur tout le journal de lecture
        w, g = np.array(log).T
        mv = g > g_start + 0.05
        c = float(np.median(g[mv] - w[mv]))
        s.update(audio=wav, audio_start=float(start - c - am["t0"]))
    json.dump(s, open(out + ".json", "w"))
    return out


def _first_moving(path, w=172, h=72):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-t", "4", "-vf", f"scale={w}:{h}", "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], capture_output=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.int16)
    d = np.abs(np.diff(f, axis=0)).mean((1, 2))
    moving = np.nonzero(d > 0.15)[0]
    return int(moving[0] + 1) if len(moving) else 0


def _sync(path, t_a, t_b):
    """Trouve la 1re et la dernière image en mouvement -> correspondance linéaire image -> temps de jeu."""
    w, h = 172, 72
    raw = subprocess.run(["ffmpeg", "-v", "error", "-hwaccel", "cuda", "-i", path, "-vf", f"scale={w}:{h}", "-fps_mode",
                          "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
    n = len(raw) // (w * h)
    f = np.frombuffer(raw[: n * w * h], np.uint8).reshape(n, w, h).astype(np.int16)
    d = np.abs(np.diff(f, axis=0)).mean((1, 2))
    moving = np.nonzero(d > 0.15)[0]
    if len(moving) < 2:
        raise RuntimeError("capture : aucune image en mouvement (le jeu était-il au premier plan ?)")
    i0, i1 = int(moving[0]), int(moving[-1] + 1)
    # images i0..i1 couvrent le temps de jeu t_a..t_b
    # images identiques à la précédente pendant le mouvement = le jeu n'a pas rendu à temps -> à interpoler
    dups = set(int(i + 1) for i in np.nonzero(d[i0:i1] <= 0.04)[0] + i0)
    # images "glitch" (raté de rendu du jeu : image blanche/vide isolée) : très différentes de leurs voisines
    # alors que les voisines se ressemblent -> traitées comme manquantes, donc interpolées
    for gap in (1, 2):
        for i in range(max(1, i0), min(n - gap, i1)):
            skip = np.abs(f[i + gap] - f[i - 1]).mean()
            if all(np.abs(f[i + k] - f[i - 1]).mean() > 6 * skip + 8 for k in range(gap)):
                dups.update(range(i, i + gap))
    dups = sorted(dups)
    return {"i0": i0, "i1": i1, "t0": t_a, "t1": t_b, "frames": n, "dups": dups,
            "rate": (i1 - i0) / max(1e-6, t_b - t_a), "dup_ratio": len(dups) / max(1, i1 - i0)}


# --- Capture "hors ligne" par l'enregistreur du jeu (enforceFrameRate) : le jeu calcule chaque image à un pas de
# temps fixe, quel que soit le temps de rendu -> aucune saccade possible, et le PC reste utilisable pendant la capture.
# 60 i/s à 0.25x = 240 i/s de temps de jeu (de quoi faire des ralentis 0.25x sans interpolation).
# Constats (16.19) :
#  - le champ "replaySpeed" de l'enregistreur est ignoré -> on règle la vitesse de lecture avant de démarrer ;
#  - en webm sans perte, un enregistrement de plus de ~600-800 images échoue sans écrire de fichier, et découper en
#    morceaux crée des sauts : chaque morceau repart d'un seek et le replay met ~1 s de jeu à se recaler
#    (positions/animations différentes) -> on enregistre en PNG, d'une traite, avec un pré-roulage jeté ;
#  - en PNG, le jeu crée un sous-dossier "<patch>_<partie>_NN" dans path, images 000000.png...
OFF_FPS, OFF_SPEED = 60, 0.25
PREROLL = 1.0  # secondes de jeu enregistrées puis jetées, le temps que le replay se recale après le seek


def recording_state():
    return game.get("/replay/recording")


class _PngStream(threading.Thread):
    """Encode au fil de l'eau les PNG écrits par le jeu (HEVC NVENC qp 12, quasi sans perte) et les supprime :
    sans ça, ~5 Mo/image -> 13 Go pour 12 s de jeu."""

    def __init__(self, root, out, skip):
        super().__init__(daemon=True)
        self.root, self.out, self.skip = root, out, skip
        self.done = threading.Event()
        self.count, self.error = 0, None

    def _folder(self):
        subs = [os.path.join(self.root, d) for d in os.listdir(self.root)]
        subs = [d for d in subs if os.path.isdir(d)]
        return max(subs, key=os.path.getmtime) if subs else None

    def run(self):
        enc = None
        try:
            k, folder = 0, None
            while True:
                folder = folder or self._folder()
                f = os.path.join(folder, f"{k:06d}.png") if folder else None
                nxt = os.path.join(folder, f"{k + 1:06d}.png") if folder else None
                # une image est complète quand la suivante existe (ou quand l'enregistrement est fini)
                if f and os.path.exists(f) and (os.path.exists(nxt) or self.done.is_set()):
                    if k >= self.skip:
                        if enc is None:
                            enc = subprocess.Popen(
                                ["ffmpeg", "-y", "-v", "error", "-f", "image2pipe", "-c:v", "png",
                                 "-framerate", str(OFF_FPS), "-i", "-", "-c:v", "hevc_nvenc", "-preset", "p5",
                                 "-tune", "hq", "-rc", "constqp", "-qp", "12", self.out], stdin=subprocess.PIPE)
                        with open(f, "rb") as fh:
                            enc.stdin.write(fh.read())
                        self.count += 1
                    for _ in range(20):
                        try:
                            os.remove(f)
                            break
                        except PermissionError:
                            time.sleep(0.05)
                    k += 1
                elif self.done.is_set() and not (f and os.path.exists(f)):
                    break
                else:
                    time.sleep(0.02)
            if folder:
                shutil.rmtree(folder, ignore_errors=True)
        except Exception as e:
            self.error = e
        finally:
            if enc:
                enc.stdin.close()
                enc.wait()


def record_offline(start, end, out, seq=None, render=None, progress=None):
    out = os.path.abspath(out)
    if os.path.exists(out):
        os.remove(out)
    tmp = out + "_png"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    rec_start = start - PREROLL
    camera.clear()
    game.seek(rec_start - 0.3, paused=True)
    game.post("/replay/render", {**camera.CLEAN_RENDER, **(render or {})})
    if seq is not None:
        camera.apply(seq)
    game.post("/replay/playback", {"speed": OFF_SPEED})
    skip = int(round(PREROLL * OFF_FPS / OFF_SPEED))
    stream = _PngStream(tmp, out, skip)
    stream.start()
    game.post("/replay/recording", {"recording": True, "codec": "png", "path": tmp, "framesPerSecond": OFF_FPS,
                                    "enforceFrameRate": True, "startTime": float(rec_start), "endTime": float(end)})
    t_begin = time.time()
    started = False
    try:
        while time.time() - t_begin < 4 * 3600:
            time.sleep(1.0)
            r = recording_state()
            if r.get("recording"):
                started = True
                if progress:
                    progress(min(1.0, max(0.0, (r.get("currentTime", rec_start) - rec_start) / (end - rec_start))))
            elif started or time.time() - t_begin > 30:
                break
    finally:
        game.post("/replay/playback", {"paused": True, "speed": 1.0})
        stream.done.set()
        stream.join()
        shutil.rmtree(tmp, ignore_errors=True)
    if not started:
        raise RuntimeError(f"l'enregistreur du jeu n'a pas démarré : {recording_state()}")
    if stream.error:
        raise stream.error
    n = stream.count
    bad = find_glitches(out)
    # image k <-> temps de jeu start + k * OFF_SPEED / OFF_FPS (pas fixe garanti par enforceFrameRate)
    rate = OFF_FPS / OFF_SPEED
    sync = {"i0": 0, "i1": n, "t0": float(start), "t1": float(start) + n / rate, "frames": n, "dups": bad,
            "rate": rate, "dup_ratio": len(bad) / max(1, n), "secs": round(time.time() - t_begin, 1), "mode": "offline"}
    json.dump(sync, open(out + ".json", "w"))
    return out


def find_glitches(path, w=172, h=72):
    """Images ratées par le jeu (image vide cyan, image d'un autre instant) : isolées (1 à 3 images), très
    différentes de leurs voisines alors que les voisines encadrantes se ressemblent (240 i/s de jeu)."""
    # passthrough : une image décodée par image enregistrée (indices = ordre des horodatages)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-hwaccel", "cuda", "-i", path, "-vf", f"scale={w}:{h}",
                          "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    n = len(raw) // (w * h * 3)
    f = np.frombuffer(raw[: n * w * h * 3], np.uint8).reshape(n, h, w, 3).astype(np.int16)
    bad = set()
    for gap in (1, 2, 3):
        for i in range(1, n - gap):
            if i - 1 in bad or i + gap in bad:  # une image ratée ne sert pas de référence (ratés rapprochés)
                continue
            ref = np.abs(f[i + gap] - f[i - 1]).mean()
            if all(np.abs(f[i + k] - f[i - 1]).mean() > 3 * ref + 6 and
                   np.abs(f[i + k] - f[i + gap]).mean() > 3 * ref + 6 for k in range(gap)):
                bad.update(range(i, i + gap))
    return sorted(bad)
