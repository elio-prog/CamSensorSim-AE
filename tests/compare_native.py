"""Compare le coeur natif C++ (native/) au moteur Python de reference.

Usage : python tests/compare_native.py   (depuis le dossier CameraSim_AE, apres compilation)

Sans bruit, les deux doivent donner la meme image a l'arrondi pres. Avec le bruit, le hasard
n'est pas tire de la meme facon : on compare la moyenne et l'ecart-type.
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
PROFILES = ["sonya7s2", "nikond850", "canoneos5d4", "canoneos70d", "canoneos700d"]
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
        out = np.fromfile(b, dtype="<f4").reshape(h, w, 3).astype(np.float64)
        return out, float(r.stdout.split()[0])


def python(img, frame, profile, **kw):
    return CameraSim(profile, **kw).process(img, frame)


DET_PY = dict(shot_noise=False, read_noise_model="off", row_noise_gain=0.0, color_bias_gain=0.0, prnu_sigma=0.0,
              dark_current_e_per_s=0.0, quantize=False, clip_full_well=False, clip_highlights=False,
              olpf_h_px=0.0, olpf_v_px=0.0)
DET_C = dict(shot_noise=0, read_model=2, row_noise_gain=0, color_bias_gain=0, prnu_sigma=0, dark_current=0,
             quantize=0, clip_full_well=0, clip_highlights=0, olpf_h_px=0, olpf_v_px=0)
DEM = {"malvar": 0, "bilinear": 1, "nearest": 2}

# Image d'essai : degrade, couleurs saturees, details fins.
h, w = 64, 96
yy, xx = np.mgrid[0:h, 0:w] / np.array([h, w])[:, None, None]
img = np.stack([0.02 + 0.6 * xx, 0.02 + 0.4 * yy, 0.05 + 0.3 * (1 - xx)], -1)
img[10:30, 10:40] = (0.35, 0.05, 0.03)
img[35:55, 50:80] = (0.03, 0.06, 0.30)
img[:, 85:] = np.where((np.arange(w)[85:] % 2) == 0, 0.5, 0.05)[None, :, None]

if not CLI.exists():
    print("outil natif introuvable : %s (compiler d'abord native/)" % CLI)
    sys.exit(1)

# 1. Sans bruit : meme image, pour chaque appareil, couleur matricielle et spectrale, trois dematricages.
worst = 0.0
cases = 0
for i, prof in enumerate(PROFILES):
    for spec in (0, 1):
        for dem, code in DEM.items():
            a = python(img, 1, prof, spectral="on" if spec else "off", demosaic=dem, **DET_PY)
            b, _ = native(img, 1, profile=i, spectral=spec, demosaic=code, **DET_C)
            worst = max(worst, float(np.abs(a - b).max() / np.abs(a).max()))
            cases += 1
check("sans bruit, au pixel pres", worst < 2e-4, "%d cas (5 appareils x 2 couleurs x 3 dematricages) : ecart maximal %.1e" % (cases, worst))

# 2. Filtre passe-bas optique : filtre a coefficients (C++) contre transformee de Fourier (Python).
a = python(img, 1, "sonya7s2", **dict(DET_PY, olpf_h_px=1.0, olpf_v_px=1.0))
b, _ = native(img, 1, profile=0, **dict(DET_C, olpf_h_px=1.0, olpf_v_px=1.0))
inner = (slice(6, -6), slice(6, -6))
e = float(np.abs(a[inner] - b[inner]).max() / np.abs(a).max())
check("filtre passe-bas", e < 5e-3, "ecart maximal %.1e (loin des bords)" % e)

# 3. Avec tout le bruit : memes niveaux et meme bruit, a plusieurs lumieres et sensibilites.
ok, lines = True, []
for prof_i, prof in ((0, "sonya7s2"), (3, "canoneos70d")):
    for iso in (400, 6400):
        for level in (0.01, 0.18, 0.7):
            flat = np.full((160, 160, 3), level)
            a = python(flat, 3, prof, iso=iso, olpf_h_px=0.0, olpf_v_px=0.0)[8:-8, 8:-8]
            b, _ = native(flat, 3, profile=prof_i, iso=iso, olpf_h_px=0, olpf_v_px=0)
            b = b[8:-8, 8:-8]
            # Le bruit de ligne rend les pixels d'une ligne dependants : l'incertitude sur la moyenne se
            # compte en lignes, pas en pixels. On compare donc l'ecart des moyennes a cette incertitude.
            se = np.sqrt(a.mean(axis=1).var(0) / a.shape[0] + b.mean(axis=1).var(0) / b.shape[0])
            dm = (np.abs(a.mean((0, 1)) - b.mean((0, 1))) / np.maximum(se, 1e-12)).max()
            ds = np.abs(a.std((0, 1)) / b.std((0, 1)) - 1).max()
            rel = np.abs(a.mean((0, 1)) - b.mean((0, 1))).max() / level
            ok &= dm < 4.0 and ds < 0.06
            lines.append("%s ISO %d niveau %.2f : moyenne %.2f %% (%.1f fois l'incertitude), bruit %.1f %%"
                         % (prof, iso, level, 100 * rel, dm, 100 * ds))
check("avec le bruit (statistiques)", ok, "\n      " + "\n      ".join(lines))

# 4. Image par image : le resultat ne depend que du numero d'image.
flat = np.full((64, 64, 3), 0.18)
b5, _ = native(flat, 5, profile=0)
b5b, _ = native(flat, 5, profile=0)
b6, _ = native(flat, 6, profile=0)
check("independant de l'ordre", np.array_equal(b5, b5b) and not np.array_equal(b5, b6), "image 5 identique a chaque calcul, image 6 differente")

# 5. Vitesse en 1920 x 1080.
big = np.full((1080, 1920, 3), 0.18) + 0.05 * np.random.default_rng(0).random((1080, 1920, 3))
_, ms = native(big, 1, profile=0)
check("vitesse", ms < 2000, "%.0f ms par image 1920 x 1080 sur le processeur (Python : plusieurs secondes)" % ms)

print("\n%d / %d comparaisons reussies" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
