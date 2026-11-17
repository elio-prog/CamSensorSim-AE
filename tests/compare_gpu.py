"""Compare la carte graphique (CUDA) au processeur dans le coeur natif : meme image.

Sans bruit : au pixel pres. Avec le bruit : le hasard est le meme (generateur a compteur) ; seules de rares
valeurs peuvent differer d'un electron quand un calcul en double tombe exactement sur un seuil.

Usage : python tests/compare_gpu.py   (apres compilation de native/ avec le CUDA Toolkit)
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "native" / "build" / "core" / "Release" / "camsim_cli.exe"
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
        return np.fromfile(b, dtype="<f4").reshape(h, w, 3).astype(np.float64), r.stdout.strip(), r.stderr.strip()


h, w = 180, 320
yy, xx = np.mgrid[0:h, 0:w] / np.array([h, w])[:, None, None]
img = np.stack([0.02 + 0.6 * xx, 0.02 + 0.5 * yy, 0.05 + 0.4 * (1 - xx)], -1)
img[10:40, 10:50] = (0.35, 0.05, 0.03)
img[50:80, 90:140] = (0.03, 0.06, 0.30)
img[100:110, 200:210] = 30.0
img = img.astype(np.float32)

_, _, info = native(img, 0, profile=0, gpu=1)
print(info)
if "pas de" in info or "aucune" in info or "sans" in info:
    print("pas de carte graphique utilisable : test saute")
    sys.exit(0)

DET = dict(shot_noise=0, read_model=2, row_noise_gain=0, color_bias_gain=0, prnu_sigma=0, dark_current=0)


def compare(name, tol_exact=True, frame=3, **kw):
    a, _, _ = native(img, frame, gpu=0, **kw)
    b, _, _ = native(img, frame, gpu=1, **kw)
    d = np.abs(a - b)
    rel = float(d.max() / max(np.abs(a).max(), 1e-12))
    same = float((d <= 1e-6 * np.abs(a).max()).mean())
    if tol_exact:
        check(name, rel < 1e-6, "ecart maximal %.1e" % rel)
    else:
        check(name, same > 0.995, "%.3f %% des valeurs identiques, ecart maximal %.1e" % (100 * same, rel))


# 1. Sans bruit : au pixel pres (matrice ou courbes, trois dematricages, appareils differents).
for prof in (0, 1, 2, 4):
    for spec in (0, 1):
        for dem in (0, 1, 2):
            compare("profil %d, spectral %d, dematricage %d" % (prof, spec, dem), profile=prof, spectral=spec, demosaic=dem, **DET)
compare("decalage dans la grille (x0, y0 impairs)", profile=0, x0=3, y0=5, **DET)

# 2. Avec tout le bruit : meme tirage.
compare("bruit complet, ISO 6400", tol_exact=False, profile=0, iso=6400)
compare("bruit complet, Nikon D850, image 7", tol_exact=False, frame=7, profile=1, iso=3200)

# 3. Defauts et pannes, tous ensemble.
FAULTS = dict(hot_pixel_fraction=0.01, dark_current=50, exposure_time_s=0.5, column_fpn_dn=30, column_gain_sigma=0.05,
              dead_pixel_fraction=0.01, stuck_pixel_fraction=0.005, dead_columns=4, dead_rows=3, row_address_bit=3,
              row_address_value=1, col_address_error_rate=0.05, interference_dn=80, interference_period_px=37,
              adc_dnl_lsb=0.8, adc_inl_lsb=300, adc_stuck_bit=9, adc_stuck_value=0, adc_sparkle_rate=0.01,
              sensor_shading=0.4, isp_black_offset_dn=40, prnu_sigma=0.02, adc_bits=10)
compare("defauts et pannes", tol_exact=False, profile=0, iso=1600, **FAULTS)
compare("glissement de bits", profile=0, bitslip=2, **DET)
compare("lignes qui glissent (cumulees)", profile=0, line_shift_rate=0.1, line_shift_max_px=12, line_shift_persist=1, **DET)
compare("lignes qui glissent", profile=0, line_shift_rate=0.1, line_shift_max_px=12, **DET)

# 4. Vitesse en 1920 x 1080.
rng = np.random.default_rng(0)
big = (0.1 * np.exp(rng.standard_normal((1080, 1920, 3)))).astype(np.float32)
t = {}
for g in (0, 1):
    best = 1e9
    for _ in range(3):
        _, ms, _ = native(big, 0, gpu=g, profile=0)
        best = min(best, float(ms.split()[0]))
    t[g] = best
check("vitesse 1920 x 1080", t[1] < t[0], "processeur %.0f ms, carte graphique %.0f ms (avec les transferts)" % (t[0], t[1]))

print("\n%d / %d comparaisons reussies" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
