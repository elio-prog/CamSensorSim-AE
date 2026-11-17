"""Compare la mesure du mouvement (DIS) et l'obturateur deroulant sur la carte graphique au processeur.

Memes etapes et memes reglages ; les sommes ne sont pas faites dans le meme ordre (calcul parallele), d'ou
de tres petits ecarts qui peuvent, rarement, faire choisir un autre point de depart a un bloc.

Usage : python tests/compare_gpu_flow.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "native" / "build" / "core" / "Release" / "camsim_cli.exe"
results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


def native(img, frame, prev=None, nxt=None, **kw):
    h, w, _ = img.shape
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        img.astype("<f4").tofile(td / "in.raw")
        args = [str(CLI), str(td / "in.raw"), str(td / "out.raw"), str(w), str(h), str(frame)]
        for name, x in (("prev", prev), ("next", nxt)):
            if x is not None:
                x.astype("<f4").tofile(td / (name + ".raw"))
                args.append("%s=%s" % (name, td / (name + ".raw")))
        args += ["%s=%s" % (k, v) for k, v in kw.items()]
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        return np.fromfile(td / "out.raw", dtype="<f4").reshape(h, w, 3).astype(np.float64), r.stdout.strip(), r.stderr


def texture(h, w, seed=0):
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3))
    for s, amp in ((2, 0.15), (6, 0.25), (20, 0.35)):
        n = cv2.GaussianBlur(rng.standard_normal((h, w, 3)), (0, 0), s)
        img += amp * n / n.std()
    img = 0.18 * np.exp(img)
    for k in range(25):
        x, y = rng.integers(0, w - 40), rng.integers(0, h - 40)
        img[y:y + rng.integers(8, 40), x:x + rng.integers(8, 40)] = rng.uniform(0.02, 0.8, 3)
    return img.astype(np.float32)


def shifted(img, dx, dy):
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img.astype(np.float32), m, (img.shape[1], img.shape[0]), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REFLECT)


_, _, info = native(np.zeros((16, 16, 3), np.float32), 0, profile=0, gpu=1)
print(info.strip().splitlines()[0] if info.strip() else "")
if "NVIDIA" not in info:
    print("pas de carte graphique utilisable : test saute")
    sys.exit(0)

H, W = 360, 640
base = texture(H + 200, W + 200, seed=11)
crop = lambda x: x[100:100 + H, 100:100 + W]          # noqa: E731
cur = crop(base)
inner = (slice(40, -40), slice(40, -40))

# 1. Flux : carte graphique et processeur.
for V in ((4.3, -1.8), (17.0, 9.0), (-30.0, 12.0)):
    nb = crop(shifted(base, *V))
    fc, _, _ = native(cur, 0, nxt=nb, flow_only=1, gpu=0)
    fg, _, _ = native(cur, 0, nxt=nb, flow_only=1, gpu=1)
    d = np.hypot(fc[..., 0] - fg[..., 0], fc[..., 1] - fg[..., 1])[inner]
    eg = np.hypot(fg[..., 0][inner] - V[0], fg[..., 1][inner] - V[1])
    check("flux (%g ; %g) px" % V, np.median(d) < 0.02 and np.percentile(d, 99) < 0.3,
          "ecart au processeur : median %.4f, 99e centile %.3f px ; erreur de la carte %.3f px"
          % (np.median(d), np.percentile(d, 99), np.median(eg)))
for fin in (2,):
    nb = crop(shifted(base, 6, 2))
    fc, _, _ = native(cur, 0, nxt=nb, flow_only=1, gpu=0, fast_motion=1)
    fg, _, _ = native(cur, 0, nxt=nb, flow_only=1, gpu=1, fast_motion=1)
    d = np.hypot(fc[..., 0] - fg[..., 0], fc[..., 1] - fg[..., 1])[inner]
    check("flux en mode rapide", np.median(d) < 0.02, "ecart median %.4f px" % np.median(d))

# 2. Objet qui bouge sur un fond fixe.
scene = texture(H, W, seed=3)
obj = texture(120, 180, seed=5) * 1.5
def place(x0):
    s = scene.copy()
    s[120:240, int(x0):int(x0) + 180] = obj
    return s
fc, _, _ = native(place(200), 0, nxt=place(225), flow_only=1, gpu=0)
fg, _, _ = native(place(200), 0, nxt=place(225), flow_only=1, gpu=1)
d = np.hypot(fc[..., 0] - fg[..., 0], fc[..., 1] - fg[..., 1])
check("objet qui bouge", np.median(d) < 0.02 and (d > 1).mean() < 0.01,
      "ecart median %.4f px, %.2f %% des pixels a plus de 1 px" % (np.median(d), 100 * (d > 1).mean()))

# 3. Obturateur deroulant, chaine complete (camera sans bruit) : meme image.
DET = dict(shot_noise=0, read_model=2, row_noise_gain=0, color_bias_gain=0, prnu_sigma=0, dark_current=0)
prv, nxt = crop(shifted(base, -8, -2)), crop(shifted(base, 8, 2))
for kw in (dict(readout_time_s=0.03), dict(readout_time_s=0.03, bottom_to_top=1), dict(readout_time_s=0.03, flicker_hz=100,
                                                                                     flicker_depth=0.4)):
    a, _, _ = native(cur, 3, prev=prv, nxt=nxt, profile=0, gpu=0, **DET, **kw)
    b, _, _ = native(cur, 3, prev=prv, nxt=nxt, profile=0, gpu=1, **DET, **kw)
    e = np.abs(a - b)[inner]
    rel = float(np.median(e) / np.abs(a[inner]).mean())
    check("obturateur deroulant %s" % kw, rel < 1e-4, "ecart median %.1e, 99e centile %.1e"
          % (rel, np.percentile(e, 99) / np.abs(a[inner]).mean()))
a, _, _ = native(cur, 3, nxt=nxt, profile=0, gpu=0, readout_time_s=0.03, **DET)
b, _, _ = native(cur, 3, nxt=nxt, profile=0, gpu=1, readout_time_s=0.03, **DET)
check("obturateur sans image precedente", float(np.median(np.abs(a - b)[inner]) / np.abs(a[inner]).mean()) < 1e-4, "")
a, _, _ = native(cur, 3, prev=prv, nxt=nxt, profile=6, gpu=0, iso=1250, dsp_mode=2, **DET)
b, _, _ = native(cur, 3, prev=prv, nxt=nxt, profile=6, gpu=1, iso=1250, dsp_mode=2, **DET)
check("camescope, seconde trame (mouvement sur la carte)", float(np.median(np.abs(a - b)[inner]) / np.abs(a[inner]).mean()) < 1e-4,
      "ecart median %.1e" % (np.median(np.abs(a - b)[inner]) / np.abs(a[inner]).mean()))

# 4. Vitesse en 1920 x 1080 (sans le premier lancement).
big = texture(1080, 1920, seed=7)
bn = shifted(big, 6, 2)
t = {}
for g in (0, 1):
    _, ms, _ = native(big, 0, nxt=bn, flow_only=1, gpu=g, warmup=1)
    t[g] = float(ms.split()[0])
check("vitesse d'une mesure 1920 x 1080", t[1] < t[0] * 0.6, "processeur %.0f ms, carte graphique %.0f ms" % (t[0], t[1]))
bp = shifted(big, -6, -2)
t2 = {}
for g in (0, 1):
    _, ms, _ = native(big, 0, prev=bp, nxt=bn, profile=0, gpu=g, readout_time_s=0.03, warmup=1)
    t2[g] = float(ms.split()[0])
check("vitesse camera + obturateur 1920 x 1080", t2[1] < t2[0] * 0.6, "processeur %.0f ms, carte graphique %.0f ms" % (t2[0], t2[1]))

print("\n%d / %d comparaisons reussies" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
