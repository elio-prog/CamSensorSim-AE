"""Tests du filtre passe-bas, de l'obturateur deroulant et du scintillement.

Usage : python tests/test_defects.py   (depuis le dossier CameraSim_AE)
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim.filters import optical_low_pass                                   # noqa: E402
from camsim.shutter import (estimate_flow, flicker_gain, rolling_shutter,     # noqa: E402
                            row_offsets_in_frames)

results = []


def check(name, ok, detail):
    results.append(ok)
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


# 1. Filtre passe-bas : la reponse mesuree doit suivre cos(pi d f) -------------
N = 256
xx = np.arange(N)[None, :] * np.ones((N, 1))
for d, cycles in ((1.0, 32), (1.0, 64), (0.7, 96), (1.0, 128)):
    f = cycles / N                                    # cycles par pixel
    img = (1.0 + 0.5 * np.cos(2 * np.pi * f * xx))[..., None]
    out = optical_low_pass(img, d, 0.0, pad=0)[..., 0]
    amp = (out.max() - out.min()) / 2 / 0.5
    th = abs(math.cos(math.pi * d * f))
    check("filtre passe-bas, d=%.1f px, f=%.3f" % (d, f), abs(amp - th) < 1e-6 and abs(out.mean() - 1) < 1e-9,
          "contraste restant %.4f, theorie %.4f%s" % (amp, th, " (Nyquist : annule)" if cycles == 128 else ""))
img = np.random.default_rng(0).random((N, N, 3))
out = optical_low_pass(img, 0.0, 1.0)
check("filtre vertical seul", np.abs(out.mean(axis=0) - img.mean(axis=0)).max() < 0.02
      and out.std() < img.std(), "n'agit que dans le sens vertical, lumiere conservee")

# 2. Obturateur deroulant, mouvement connu ---------------------------------------
# Une barre verticale qui avance de v pixels par image doit sortir penchee :
# la ligne y est decalee de v * tau(y), tau allant de -r/2 a +r/2 image.
H = W = 400
v, fps, readout = 40.0, 25.0, 0.02                   # r = readout * fps = 0,5 image


def bar(center):
    x = np.arange(W)[None, :] * np.ones((H, 1))
    return np.exp(-0.5 * ((x - center) / 6.0) ** 2)[..., None] * np.ones(3)


def centers(img):
    x = np.arange(W)[None, :]
    g = img[..., 0]
    return (g * x).sum(axis=1) / g.sum(axis=1)


cur = bar(200.0)
flow = np.zeros((H, W, 2))
flow[..., 0] = v
out = rolling_shutter(cur, fps=fps, readout_time_s=readout, flow_next=flow, flow_prev=-flow)
tau = row_offsets_in_frames(H, fps, readout)
err = np.abs(centers(out) - (200.0 + v * tau)).max()
lean = centers(out)[-1] - centers(out)[0]
check("obturateur deroulant, mouvement connu", err < 0.05,
      "barre penchee de %.2f px du haut au bas (attendu %.2f), erreur max %.3f px" % (lean, v * 0.5 * (H - 1) / H, err))
out0 = rolling_shutter(cur, fps=fps, readout_time_s=0.0, flow_next=flow, flow_prev=-flow)
check("temps de lecture nul = obturateur global", np.array_equal(out0, cur), "image inchangee")

# 3. Obturateur deroulant, mouvement estime par OpenCV ----------------------------
rng = np.random.default_rng(1)
big = rng.random((H // 8 + 2, W // 8 + 8))
tex = np.kron(big, np.ones((8, 8)))                   # texture en blocs, pour que le flux soit mesurable
import cv2                                            # noqa: E402
tex = cv2.GaussianBlur(tex, (0, 0), 2.0)


def frame_at(shift):
    return (tex[4:4 + H, 16 + int(shift):16 + int(shift) + W])[..., None] * np.ones(3)


vt = 6.0                                              # la texture avance de 6 px par image
prev_, cur_, next_ = frame_at(vt), frame_at(0), frame_at(-vt)
fl = estimate_flow(cur_, next_)
med = np.median(fl[40:-40, 40:-40, 0])
check("flux optique estime", abs(med - vt) < 0.3, "mouvement mesure %.2f px par image, reel %.2f" % (med, vt))
out = rolling_shutter(cur_, prev_, next_, fps=fps, readout_time_s=0.04)       # r = 1 image
# Verification : la ligne du bas (tau = +0,5) doit ressembler a la texture decalee de +3 px.
row = H - 60
ref = frame_at(0)[row, 60:-60, 0]
best = min(range(-8, 9), key=lambda k: np.abs(out[row, 60:-60, 0] - np.roll(frame_at(0)[row, :, 0], k)[60:-60]).mean())
exp_shift = vt * row_offsets_in_frames(H, fps, 0.04)[row]
check("obturateur deroulant, mouvement estime", abs(best - exp_shift) <= 1.0,
      "ligne %d decalee de %d px, attendu %.1f" % (row, best, exp_shift))

# 4. Scintillement : amplitude = m * sinc(f * T_pose) -------------------------------
for texp, label in ((1 / 50, "1/50 s"), (1 / 100, "1/100 s"), (1 / 400, "1/400 s"), (1 / 2000, "1/2000 s")):
    g = flicker_gain(4000, 0, fps=25.0, readout_time_s=0.03, exposure_time_s=texp, flicker_hz=100.0, depth=0.3)
    amp = (g.max() - g.min()) / 2
    th = 0.3 * abs(math.sin(math.pi * 100 * texp) / (math.pi * 100 * texp))
    nb = 100.0 * 0.03
    check("scintillement 100 Hz, pose %s" % label, abs(amp - th) < 2e-3,
          "amplitude des bandes %.4f, theorie %.4f ; %.0f bandes dans l'image" % (amp, th, nb))

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
