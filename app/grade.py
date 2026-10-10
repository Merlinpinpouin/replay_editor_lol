"""Génère un LUT 3D (.cube) qui transfère la colorimétrie d'images de référence vers des images du jeu brut.
Méthode : Iterative Distribution Transfer (Pitié et al.) — une suite de remappages 1D d'histogrammes selon
des axes aléatoires. Gère les changements de teinte (herbe verte -> ocre) contrairement à des courbes RGB.
Usage : python -m app.grade <dossier_ref> <dossier_raw> <sortie.cube>"""
import glob, os, sys
import numpy as np
from PIL import Image

N = 33  # taille du LUT


def load(folder, n=200000, seed=0):
    px = []
    for f in sorted(glob.glob(os.path.join(folder, "*.png")) + glob.glob(os.path.join(folder, "*.jpg"))):
        a = np.asarray(Image.open(f).convert("RGB"), dtype=np.float32) / 255.0
        px.append(a.reshape(-1, 3))
    px = np.concatenate(px)
    rng = np.random.default_rng(seed)
    return px[rng.choice(len(px), min(n, len(px)), replace=False)]


def _rotations(k, rng):
    out = []
    for _ in range(k):
        q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        out.append(q)
    return out


def fit(src, ref, iters=30, bins=300, seed=1):
    """Retourne une liste de (rotation, [(xs, ys) par axe]) applicable à n'importe quels pixels."""
    rng = np.random.default_rng(seed)
    steps, cur = [], src.copy()
    qs = np.linspace(0, 1, bins)
    for R in _rotations(iters, rng):
        a, b = cur @ R, ref @ R
        maps = []
        for d in range(3):
            xs = np.quantile(a[:, d], qs)
            ys = np.quantile(b[:, d], qs)
            xs = np.maximum.accumulate(xs + np.arange(bins) * 1e-7)
            maps.append((xs, ys))
            a[:, d] = np.interp(a[:, d], xs, ys)
        cur = a @ R.T
        steps.append((R, maps))
    return steps


def apply(steps, px, relax=1.0):
    cur = px.copy()
    for R, maps in steps:
        a = cur @ R
        for d, (xs, ys) in enumerate(maps):
            a[:, d] = np.interp(a[:, d], xs, ys)
        cur = a @ R.T
    out = px + relax * (cur - px)
    return np.clip(out, 0, 1)


def _sqrtm(m):
    w, v = np.linalg.eigh(m)
    return (v * np.sqrt(np.maximum(w, 1e-10))) @ v.T


def fit_mkl(src, ref):
    """Transfert linéaire Monge-Kantorovich : x -> mr + T (x - ms), T = Cs^-1/2 (Cs^1/2 Cr Cs^1/2)^1/2 Cs^-1/2."""
    ms, mr = src.mean(0), ref.mean(0)
    cs, cr = np.cov(src.T), np.cov(ref.T)
    a = _sqrtm(cs)
    ai = np.linalg.inv(a)
    t = ai @ _sqrtm(a @ cr @ a) @ ai
    return ms, mr, t


def lum(px):
    return px @ np.array([0.2126, 0.7152, 0.0722], np.float32)


def fit_curve(src, ref, bins=64):
    """Courbe de luminance (match d'histogramme) lissée et monotone."""
    qs = np.linspace(0.005, 0.995, bins)
    xs, ys = np.quantile(lum(src), qs), np.quantile(lum(ref), qs)
    ys = np.convolve(np.pad(ys, 3, mode="edge"), np.ones(7) / 7, "valid")
    xs = np.concatenate([[0], xs, [1]]); ys = np.maximum.accumulate(np.concatenate([[0], ys, [1]]))
    return xs, ys


def rgb2hsv(c):
    mx, mn = c.max(1), c.min(1); d = mx - mn + 1e-8
    r, g, b = c.T
    h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
    s = np.where(mx > 0, (mx - mn) / (mx + 1e-8), 0)
    return np.stack([h, s, mx], 1)


def hsv2rgb(hsv):
    h, s, v = hsv.T
    c = v * s; x = c * (1 - np.abs((h / 60) % 2 - 1)); m = v - c
    k = (h // 60).astype(int) % 6
    z = np.zeros_like(h)
    r = np.choose(k, [c, x, z, z, x, c]); g = np.choose(k, [x, c, c, x, z, z]); b = np.choose(k, [z, z, x, c, c, x])
    return np.stack([r + m, g + m, b + m], 1)


def tame_greens(px, center=105, width=55, target=38, amount=0.85, desat=0.45):
    """Herbe verte -> ocre poussiéreux (la Faille de la référence n'a quasiment pas de vert)."""
    hsv = rgb2hsv(px)
    w = np.clip(1 - np.abs(hsv[:, 0] - center) / width, 0, 1) ** 0.7 * np.clip(hsv[:, 1] * 4, 0, 1)
    hsv[:, 0] = hsv[:, 0] + amount * w * (target - hsv[:, 0])
    hsv[:, 1] = hsv[:, 1] * (1 - desat * w)
    return hsv2rgb(hsv)


def build_mkl(ref_dir, raw_dir, out, strength=1.0, curve=0.6, greens=True):
    ref, raw = load(ref_dir), load(raw_dir)
    if greens:
        raw = tame_greens(raw)
    ms, mr, t = fit_mkl(raw, ref)
    g = grid()
    c = mr + ((tame_greens(g) if greens else g) - ms) @ t.T
    mid = np.clip(c, 0, 1)
    xs, ys = fit_curve(np.clip(mr + (raw - ms) @ t.T, 0, 1), ref)
    l0 = lum(mid); l1 = np.interp(l0, xs, ys)
    l1 = l0 + curve * (l1 - l0)
    graded = np.clip(mid * ((l1 + 1e-4) / (l0 + 1e-4))[:, None], 0, 1)
    lut = g + strength * (graded - g)
    write_cube(out, smooth(lut))
    return out


def grid():
    r = np.linspace(0, 1, N)
    b, g, rr = np.meshgrid(r, r, r, indexing="ij")  # .cube : R varie le plus vite
    return np.stack([rr, g, b], -1).reshape(-1, 3)


def smooth(lut, k=1):
    """Lissage 3D léger (moyenne 3x3x3) pour éviter les cassures de dégradé."""
    v = lut.reshape(N, N, N, 3)
    p = np.pad(v, ((k, k), (k, k), (k, k), (0, 0)), mode="edge")
    acc = np.zeros_like(v)
    for i in range(2 * k + 1):
        for j in range(2 * k + 1):
            for l in range(2 * k + 1):
                acc += p[i:i + N, j:j + N, l:l + N]
    return (acc / (2 * k + 1) ** 3).reshape(-1, 3)


def write_cube(path, lut, title="lolclip"):
    with open(path, "w") as f:
        f.write(f'TITLE "{title}"\nLUT_3D_SIZE {N}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
        for r, g, b in lut:
            f.write(f"{r:.6f} {g:.6f} {b:.6f}\n")


def build(ref_dir, raw_dir, out, relax=1.0):
    ref, raw = load(ref_dir), load(raw_dir)
    steps = fit(raw, ref)
    lut = smooth(apply(steps, grid(), relax))
    write_cube(out, lut)
    return out



# --- Look "teal & orange" (référence infos/) : remap de teintes paramétrique + courbe/saturation ajustées sur les données.
# Le transfert statistique pur échoue ici : les captures brutes (combats, effets violets) n'ont pas le même contenu que
# la référence (terrain). On fixe donc la direction des teintes à la main et on n'ajuste que tonalité et saturation.
HUE_SHIFTS = [  # (centre, demi-largeur, cible, force, x saturation, x valeur) — degrés
    (112, 70, 27, 0.90, 0.85, 1.00),   # herbe (y compris l herbe sombre vert-bleu) / verts -> terre ocre-orangée (chaude)
    (55, 22, 28, 0.60, 0.90, 1.00),    # jaunes -> orange chaud
    (205, 45, 188, 0.75, 1.10, 1.25),  # bleus / eau sombre -> sarcelle lumineuse
]
# ombres à peine sarcelle, tons moyens et hautes lumières chauds (la carte doit paraître chaude)
SPLIT = {"shadow": (-0.010, 0.000, 0.012), "mid": (0.030, 0.008, -0.030), "high": (0.040, 0.015, -0.035)}


def hue_shift(px):
    hsv = rgb2hsv(px)
    h, s = hsv[:, 0].copy(), hsv[:, 1]
    sw = np.clip(s * 5, 0, 1)  # les gris restent neutres
    dh, ks, kv = np.zeros_like(h), np.ones_like(h), np.ones_like(h)
    for c, wd, tgt, k, ms, mv in HUE_SHIFTS:
        dist = (h - c + 180) % 360 - 180
        w = np.clip(1 - np.abs(dist) / wd, 0, 1)
        w = w * w * (3 - 2 * w) * sw
        dh += k * w * ((tgt - h + 180) % 360 - 180)
        ks *= 1 + w * (ms - 1)
        kv *= 1 + w * (mv - 1)
    hsv[:, 0] = (h + dh) % 360
    hsv[:, 1] = np.clip(s * ks, 0, 1)
    hsv[:, 2] = np.clip(hsv[:, 2] * kv, 0, 1)
    out = hsv2rgb(hsv)
    l = lum(out)[:, None]
    ws = np.clip(1 - l / 0.35, 0, 1) ** 1.5
    wh = np.clip((l - 0.45) / 0.45, 0, 1)
    wm = np.clip(1 - np.abs(l - 0.35) / 0.25, 0, 1)
    return np.clip(out + ws * np.array(SPLIT["shadow"]) + wm * np.array(SPLIT["mid"])
                   + wh * np.array(SPLIT["high"]), 0, 1)


def sat_scale(px, gain, rolloff=0.85):
    """Saturation x gain, avec épaule douce pour ne pas brûler les couleurs déjà saturées (effets)."""
    hsv = rgb2hsv(px)
    s = hsv[:, 1] * gain
    hsv[:, 1] = np.where(s < rolloff, s, rolloff + (1 - rolloff) * np.tanh((s - rolloff) / (1 - rolloff)))
    return hsv2rgb(hsv)


def map_weight(px):
    """Poids du look par couleur : 1 pour les tons du décor (ternes / moyennement saturés, pas trop clairs),
    0 pour les couleurs vives ou lumineuses (champions, sorts, effets) qui gardent leurs couleurs d'origine,
    comme dans la référence où seule la carte change de teinte."""
    hsv = rgb2hsv(px)
    s, v = hsv[:, 1], hsv[:, 2]

    def ss(a, b, x):
        t = np.clip((x - a) / (b - a), 0, 1)
        return t * t * (3 - 2 * t)
    base = (1 - ss(0.50, 0.80, s)) * (1 - ss(0.62, 0.92, v))
    # l'herbe peut être assez saturée, mais champions et sorts sont rarement vert herbe -> les verts suivent la carte
    d = np.abs((hsv[:, 0] - 100 + 180) % 360 - 180)
    green = (1 - ss(35, 60, d)) * (1 - ss(0.80, 0.95, v)) * (1 - ss(0.85, 1.0, s))
    return np.maximum(base, green)


def build_look(ref_dir, raw_dir, out, strength=1.0, sat_gain=1.08):
    ref, raw = load(ref_dir), load(raw_dir)
    a = hue_shift(raw)
    # tonalité : correspondance des quantiles de luminance (lissée, monotone, extrémités fixes)
    xs, ys = fit_curve(a, ref, bins=32)
    g = grid()
    c = hue_shift(g)
    lg = lum(c)
    c = np.clip(c * ((np.interp(lg, xs, ys) + 1e-4) / (lg + 1e-4))[:, None], 0, 1)
    c = sat_scale(c, sat_gain)
    lut = g + strength * map_weight(g)[:, None] * (np.clip(c, 0, 1) - g)
    write_cube(out, smooth(lut))
    return {"sat_gain": sat_gain}

if __name__ == "__main__":
    if sys.argv[1] == "look":
        print(build_look(sys.argv[2], sys.argv[3], sys.argv[4]))
        sys.exit()
    if sys.argv[1] == "mkl":
        build_mkl(sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5]) if len(sys.argv) > 5 else 1.0)
        sys.exit()
    build(sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]) if len(sys.argv) > 4 else 1.0)
