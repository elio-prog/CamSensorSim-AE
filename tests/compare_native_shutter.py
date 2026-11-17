"""Compare le temps dans le coeur natif (obturateur deroulant, scintillement, seconde trame une demi-image
plus tard) au moteur Python.

Le mouvement entre images est estime par la meme methode (DIS, reglages MEDIUM d'OpenCV), reecrite en C++ :
on verifie que le flux natif retrouve un deplacement connu et suit celui d'OpenCV, puis que les images sont
les memes la ou le mouvement est bien defini (translation d'une texture), et proches autour d'un objet
qui bouge (la ou il cache le fond, les deux estimations different un peu).

Usage : python tests/compare_native_shutter.py   (apres compilation de native/)
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from camsim import CameraSim                          # noqa: E402
from camsim.shutter import estimate_flow              # noqa: E402

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
        t0 = time.perf_counter()
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        return np.fromfile(td / "out.raw", dtype="<f4").reshape(h, w, 3).astype(np.float64), r.stdout.strip()


def texture(h, w, seed=0):
    """Scene texturee (details a plusieurs echelles, bords nets) en ACEScg lineaire."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3))
    for s, amp in ((2, 0.15), (6, 0.25), (20, 0.35)):
        n = cv2.GaussianBlur(rng.standard_normal((h, w, 3)), (0, 0), s)
        img += amp * n / n.std()
    img = 0.18 * np.exp(img)
    for k in range(25):                              # quelques rectangles a bords nets
        x, y = rng.integers(0, w - 40), rng.integers(0, h - 40)
        img[y:y + rng.integers(8, 40), x:x + rng.integers(8, 40)] = rng.uniform(0.02, 0.8, 3)
    return img


def shifted(img, dx, dy):
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img.astype(np.float32), m, (img.shape[1], img.shape[0]), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REFLECT).astype(np.float64)


DET_PY = dict(shot_noise=False, read_noise_model="off", row_noise_gain=0.0, color_bias_gain=0.0, prnu_sigma=0.0,
              dark_current_e_per_s=0.0, quantize=False, clip_full_well=False, clip_highlights=False,
              olpf_h_px=0.0, olpf_v_px=0.0)   # filtre passe-bas : deja compare ailleurs (a 0,4 % pres)
DET_C = dict(shot_noise=0, read_model=2, row_noise_gain=0, color_bias_gain=0, prnu_sigma=0, dark_current=0,
             quantize=0, clip_full_well=0, clip_highlights=0, olpf_h_px=0, olpf_v_px=0)
IDX = {"sonya7s2": 0, "canoneos700d": 4, "kai0373_3ccd": 5, "icx409ak_camcorder": 6}

H, W = 180, 320
base = texture(H + 80, W + 80)
crop = lambda x: x[40:40 + H, 40:40 + W]                          # noqa: E731
V = (4.3, -1.8)                                                   # mouvement par image (pixels)
cur = crop(base)
prv = crop(shifted(base, -V[0], -V[1]))
nxt = crop(shifted(base, V[0], V[1]))
inner = (slice(20, -20), slice(20, -20))

# 1. Flux : le natif retrouve un deplacement connu, et suit celui d'OpenCV (moteur Python).
for V2 in ((4.3, -1.8), (17.0, 9.0), (-30.0, 12.0)):
    nb = crop(shifted(base, *V2))
    fl, _ = native(cur, 0, nxt=nb, flow_only=1)
    fp = estimate_flow(cur, nb)
    e_c = np.hypot(fl[..., 0][inner] - V2[0], fl[..., 1][inner] - V2[1])
    e_p = np.hypot(fp[..., 0][inner] - V2[0], fp[..., 1][inner] - V2[1])
    d = np.hypot(fl[..., 0] - fp[..., 0], fl[..., 1] - fp[..., 1])[inner]
    check("flux sur une translation (%g ; %g) px" % V2, np.median(e_c) < 2 * np.median(e_p) + 0.05 and np.median(d) < 0.15,
          "erreur mediane natif %.3f px (Python %.3f) ; ecart au flux Python : median %.3f, 95e centile %.2f px"
          % (np.median(e_c), np.median(e_p), np.median(d), np.percentile(d, 95)))


def compare(name, key, py, c, tol, frame=3, image=cur, prev=prv, nx=nxt, region=inner):
    a = CameraSim(key, **dict(DET_PY, **py)).process_frame(image, frame, prev=prev, nxt=nx)[0]
    b, _ = native(image, frame, prev=prev, nxt=nx, profile=IDX[key], **dict(DET_C, **c))
    d = np.abs(a - b)[region]
    e = float(np.median(d) / np.abs(a[region]).mean())
    e99 = float(np.percentile(d, 99) / np.abs(a[region]).mean())
    check(name, e < tol and e99 < 20 * tol, "ecart median %.1e, 99e centile %.1e (relatifs)" % (e, e99))


# 2. Scintillement (sans mouvement) : formule exacte.
for ro in (0.0, 0.03):
    compare("scintillement 100 Hz, lecture %.0f ms" % (ro * 1000), "sonya7s2",
            dict(iso=800, flicker_hz=100, flicker_depth=0.4, flicker_phase=0.7, readout_time_s=ro, exposure_time_s=1 / 200),
            dict(iso=800, flicker_hz=100, flicker_depth=0.4, flicker_phase=0.7, readout_time_s=ro, exposure_time_s=1 / 200),
            1e-5, prev=None, nx=None)

# 3. Obturateur deroulant sur une translation.
for ro, b2t in ((0.03, 0), (0.025, 1)):
    sens = "bas vers haut" if b2t else "haut vers bas"
    compare("obturateur deroulant %.0f ms, %s" % (ro * 1000, sens), "sonya7s2",
            dict(iso=800, readout_time_s=ro, shutter_direction="bottom_to_top" if b2t else "top_to_bottom"),
            dict(iso=800, readout_time_s=ro, bottom_to_top=b2t), 0.01)
compare("obturateur deroulant sans image precedente", "sonya7s2", dict(iso=800, readout_time_s=0.03),
        dict(iso=800, readout_time_s=0.03), 0.01, prev=None)
compare("obturateur deroulant en mode video 1080p", "canoneos700d", dict(iso=800, readout_time_s=0.03, video_mode="1080p"),
        dict(iso=800, readout_time_s=0.03, video_mode=1), 0.01)

# 4. Seconde trame une demi-image plus tard (3 CCD entrelace, camescope).
compare("3 CCD entrelace, seconde trame", "kai0373_3ccd", dict(iso=400, interlace="fields"), dict(iso=400, interlace=1), 0.01)
compare("camescope, seconde trame", "icx409ak_camcorder", dict(iso=1250, dsp_enabled="off"), dict(iso=1250, dsp_mode=2), 0.01)
compare("camescope sur la vraie grille, seconde trame", "icx409ak_camcorder",
        dict(iso=1250, dsp_enabled="off", sensor_resolution="capteur"), dict(iso=1250, dsp_mode=2, resolution=1), 0.01)

# La seconde trame change vraiment l'image (sinon le test 4 ne prouverait rien).
a0 = native(cur, 3, profile=6, iso=1250, dsp_mode=2, **DET_C)[0]
a1 = native(cur, 3, nxt=nxt, profile=6, iso=1250, dsp_mode=2, **DET_C)[0]
diff = np.abs(a1 - a0)[inner].mean() / np.abs(a0[inner]).mean()
check("la seconde trame bouge bien", diff > 0.01, "ecart moyen entre trame fixe et trame en mouvement %.1f %%" % (100 * diff))

# 5. Objet qui bouge sur un fond fixe : flux different la ou l'objet cache le fond ; ecart limite.
scene = texture(H, W, seed=3)
obj = texture(60, 90, seed=5) * 1.5
def place(x0):
    s = scene.copy()
    s[60:120, int(x0):int(x0) + 90] = obj
    return s
compare("objet qui bouge (occultations)", "sonya7s2", dict(iso=800, readout_time_s=0.03), dict(iso=800, readout_time_s=0.03),
        0.02, image=place(110), prev=place(100), nx=place(120), region=(slice(10, -10), slice(10, -10)))

# 6. Vitesse.
big_img = texture(1080, 1920, seed=7)
_, ms = native(big_img, 0, nxt=shifted(big_img, 3, 1), flow_only=1)
check("vitesse du flux 1920 x 1080", float(ms.split()[0]) < 500, ms + " par flux")
_, ms_fast = native(big_img, 0, nxt=shifted(big_img, 3, 1), flow_only=1, fast_motion=1)

# 7. Mouvement rapide (quart de la resolution) : un peu moins fin, nettement plus rapide.
hh, ww = 540, 960
bb = texture(hh + 200, ww + 200, seed=13)
cc = bb[100:100 + hh, 100:100 + ww]
inn = (slice(40, -40), slice(40, -40))
errs = []
for V2 in ((6.5, -2.5), (25.0, 10.0)):
    nb = shifted(bb, *V2)[100:100 + hh, 100:100 + ww]
    a0, _ = native(cc, 3, nxt=nb, profile=0, iso=800, readout_time_s=0.03, **DET_C)
    a1, _ = native(cc, 3, nxt=nb, profile=0, iso=800, readout_time_s=0.03, fast_motion=1, **DET_C)
    errs.append(float(np.median(np.abs(a1 - a0)[inn]) / np.abs(a0[inn]).mean()))
check("mouvement rapide : meme obturateur deroulant", max(errs) < 5e-3,
      "ecart median a l'estimation fine %s ; flux 1920 x 1080 : %s (fin : %s)"
      % (", ".join("%.1e" % e for e in errs), ms_fast, ms))

print("\n%d / %d comparaisons reussies" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
