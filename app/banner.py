"""Encadré de présentation du joueur au début de chaque action (comme infos/ssstik.io_1791565461695.mp4) :
région, emblème du rang dans un hexagone, puis une plaque sombre à bord doré qui s'étire vers la droite avec le pseudo
et l'image du champion. Il apparaît en ~0.7 s, reste, puis s'efface.
Rendu ici (PIL + numpy) en vidéo RGBA (ffv1) de la hauteur du bandeau, posée par ffmpeg (montage.assemble).
Images : splash « centered » du champion (Data Dragon) et emblème du rang (CommunityDragon), téléchargés une fois
dans <données>/cache/images."""
import math, os, subprocess, urllib.request, zlib
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from .paths import CACHE as DATA_CACHE

FPS = 60
VERSION = 1  # à augmenter si le dessin change (rendus en cache dans <données>/cache/banners)
CACHE = os.path.join(DATA_CACHE, "images")
OUT_CACHE = os.path.join(DATA_CACHE, "banners")
SPLASH = "https://ddragon.leagueoflegends.com/cdn/img/champion/centered/{}_0.jpg"
EMBLEM = ("https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/ranked-emblem/"
          "emblem-{}.png")
GOLD, GOLD_LIGHT, PLATE = (200, 170, 110), (240, 230, 210), (10, 12, 14)
FONT = r"C:\Windows\Fonts\seguisb.ttf"
FONT_BOLD = r"C:\Windows\Fonts\seguibl.ttf"
FONT_CJK = r"C:\Windows\Fonts\malgunbd.ttf"
DELAY = 0.35  # apparition après le début de l'action (après la sortie du brouillard pour les actions suivantes)
LENGTH = 5.5  # durée d'affichage max (s)
OUT = 0.35  # disparition (s)


def _get(url, name):
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, name)
    if not os.path.exists(f):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 lolclip"})  # sinon 403
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
            open(f, "wb").write(data)
        except Exception:
            return None
    try:
        return Image.open(f)
    except Exception:
        return None


def splash(champion):
    im = _get(SPLASH.format(champion), f"splash_{champion}.jpg")
    return im.convert("RGB") if im else None


def emblem(tier):
    if not tier:
        return None
    im = _get(EMBLEM.format(tier.lower()), f"emblem_{tier.lower()}.png")
    if not im:
        return None
    im = im.convert("RGBA")
    return im.crop(im.getbbox())


def _font(path, size, text):
    if any(ord(c) > 0x2E80 for c in text):  # coréen, chinois, japonais
        path = FONT_CJK
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _smooth(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def layout(w, h):
    """Unité (px du rendu pour 1 px de la référence 720x720) et position verticale du bandeau."""
    u = min(w, h) / 720
    band_h = int(round(100 * u)) // 2 * 2
    y = h - int(round(118 * u)) if h <= w * 1.2 else int(h * 0.70)  # vertical : au-dessus de l'interface TikTok
    return u, band_h, y


def _layers(w, u, band_h, name, champion, tier, region):
    """Calques fixes (RGBA, w x band_h) : région, hexagone + emblème, plaque (+ image, bords), pseudo."""
    S = lambda v: int(round(v * u))
    cy = band_h // 2
    L = {k: Image.new("RGBA", (w, band_h), (0, 0, 0, 0)) for k in ("region", "hex", "plate", "name")}
    # région
    d = ImageDraw.Draw(L["region"])
    f = _font(FONT_BOLD, S(13), region)
    d.text((S(22), cy), region, font=f, fill=(255, 255, 255, 235), anchor="lm")
    # plaque : pentagone à encoche à gauche (la pointe de l'hexagone vient s'y loger)
    x0, x1 = S(150), min(w - S(8), S(150) + S(560))
    ph = S(78)
    top, bot = cy - ph // 2, cy + ph // 2
    notch = S(26)
    poly = [(x0, top), (x1, top), (x1, bot), (x0, bot), (x0 + notch, cy)]
    plate = Image.new("RGBA", (w, band_h), (0, 0, 0, 0))
    ImageDraw.Draw(plate).polygon(poly, fill=PLATE + (235,))
    mask = Image.new("L", (w, band_h), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    art = splash(champion)
    if art:
        aw = int((x1 - x0) * 0.55)
        # visage : le champion est au centre du splash « centered », plutôt dans le haut
        sw, sh = art.size
        crop_w = min(sw, sh * 0.62 * aw / ph)
        crop_h = crop_w * ph / aw
        cx, cy_art = sw * 0.5, sh * 0.36
        top_art = min(max(0.0, cy_art - crop_h / 2), sh - crop_h)
        box = (cx - crop_w / 2, top_art, cx + crop_w / 2, top_art + crop_h)
        a = art.resize((aw, ph), Image.LANCZOS, box=box).convert("RGBA")
        g = np.linspace(0, 1, aw)
        fade = np.clip(g / 0.45, 0, 1) ** 1.5 * np.clip((1 - g) / 0.08, 0, 1)
        a.putalpha(Image.fromarray((np.tile(fade, (ph, 1)) * 230).astype(np.uint8)))
        art_l = Image.new("RGBA", (w, band_h), (0, 0, 0, 0))
        art_l.paste(a, (x1 - aw, top), a)
        art_l.putalpha(Image.fromarray(np.minimum(np.asarray(art_l.getchannel("A")), np.asarray(mask))))
        plate = Image.alpha_composite(plate, art_l)
    # léger dégradé sombre sous le texte
    d = ImageDraw.Draw(plate)
    lw = max(1, S(1.3))
    d.line(poly + [poly[0]], fill=GOLD + (200,), width=lw)
    # filets dorés décalés (dessus et dessous), comme la référence
    o = S(6)
    d.line([(x0 + S(30), top - o), (x1 - S(150), top - o)], fill=GOLD + (150,), width=lw)
    d.line([(x0 + S(30), top - o), (x0 + S(14), top - o + S(8))], fill=GOLD + (150,), width=lw)
    d.line([(x1 - S(260), bot + o), (x1 - S(10), bot + o)], fill=GOLD + (150,), width=lw)
    L["plate"] = plate
    # hexagone allongé + emblème
    hx0, hx1, hh = S(54), S(150) + notch - S(4), S(40)
    hex_poly = [(hx0, cy), (hx0 + S(14), cy - hh // 2), (hx1 - S(14), cy - hh // 2), (hx1, cy),
                (hx1 - S(14), cy + hh // 2), (hx0 + S(14), cy + hh // 2)]
    d = ImageDraw.Draw(L["hex"])
    d.polygon(hex_poly, fill=PLATE + (240,))
    d.line(hex_poly + [hex_poly[0]], fill=GOLD + (220,), width=lw)
    inner = [(x + (S(5) if x < (hx0 + hx1) / 2 else -S(5)), y) for x, y in hex_poly]
    d.line(inner + [inner[0]], fill=GOLD + (90,), width=max(1, lw // 2))
    d.polygon([(hx1, cy - S(4)), (hx1 + S(4), cy), (hx1, cy + S(4)), (hx1 - S(4), cy)], fill=(80, 200, 220, 230))
    em = emblem(tier)
    if em:
        eh = S(30)
        ew = int(em.width * eh / em.height)
        if ew > hx1 - hx0 - S(36):
            ew = hx1 - hx0 - S(36)
            eh = int(em.height * ew / em.width)
        e = em.resize((ew, eh), Image.LANCZOS)
        L["hex"].alpha_composite(e, ((hx0 + hx1 - ew) // 2, cy - eh // 2))
    # pseudo : dégradé doré clair -> doré, ombre portée
    f = _font(FONT, S(30), name)
    tx, ty = x0 + S(52), cy
    txt = Image.new("L", (w, band_h), 0)
    ImageDraw.Draw(txt).text((tx, ty), name, font=f, fill=255, anchor="lm")
    grad = np.linspace(0, 1, band_h)[:, None]
    col = np.array(GOLD_LIGHT)[None, None] * (1 - grad[..., None] * 0.9) + np.array(GOLD)[None, None] * grad[..., None] * 0.9
    col = np.broadcast_to(col, (band_h, w, 3))
    a = np.asarray(txt, np.float32)
    shadow = np.asarray(txt.filter(ImageFilter.GaussianBlur(S(3))), np.float32)
    rgba = np.zeros((band_h, w, 4), np.float32)
    rgba[..., :3] = col
    rgba[..., 3] = a
    sh = np.zeros_like(rgba)
    sh[..., 3] = shadow * 0.8
    base = Image.alpha_composite(Image.fromarray(sh.astype(np.uint8)), Image.fromarray(rgba.astype(np.uint8)))
    L["name"] = base
    geo = {"x0": x0, "x1": x1, "top": top, "bot": bot, "hx0": hx0, "hx1": hx1, "cy": cy}
    return L, geo


def render(out, w, h, name, champion, tier=None, region="", duration=LENGTH):
    """Vidéo RGBA (ffv1, w x hauteur du bandeau) de l'encadré, durée <duration> s (apparition, maintien,
    disparition). -> (fichier, y du bandeau dans l'image)."""
    u, band_h, y = layout(w, h)
    L, g = _layers(w, u, band_h, name, champion, tier, region)
    arr = {k: np.asarray(v, np.float32) for k, v in L.items()}
    n = int(round(duration * FPS))
    rng = np.random.default_rng(zlib.crc32(name.encode()))
    sparks = [(rng.uniform(0, 1), rng.uniform(0.1, 0.9), rng.uniform(0.6, 1.6), rng.uniform(0, 6.28))
              for _ in range(9)]
    # étincelle : petit point flou (pas un carré), et jamais sur le pseudo
    r = max(2, int(round(2.5 * u)))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    dot = np.exp(-(xx ** 2 + yy ** 2) / (2 * (0.45 * r) ** 2)).astype(np.float32)
    name_box = np.asarray(L["name"].getchannel("A")).nonzero()
    nx0, nx1 = (name_box[1].min() - 10 * u, name_box[1].max() + 10 * u) if len(name_box[1]) else (0, -1)
    xs = np.arange(w)[None, :]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{band_h}",
                           "-r", str(FPS), "-i", "-", "-c:v", "ffv1", "-pix_fmt", "bgra", out], stdin=subprocess.PIPE)
    try:
        for i in range(n):
            t = i / FPS
            fade_out = 1 - _smooth((t - (duration - OUT)) / OUT)
            shift = int(round(-30 * u * (1 - fade_out)))  # glisse un peu vers la gauche en disparaissant
            a_reg = _smooth(t / 0.15)
            a_hex = _smooth((t - 0.08) / 0.18)
            wipe = _smooth((t - 0.2) / 0.38)  # la plaque s'étire vers la droite
            a_name = _smooth((t - 0.45) / 0.3)
            frame = np.zeros((band_h, w, 4), np.float32)

            def over(layer, alpha, dx=0, clip=None):
                if alpha <= 0:
                    return
                src = np.roll(layer, dx, axis=1) if dx else layer
                a = src[..., 3:4] / 255 * alpha
                if clip is not None:
                    a = a * clip[..., None]
                frame[..., :3] = src[..., :3] * a + frame[..., :3] * (1 - a)
                frame[..., 3:4] = a * 255 + frame[..., 3:4] * (1 - a)

            over(arr["region"], a_reg * fade_out, shift)
            if wipe > 0:
                edge = g["x0"] + (g["x1"] - g["x0"]) * wipe
                clip = np.broadcast_to(np.clip((edge - xs) / (6 * u), 0, 1), (band_h, w)).astype(np.float32)
                over(arr["plate"], fade_out, shift, np.roll(clip, shift, axis=1) if shift else clip)
                # étincelles qui dérivent dans la plaque
                for sx, sy, sp, ph in sparks:
                    px = g["x0"] + 40 * u + ((sx + 0.02 * sp * t) % 1) * (g["x1"] - g["x0"] - 80 * u)
                    if px > edge:
                        continue
                    py = g["top"] + sy * (g["bot"] - g["top"]) - 6 * u * sp * t
                    if nx0 <= px <= nx1:
                        continue
                    b = (0.35 + 0.35 * math.sin(ph + 4 * t)) * fade_out * wipe
                    xi, yi = int(px) + shift, int(py)
                    if g["top"] + r < yi < g["bot"] - r and r <= xi < w - r:
                        sl = (slice(yi - r, yi + r + 1), slice(xi - r, xi + r + 1))
                        a = dot * b
                        frame[sl][..., :3] = np.array((255, 240, 205)) * a[..., None] + frame[sl][..., :3] * (1 - a[..., None])
                        frame[sl][..., 3] = np.maximum(frame[sl][..., 3], 255 * a)
            # hexagone : arrive de la gauche en grossissant légèrement
            over(arr["hex"], a_hex * fade_out, shift + int(round(-18 * u * (1 - a_hex))))
            over(arr["name"], a_name * fade_out, shift + int(round(-14 * u * (1 - a_name))))
            ff.stdin.write(frame.clip(0, 255).astype(np.uint8).tobytes())
    finally:
        ff.stdin.close()
        ff.wait()
    return out, y


def cached(w, h, name, champion, tier, region, duration):
    """Rendu mis en cache (même joueur / rang / format / durée) -> (fichier, y)."""
    duration = round(min(LENGTH, max(1.5, duration)) * 2) / 2
    key = "".join(c for c in f"{name}_{champion}_{tier}_{region}" if c.isalnum() or c == "_")
    f = os.path.join(OUT_CACHE, f"b{VERSION}_{key}_{zlib.crc32(name.encode()) % 10 ** 6}_{w}x{h}_{duration:.1f}.mkv")
    if not os.path.exists(f):
        return render(f, w, h, name, champion, tier, region, duration)
    return f, layout(w, h)[2]
