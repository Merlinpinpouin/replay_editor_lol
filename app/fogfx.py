"""Transition « traversée du brouillard » entre deux actions (clips combinés), ajoutée au montage.

La caméra avance dans un nuage de fumée sombre (façon brouillard de guerre) qui se referme sur la fin de l'action A ;
écran entièrement couvert -> coupe franche vers l'action B ; le nuage continue de défiler vers la caméra et se déchire
pour révéler B. Deux couches de fumée qui grossissent à des vitesses différentes (parallaxe) donnent l'impression
d'avancer à travers le brouillard. Le nuage est calculé ici (numpy + PIL) en vidéo RGBA à mi-résolution, puis posé
par ffmpeg sur le montage (montage.assemble)."""
import os, subprocess
import numpy as np
from PIL import Image
from .paths import CACHE

FPS = 60
VERSION = 1  # à augmenter si le rendu du nuage change (fichiers en cache dans <données>/cache/fog)
TEX = os.path.join(CACHE, "fog_cloud.png")
T_IN, T_OUT = 0.6, 0.85  # durée de l'entrée dans le nuage (fin de A) et de la sortie (début de B), en s
HOLD = 0.06  # écran entièrement couvert autour de la coupe
DARK = np.array([38, 44, 54], np.float32)  # creux de la fumée (gris bleuté sombre)
LIGHT = np.array([118, 126, 138], np.float32)  # volutes éclairées


def _noise(n, seed, octaves=6):
    """Bruit fractal n x n (somme d'octaves de bruit lissé), 0..1."""
    r = np.random.default_rng(seed)
    out = np.zeros((n, n), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        g = 4 * 2 ** o
        layer = Image.fromarray((r.random((g, g)) * 255).astype(np.uint8)).resize((n, n), Image.BICUBIC)
        out += amp * np.asarray(layer, np.float32) / 255
        tot += amp
        amp *= 0.55
    out /= tot
    out -= out.min()
    return out / out.max()


def texture():
    """Texture de fumée (2 couches dans les canaux R et G), créée une fois dans le cache."""
    if not os.path.exists(TEX):
        os.makedirs(os.path.dirname(TEX), exist_ok=True)
        a, b = _noise(1536, 7), _noise(1536, 11)
        img = np.stack([a, b, np.zeros_like(a)], -1)
        Image.fromarray((img * 255).astype(np.uint8)).save(TEX)
    t = np.asarray(Image.open(TEX).convert("RGB"), np.float32) / 255
    return t[..., 0], t[..., 1]


def _zoomed(tex, z, w, h, drift):
    """Centre de la texture agrandi z fois, recadré au ratio w/h (la caméra avance dans le nuage)."""
    n = tex.shape[0]
    cw = n / z
    ch = cw * h / w
    if ch > n / z:
        ch = n / z
        cw = ch * w / h
    cx, cy = n / 2 + drift[0] * n, n / 2 + drift[1] * n
    box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
    im = Image.fromarray((tex * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR, box=box)
    return np.asarray(im, np.float32) / 255


def _smooth(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def render(out, w, h, t_in=T_IN, t_out=T_OUT, strength=1.0):
    """Vidéo RGBA (ffv1, w/2 x h/2) du nuage : t_in s d'entrée puis t_out s de sortie (coupe à t_in).
    strength (0..1) : densité de la fumée en dehors du moment où elle couvre tout l'écran."""
    a_tex, b_tex = texture()
    fw, fh = (w // 2) // 2 * 2, (h // 2) // 2 * 2
    n = int(round((t_in + t_out) * FPS))
    yy, xx = np.mgrid[0:fh, 0:fw].astype(np.float32)
    # les bords se couvrent avant le centre (on entre dans le nuage) et se découvrent après
    rad = np.hypot((xx - fw / 2) / (fw / 2), (yy - fh / 2) / (fh / 2)) / np.sqrt(2)
    ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{fw}x{fh}",
                           "-r", str(FPS), "-i", "-", "-c:v", "ffv1", "-pix_fmt", "bgra", out], stdin=subprocess.PIPE)
    try:
        for i in range(n):
            t = i / FPS
            # densité : 0 -> 1 (couvert) à la coupe -> 0 ; montée en accélérant, sortie qui ralentit
            if t < t_in - HOLD / 2:
                d = (t / (t_in - HOLD / 2)) ** 1.4
            elif t < t_in + HOLD / 2:
                d = 1.0
            else:
                d = 1 - _smooth((t - t_in - HOLD / 2) / (t_out - HOLD / 2)) ** 0.7
            # avancée continue dans le nuage : zoom exponentiel, couche proche plus rapide (parallaxe)
            u = t / (t_in + t_out)
            near = _zoomed(a_tex, 1.0 * 3.2 ** u, fw, fh, (0.02 * u, -0.015 * u))
            far = _zoomed(b_tex, 1.4 * 1.8 ** u, fw, fh, (-0.03 * u, 0.01 * u))
            smoke = 0.6 * near + 0.4 * far
            # seuil qui descend avec la densité : volutes d'abord, puis tout l'écran
            thr = 1.0 - 1.12 * d + 0.25 * (1 - rad) * (1 - d)
            alpha = _smooth((smoke - thr) / 0.22) * (strength + (1 - strength) * d ** 3)
            if d >= 0.999:
                alpha[:] = 1.0
            shade = _smooth(0.25 + 0.9 * (far - 0.35))[..., None]
            rgb = DARK + (LIGHT - DARK) * shade
            rgb *= (1 - 0.35 * rad ** 2)[..., None]  # bords plus sombres
            frame = np.dstack([rgb, alpha[..., None] * 255]).clip(0, 255).astype(np.uint8)
            ff.stdin.write(frame.tobytes())
    finally:
        ff.stdin.close()
        ff.wait()
    return out
