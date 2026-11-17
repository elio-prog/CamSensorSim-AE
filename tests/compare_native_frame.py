"""Compare la chaine complete d'une image du coeur natif (vraie grille du capteur, mode video des
appareils photo, etage video du profil) au moteur Python (CameraSim.process_frame).

Usage : python tests/compare_native_frame.py   (apres compilation de native/)
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from camsim import CameraSim                          # noqa: E402

CLI = ROOT / "native" / "build" / "core" / "Release" / "camsim_cli.exe"
IDX = {"sonya7s2": 0, "nikond850": 1, "canoneos5d4": 2, "canoneos70d": 3, "canoneos700d": 4, "kai0373_3ccd": 5,
       "icx409ak_camcorder": 6}
results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


def native(img, frame, **kw):
    h, w, _ = img.shape
    with tempfile.TemporaryDirectory() as td:
        a, b = Path(td) / "in.raw", Path(td) / "out.raw"
        img.astype("<f4").tofile(a)
        args = [str(CLI), str(a), str(b), str(w), str(h), str(frame)] + ["%s=%s" % (k, v) for k, v in kw.items()]
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        return np.fromfile(b, dtype="<f4").reshape(h, w, 3).astype(np.float64), r.stderr


DET_PY = dict(shot_noise=False, read_noise_model="off", row_noise_gain=0.0, color_bias_gain=0.0, prnu_sigma=0.0,
              dark_current_e_per_s=0.0, quantize=False, clip_full_well=False, clip_highlights=False,
              olpf_h_px=0.0, olpf_v_px=0.0)
DET_C = dict(shot_noise=0, read_model=2, row_noise_gain=0, color_bias_gain=0, prnu_sigma=0, dark_current=0,
             quantize=0, clip_full_well=0, clip_highlights=0, olpf_h_px=0, olpf_v_px=0)
UP = {"lanczos": 0, "bilinear": 1, "nearest": 2}
RO = {"full": 0, "skip3": 1, "bin3x3": 2, "vbin2": 3}

h, w = 90, 160
yy, xx = np.mgrid[0:h, 0:w] / np.array([h, w])[:, None, None]
img = np.stack([0.02 + 0.5 * xx, 0.02 + 0.4 * yy, 0.05 + 0.3 * (1 - xx)], -1)
img[10:40, 10:50] = (0.35, 0.05, 0.03)
img[50:80, 90:140] = (0.03, 0.06, 0.30)
img = img.astype(np.float32).astype(np.float64)


def compare(name, key, py, c, tol=2e-4, image=img):
    a = CameraSim(key, **dict(DET_PY, **py)).process_frame(image, 2)[0]
    b, _ = native(image, 2, profile=IDX[key], **dict(DET_C, **c))
    e = float(np.abs(a - b).max() / np.abs(a).max())
    check(name, e < tol, "ecart maximal %.1e" % e)


# 1. Vraie grille du capteur (la grille du camescope et du 3 CCD est plus petite que l'image).
for up in ("lanczos", "bilinear", "nearest"):
    compare("grille du camescope, agrandi %s" % up, "icx409ak_camcorder",
            dict(iso=1250, sensor_resolution="capteur", sensor_upscale=up, dsp_enabled="off"),
            dict(iso=1250, resolution=1, upscale=UP[up]))
compare("grille du 3 CCD", "kai0373_3ccd", dict(iso=400, sensor_resolution="capteur"), dict(iso=400, resolution=1))
compare("grille d'un appareil photo (plus fine que l'image)", "canoneos700d", dict(iso=800, sensor_resolution="capteur"),
        dict(iso=800, resolution=1), tol=5e-4)

# 2. Etage video du profil (camescope) : actif par defaut.
compare("camescope avec son etage video", "icx409ak_camcorder", dict(iso=1250), dict(iso=1250, dsp_mode=0))

# 3. Mode video des appareils photo.
for key, mode, ro in (("canoneos700d", "1080p", "auto"), ("canoneos5d4", "4k", "auto"), ("nikond850", "1080p", "auto"),
                      ("canoneos700d", "1080p", "bin3x3"), ("sonya7s2", "4k", "auto"), ("nikond850", "4k", "auto")):
    py = dict(iso=800, video_mode=mode, video_readout=ro)
    c = dict(iso=800, video_mode=1 if mode == "1080p" else 2, video_readout=-1 if ro == "auto" else RO[ro])
    compare("video %s %s (%s)" % (key, mode, ro), key, py, c, tol=1e-3)

# 4. Mode absent : signale, calcule en photo.
_, err = native(img, 2, profile=IDX["canoneos70d"], video_mode=2, **DET_C)
check("mode video absent", "absent" in err, err.strip() or "pas de message")

# 5. Avec le bruit, en mode video : meme niveau et meme bruit.
flat = np.full((180, 320, 3), 0.18)
a = CameraSim("canoneos700d", iso=3200, video_mode="1080p", olpf_h_px=0.0, olpf_v_px=0.0).process_frame(flat, 3)[0][10:-10, 10:-10]
b, _ = native(flat, 3, profile=IDX["canoneos700d"], iso=3200, video_mode=1, olpf_h_px=0, olpf_v_px=0)
b = b[10:-10, 10:-10]
dm = np.abs(a.mean((0, 1)) / b.mean((0, 1)) - 1).max()
ds = np.abs(a.std((0, 1)) / b.std((0, 1)) - 1).max()
check("video avec bruit", dm < 0.01 and ds < 0.08, "moyenne a %.2f %% pres, bruit a %.1f %% pres" % (100 * dm, 100 * ds))

print("\n%d / %d comparaisons reussies" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
