"""Musique de fond : liste des morceaux disponibles + repérage automatique du drop (calé ensuite sur le 1er kill)."""
import json, os, subprocess
import numpy as np
from .paths import ROOT

FOLDERS = [os.path.join(ROOT, "music"), os.path.join(ROOT, "infos")]  # music/ en priorité
EXTS = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus")
CACHE = os.path.join(ROOT, "music", "drops.json")
SR, HOP = 8000, 0.05  # analyse en mono 8 kHz, énergie toutes les 50 ms


def tracks():
    """Noms des morceaux (fichiers audio de music/ puis infos/), sans doublon."""
    out = []
    for d in FOLDERS:
        if os.path.isdir(d):
            out += [f for f in sorted(os.listdir(d)) if f.lower().endswith(EXTS) and f not in out]
    return out


def path_for(name):
    name = os.path.basename(name or "")
    for d in FOLDERS:
        p = os.path.join(d, name)
        if name and os.path.isfile(p):
            return p
    return None


def _energy(path):
    """Énergie (dB par tranche de 50 ms) : tout le spectre, et les basses seules (< 160 Hz : kick + basse, ce qui
    entre au drop ; une montée de synthé aiguë avant le drop fait monter le volume mais pas les basses)."""
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
    n = int(SR * HOP)
    x = x[:len(x) // n * n].reshape(-1, n)
    spec = np.abs(np.fft.rfft(x * np.hanning(n), axis=1)) ** 2
    bass = spec[:, :int(160 * n / SR) + 1].sum(1) / spec.shape[1]
    return 10 * np.log10(np.mean(x ** 2, axis=1) + 1e-9), 10 * np.log10(bass + 1e-9)


def find_drop(path, jump=4.0):
    """Instant (s) du drop : le 1er moment où le morceau entre dans une partie forte (moyenne des 4 s suivantes
    parmi les 30 % les plus fortes du morceau) avec un saut d'au moins <jump> dB (moyenne tout le spectre / basses)
    par rapport à la seconde d'avant. Pas de tel moment : le plus grand saut du morceau. L'instant est ensuite calé
    sur l'attaque (la tranche de 50 ms où les basses montent le plus)."""
    full, bass = _energy(path)
    b, a = int(1.0 / HOP), int(4.0 / HOP)
    if len(full) < b + a + 1:
        return 0.0

    def jumps(e):
        c = np.concatenate([[0.0], np.cumsum(e)])
        i = np.arange(b, len(e) - a)
        after = (c[i + a] - c[i]) / a
        return i, after, after - (c[i] - c[i - b]) / b

    i, after, g_full = jumps(full)
    _, after_b, g_bass = jumps(bass)
    gain = 0.4 * g_full + 0.6 * g_bass
    loud = (after >= np.percentile(after, 70) - 1.0) & (after_b >= np.percentile(after_b, 60) - 1.0)
    ok = loud & (gain >= jump)
    if not ok.any():
        k = int(np.argmax(gain))
    else:
        k = int(np.argmax(ok))
        k += int(np.argmax(gain[k:k + b]))  # meilleur instant dans la seconde qui suit
    # attaque : plus forte montée des basses d'une tranche à la suivante, à ±0.3 s
    j0, r = i[k], int(0.3 / HOP)
    lo, hi = max(1, j0 - r), min(len(bass) - 1, j0 + r)
    d = np.diff(bass)[lo - 1:hi]
    return round((lo + int(np.argmax(d))) * HOP, 2)


VERSION = 2  # version de find_drop : les valeurs détectées par une ancienne version sont recalculées


def _key(path):
    st = os.stat(path)
    return f"{os.path.basename(path)}|{st.st_size}|{int(st.st_mtime)}"


def _cache():
    try:
        return json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(cache):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def info(path):
    """{auto: drop détecté, manual: drop corrigé à la main ou None, drop: celui utilisé} (mis en cache, clé : nom +
    taille + date du fichier)."""
    cache, key = _cache(), _key(path)
    e = cache.get(key)
    if not isinstance(e, dict):
        e = {}
    if e.get("v") != VERSION:
        e.update(auto=find_drop(path), v=VERSION)
        cache[key] = e
        _save(cache)
    return {"auto": e["auto"], "manual": e.get("manual"), "drop": e["auto"] if e.get("manual") is None else e["manual"]}


def drop(path):
    return info(path)["drop"]


def set_manual(path, t):
    """Drop corrigé à la main (t en s), ou None pour revenir au drop détecté."""
    info(path)
    cache = _cache()
    cache[_key(path)]["manual"] = None if t is None else round(float(t), 2)
    _save(cache)
    return info(path)


def add(name, data):
    """Enregistre un morceau envoyé depuis l'interface dans music/ -> nom du fichier."""
    name = os.path.basename(name or "")
    if not name.lower().endswith(EXTS):
        raise ValueError("format audio non pris en charge (" + ", ".join(EXTS) + ")")
    os.makedirs(FOLDERS[0], exist_ok=True)
    path = os.path.join(FOLDERS[0], name)
    if not os.path.exists(path) or os.path.getsize(path) != len(data):
        open(path, "wb").write(data)
    return name
