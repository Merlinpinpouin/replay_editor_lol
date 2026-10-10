"""Mesure les à-coups de caméra : mouvement global image à image (corrélation de phase), puis pics de 'jerk'."""
import subprocess, sys
import numpy as np
w, h = 320, 156
for f in sys.argv[1:]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-hwaccel", "cuda", "-i", f, "-vf", f"crop=iw:ih-188:0:94,scale={w}:{h}",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
    n = len(raw) // (w * h)
    fr = np.frombuffer(raw[: n * w * h], np.uint8).reshape(n, h, w).astype(np.float32)
    win = np.outer(np.hanning(h), np.hanning(w))
    F = [np.fft.fft2((x - x.mean()) * win) for x in fr]
    mv = []
    for a, b in zip(F[:-1], F[1:]):
        R = a * np.conj(b); R /= np.abs(R) + 1e-9
        r = np.fft.ifft2(R).real
        i = np.unravel_index(np.argmax(r), r.shape)
        mv.append([v if v < s / 2 else v - s for v, s in zip(i, r.shape)])
    mv = np.array(mv, float)
    sp = np.hypot(*mv.T)
    acc = np.abs(np.diff(sp))
    dup = int((np.abs(np.diff(fr, axis=0)).mean((1, 2)) < 0.3).sum())
    spikes = np.nonzero(acc > 6)[0]
    print(f"{f.split(chr(92))[-1]}: {n} img, vitesse moy {sp.mean():.1f}px, jerk p99 {np.percentile(acc, 99):.1f}, "
          f"pics>6px: {len(spikes)} @ {[round(i / 60, 2) for i in spikes[:12]]}, images figées: {dup}")
