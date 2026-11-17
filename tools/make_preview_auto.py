"""Video de demonstration des automatismes (exposition, gain, balance des blancs).

L'image 1010 du rendu est reprise sur 100 images, et l'ECLAIRAGE est modifie pour la
demonstration (ce n'est pas un effet de camera) :
  images  0-24 : eclairage normal
  images 25-49 : eclairage x 4 (une lampe s'allume)
  images 50-74 : eclairage x 4 et orange (lumiere chaude)
  images 75-99 : retour a l'eclairage normal
Trois cameras cote a cote : automatismes coupes, automatismes doux, automatismes nerveux
(qui pompent). Sorties : une video MP4 et une planche PNG avec la courbe d'exposition.

Usage : python tools/make_preview_auto.py <image.exr> <dossier de sortie>
"""
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim, sources               # noqa: E402
from camsim.color import preview_srgb               # noqa: E402
from camsim.exr_io import read_exr                  # noqa: E402

N, S = 100, 480
CAM = "icx409ak_camcorder"
CONFIGS = [
    ("Automatismes coupes", dict()),
    ("Automatismes doux", dict(auto_exposure=True, auto_wb=True, ae_damping=0.9, ae_speed_s=0.4, awb_speed_s=1.0)),
    ("Automatismes nerveux (pompage)", dict(auto_exposure=True, auto_wb=True, ae_damping=0.22, ae_speed_s=0.18,
                                            ae_max_rate_ev_s=12, ae_deadband_ev=0.05, awb_speed_s=0.3)),
]


def light(k):
    if k < 25:
        return 1.0, np.ones(3)
    if k < 50:
        return 4.0, np.ones(3)
    if k < 75:
        return 4.0, np.array([1.5, 1.0, 0.55])
    return 1.0, np.ones(3)


def main(src, out_dir):
    out_dir = Path(out_dir)
    rgb, _, _ = read_exr(src)
    base = cv2.resize(rgb, (S, S), interpolation=cv2.INTER_AREA) * 2.0
    frames = [[] for _ in CONFIGS]
    curves = [[] for _ in CONFIGS]
    for c, (label, kw) in enumerate(CONFIGS):
        sim = CameraSim(CAM, iso=1250, fps=25, **kw)
        for k in range(N):
            g, tint = light(k)
            o, _ = sim.process_frame(base * g * tint, k)
            frames[c].append(preview_srgb(o, 1.0))
            curves[c].append(sim.auto.x)
        print(label, "fini")
    # Video : trois cameras cote a cote.
    tmp = out_dir / "_auto_frames"
    tmp.mkdir(parents=True, exist_ok=True)
    for k in range(N):
        row = np.concatenate([frames[c][k] for c in range(len(CONFIGS))], axis=1)
        im = Image.fromarray(row)
        d = ImageDraw.Draw(im)
        for c, (label, _) in enumerate(CONFIGS):
            d.text((c * S + 6, 6), label, fill=(255, 255, 0))
        d.text((6, S - 16), "image %d - eclairage x%d%s" % (k, light(k)[0], " orange" if 50 <= k < 75 else ""),
               fill=(255, 255, 0))
        im.save(tmp / ("f_%03d.png" % k))
    mp4 = out_dir / "automatismes_demo.mp4"
    subprocess.run([sources.find_ffmpeg(), "-v", "error", "-y", "-framerate", "25", "-i", str(tmp / "f_%03d.png"),
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "16", str(mp4)], check=True)
    for f in tmp.glob("*.png"):
        f.unlink()
    tmp.rmdir()
    # Planche : quelques images de chaque camera, et la courbe d'exposition.
    picks = [20, 27, 32, 45, 55, 70, 78, 90]
    t = 200
    sheet = Image.new("RGB", (len(picks) * (t + 4) + 140, len(CONFIGS) * (t + 4) + 260), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for c, (label, _) in enumerate(CONFIGS):
        d.text((4, c * (t + 4) + t // 2), label[:22], fill=(230, 230, 230))
        for i, k in enumerate(picks):
            sheet.paste(Image.fromarray(frames[c][k]).resize((t, t)), (140 + i * (t + 4), c * (t + 4)))
            if c == 0:
                pass
    for i, k in enumerate(picks):
        d.text((140 + i * (t + 4) + 4, len(CONFIGS) * (t + 4) + 2), "image %d" % k, fill=(200, 200, 200))
    # Courbe
    gy0, gh, gx0, gw = len(CONFIGS) * (t + 4) + 30, 210, 140, len(picks) * (t + 4) - 10
    d.rectangle([gx0, gy0, gx0 + gw, gy0 + gh], outline=(90, 90, 90))
    lo, hi = -3.0, 1.0
    yv = lambda v: gy0 + gh * (hi - v) / (hi - lo)
    for v in (-2, -1, 0):
        d.line([gx0, yv(v), gx0 + gw, yv(v)], fill=(55, 55, 55))
        d.text((gx0 - 40, yv(v) - 6), "%+d" % v, fill=(160, 160, 160))
    cols = [(160, 160, 160), (80, 200, 120), (240, 120, 60)]
    for c in range(len(CONFIGS)):
        pts = [(gx0 + gw * k / (N - 1), yv(curves[c][k])) for k in range(N)]
        d.line(pts, fill=cols[c], width=2)
        d.text((gx0 + 8, gy0 + 6 + 14 * c), CONFIGS[c][0], fill=cols[c])
    d.text((4, gy0 + gh // 2), "exposition (diaph.)", fill=(200, 200, 200))
    png = out_dir / "apercu_automatismes.png"
    sheet.save(png)
    print("ecrit", mp4, png)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
