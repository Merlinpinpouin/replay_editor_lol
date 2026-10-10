"""Montage final en UNE commande ffmpeg, à partir des captures temps réel (capture.record_live).

Tout est calé sur les horodatages des captures (et non sur des numéros d'image) : ddagrab laisse parfois un trou de
33-50 ms entre deux images ; un lecteur le gère, mais compter les images à 1/60 s faisait accélérer la vidéo.
Chaîne par segment : découpe au temps -> 60 i/s constants -> recadrage au format -> étalonnage (LUT, en 16 bits)
-> vignette -> un seul redimensionnement -> bandes noires. Son : celui du jeu (+ musique en option), normalisé."""
import json, os, subprocess
from . import fogfx
from .paths import ROOT

ASSETS = os.path.join(ROOT, "assets")
LUT = os.path.join(ASSETS, "reference.cube")
VIGNETTE = os.path.join(ASSETS, "vignette_1440.png")
FPS = 60
# 1440p : même ratio que la référence (1920x940 + 70 px de bandes), en plus grand
OUT_W, OUT_H, BAR = 2560, 1252, 94
# formats de sortie : (largeur, hauteur de l'image, bandes noires en haut/bas). L'image est recadrée au centre de la
# capture (écran 3440x1440) : le carré et le vertical gardent la pleine hauteur de 1440 px.
FORMATS = {
    "cinema": (OUT_W, OUT_H, BAR),  # 2560x1440 avec bandes noires (style des premières références)
    "wide": (2560, 1440, 0),        # 16:9 plein écran
    "square": (1440, 1440, 0),      # 1:1, comme la vidéo infos/ssstik.io_*.mp4
    "vertical": (1080, 1920, 0),    # 9:16 (TikTok / Shorts)
}
LOUDNESS = -23  # volume visé (LUFS) : -16 = niveau réseaux sociaux, jugé trop fort ; -23 = norme TV
FLASH = 0.15  # flash blanc (s) au début de chaque segment après le premier
MUSIC_VOL, GAME_VOL = 0.55, 0.8  # mélange musique / son du jeu (avant normalisation du volume)


def _esc(p):
    return p.replace("\\", "/").replace(":", "\\:")


def assemble(raws, out, grade=True, vignette=True, flash=True, loudness=None, fmt="cinema", fogs=None, music=None,
             fog_strength=1.0, banners=()):
    """raws : captures (.mkv avec leur .json) dans l'ordre du montage -> MP4 H.264.
    fogs : indices des captures qui commencent une nouvelle action (clips combinés) : traversée du brouillard
    (fogfx) à la coupe, pas de flash, son estompé de part et d'autre.
    music : (fichier, instant du morceau au début de la vidéo) ; instant négatif = la musique démarre plus tard.
    banners : encadrés de présentation [(vidéo RGBA, instant d'apparition, y)] (banner.py), posés par-dessus tout."""
    OUT_W, OUT_H, BAR = FORMATS.get(fmt, FORMATS["cinema"])
    fogs = set(fogs or ())
    metas = [json.load(open(r + ".json")) for r in raws]
    cmd, fc, labels = ["ffmpeg", "-y", "-v", "error"], [], []
    n_in = 0
    for k, (raw, m) in enumerate(zip(raws, metas)):
        dur = m["end"] - m["start"]
        cmd += ["-ss", f"{m['ss']:.4f}", "-t", f"{dur:.4f}", "-i", raw]
        vi = n_in
        n_in += 1
        # images ratées par le jeu (ciel, image vide) retirées : fps les remplace par l'image précédente
        bad = [g - m["ss"] for g in m.get("glitches", []) if -0.02 <= g - m["ss"] <= dur + 0.02]
        expr = "+".join(f"lt(abs(t-{b:.4f}),0.006)" for b in bad)
        chain = ([f"select='not({expr})'"] if bad else []) + [f"fps={FPS}", f"crop='min(iw,ih*{OUT_W}/{OUT_H})':'min(ih,iw*{OUT_H}/{OUT_W})'"]
        if grade and os.path.exists(LUT):
            chain.append(f"format=gbrp16le,lut3d=file='{_esc(LUT)}':interp=tetrahedral")
        # même format de pixels pour toutes les parties : concat ne convertit pas (sinon couleurs fausses)
        chain.append(f"scale={OUT_W}:{OUT_H}:flags=lanczos,format=yuv444p10le")
        if flash and k > 0 and k not in fogs:
            chain.append(f"fade=t=in:st=0:d={FLASH}:color=white")
        fc.append(f"[{vi}:v]{','.join(chain)}[v{k}]")
        if m.get("audio") and os.path.exists(m["audio"]):
            cmd += ["-ss", f"{max(0.0, m['audio_start']):.4f}", "-t", f"{dur:.4f}", "-i", m["audio"]]
            fade = f",afade=t=in:d={fogfx.T_OUT:.3f}" if k in fogs else ""
            if k + 1 in fogs:
                fade += f",afade=t=out:st={max(0.0, dur - fogfx.T_IN):.4f}:d={fogfx.T_IN:.3f}"
            fc.append(f"[{n_in}:a]aresample=48000,apad=whole_dur={dur:.4f}{fade}[a{k}]")
            n_in += 1
        else:
            fc.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={dur:.4f}[a{k}]")
        labels.append(f"[v{k}][a{k}]")
    fc.append(f"{''.join(labels)}concat=n={len(raws)}:v=1:a=1[vc][ac]")
    last = "vc"
    if fogs:
        # nuage calculé une fois par format, posé à chaque coupe entre deux actions
        os.makedirs(os.path.join(ASSETS, "fog_cache"), exist_ok=True)
        fog = os.path.join(ASSETS, "fog_cache", f"fog_{fogfx.VERSION}_{OUT_W}x{OUT_H}_{fog_strength:.2f}.mkv")
        if not os.path.exists(fog):
            fogfx.render(fog, OUT_W, OUT_H, strength=fog_strength)
        cuts = [sum(m["end"] - m["start"] for m in metas[:k]) for k in sorted(fogs)]
        for j, c in enumerate(cuts):
            cmd += ["-i", fog]
            fc.append(f"[{n_in}:v]scale={OUT_W}:{OUT_H}:flags=bicubic,format=yuva444p10le,"
                      f"setpts=PTS-STARTPTS+{max(0.0, c - fogfx.T_IN):.4f}/TB[fg{j}]")
            fc.append(f"[{last}][fg{j}]overlay=eof_action=pass:format=yuv444p10[vf{j}]")
            n_in += 1
            last = f"vf{j}"
    if vignette and os.path.exists(VIGNETTE):
        cmd += ["-loop", "1", "-i", VIGNETTE]
        fc.append(f"[{n_in}:v]scale={OUT_W}:{OUT_H}[vig];[{last}][vig]overlay=shortest=1[vv]")
        n_in += 1
        last = "vv"
    for j, (bf, t0, y) in enumerate(banners):
        cmd += ["-i", bf]
        fc.append(f"[{n_in}:v]format=yuva444p10le,setpts=PTS-STARTPTS+{t0:.4f}/TB[bn{j}]")
        fc.append(f"[{last}][bn{j}]overlay=0:{y}:eof_action=pass:format=yuv444p10[vb{j}]")
        n_in += 1
        last = f"vb{j}"
    fc.append(f"[{last}]pad={OUT_W}:{OUT_H + 2 * BAR}:0:{BAR}:black,format=yuv420p[v]" if BAR
              else f"[{last}]format=yuv420p[v]")
    total = sum(m["end"] - m["start"] for m in metas)
    if music:
        path, ss = music
        cmd += ["-ss", f"{max(0.0, ss):.4f}", "-i", path]
        delay = max(0.0, -ss)
        fc.append(f"[{n_in}:a]aresample=48000,aformat=channel_layouts=stereo,volume={MUSIC_VOL:g},"
                  f"adelay={delay * 1000:.0f}:all=1,atrim=duration={total:.4f},afade=t=in:st={delay:.4f}:d=0.3,"
                  f"afade=t=out:st={max(0.0, total - 0.7):.4f}:d=0.7[mu]")
        fc.append(f"[ac]volume={GAME_VOL:g}[ag];[ag][mu]amix=inputs=2:duration=first:normalize=0[am]")
        n_in += 1
        ac = "am"
    else:
        ac = "ac"
    fc.append(f"[{ac}]loudnorm=I={loudness or LOUDNESS:g}:TP=-2:LRA=11,aresample=48000[a]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
            # x264 (processeur) plutôt que NVENC : NVENC affamait les images B (~55 ko contre ~500 ko pour une image
            # clé) -> blocs visibles dans les zones sombres et en mouvement. aq-mode 3 = plus de bits dans le sombre.
            "-c:v", "libx264", "-preset", "slow", "-crf", "12", "-tune", "film", "-profile:v", "high",
            "-x264-params", "aq-mode=3", "-g", "120",
            "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    return out
