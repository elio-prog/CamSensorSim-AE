"""Compare les defauts et pannes du coeur natif au moteur Python.

Usage : python tests/compare_native_faults.py   (apres compilation de native/)
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
        return np.fromfile(b, dtype="<f4").reshape(h, w, 3).astype(np.float64)


DET_PY = dict(shot_noise=False, read_noise_model="off", row_noise_gain=0.0, color_bias_gain=0.0, prnu_sigma=0.0,
              dark_current_e_per_s=0.0, clip_full_well=False, clip_highlights=False, olpf_h_px=0.0, olpf_v_px=0.0,
              demosaic="nearest", spectral="off")
DET_C = dict(shot_noise=0, read_model=2, row_noise_gain=0, color_bias_gain=0, prnu_sigma=0, dark_current=0,
             clip_full_well=0, clip_highlights=0, olpf_h_px=0, olpf_v_px=0, demosaic=2, spectral=0)

h, w = 96, 128
yy, xx = np.mgrid[0:h, 0:w] / np.array([h, w])[:, None, None]
img = np.stack([0.01 + 0.5 * xx, 0.01 + 0.4 * yy, 0.05 + 0.3 * (1 - xx)], -1).astype(np.float32).astype(np.float64)

# 1. Pannes sans hasard : meme image.
cases = [
    ("bits colles du convertisseur", dict(adc_stuck_bits="9:1"), dict(adc_stuck_bit=9, adc_stuck_value=1)),
    ("bit colle a zero", dict(adc_stuck_bits="6:0"), dict(adc_stuck_bit=6, adc_stuck_value=0)),
    ("courbure du convertisseur", dict(adc_inl_lsb=300.0), dict(adc_inl_lsb=300)),
    ("profondeur reduite", dict(adc_bits=6), dict(adc_bits=6)),
    ("glissement de bits", dict(bitslip=3), dict(bitslip=3)),
    ("parasite d'alimentation", dict(interference_dn=80.0, interference_period_px=517.3), dict(interference_dn=80, interference_period_px=517.3)),
    ("adresse de ligne collee", dict(row_address_stuck="4:0"), dict(row_address_bit=4, row_address_value=0)),
    ("adresse de colonne collee", dict(col_address_stuck="2:1"), dict(col_address_bit=2, col_address_value=1)),
]
for name, py, c in cases:
    a = CameraSim("sonya7s2", iso=800, **dict(DET_PY, **py)).process(img, 2)
    b = native(img, 2, profile=0, iso=800, **dict(DET_C, **c))
    diff = np.abs(a - b) > 1e-4 * np.abs(a).max()
    check(name, diff.mean() < 0.002, "%.2f %% de pixels differents (arrondis du convertisseur)" % (100 * diff.mean()))

# 2. Pannes tirees au hasard : meme effet moyen (ecart a l'image propre).
base_py = CameraSim("sonya7s2", iso=800, **DET_PY).process(img, 2)
base_c = native(img, 2, profile=0, iso=800, **DET_C)
rnd = [
    ("pixels morts et colles", dict(dead_pixel_fraction=0.01, stuck_pixel_fraction=0.003), dict(dead_pixel_fraction=0.01, stuck_pixel_fraction=0.003)),
    ("colonnes et lignes mortes", dict(dead_columns=6, dead_rows=4), dict(dead_columns=6, dead_rows=4)),
    ("bruit de colonnes fixe", dict(column_fpn_dn=30.0, column_gain_sigma=0.05), dict(column_fpn_dn=30, column_gain_sigma=0.05)),
    ("lignes lues au mauvais endroit", dict(row_address_error_rate=0.05), dict(row_address_error_rate=0.05)),
    ("convertisseur a marches irregulieres", dict(adc_dnl_lsb=0.8), dict(adc_dnl_lsb=0.8)),
    ("bits inverses au hasard", dict(adc_sparkle_rate=0.01), dict(adc_sparkle_rate=0.01)),
    ("lignes qui glissent", dict(line_shift_rate=0.05, line_shift_max_px=8), dict(line_shift_rate=0.05, line_shift_max_px=8)),
    ("synchro perdue", dict(line_shift_rate=0.05, line_shift_max_px=8, line_shift_persist=True),
     dict(line_shift_rate=0.05, line_shift_max_px=8, line_shift_persist=1)),
]
# Degrade (toute l'echelle du convertisseur est parcourue) et moyenne sur plusieurs images :
# certaines pannes ne touchent qu'une dizaine de lignes par image.
gy, gx = np.mgrid[0:256, 0:256] / 256.0
big = np.stack([0.01 + 0.6 * gx, 0.01 + 0.5 * gy, 0.02 + 0.4 * gx * gy], -1).astype(np.float32).astype(np.float64)
FRAMES = range(1, 25)
bp = {f: CameraSim("sonya7s2", iso=800, **DET_PY).process(big, f) for f in FRAMES}
bc = {f: native(big, f, profile=0, iso=800, **DET_C) for f in FRAMES}
for name, py, c in rnd:
    da = np.mean([np.abs(CameraSim("sonya7s2", iso=800, **dict(DET_PY, **py)).process(big, f) - bp[f]).mean() for f in FRAMES])
    db = np.mean([np.abs(native(big, f, profile=0, iso=800, **dict(DET_C, **c)) - bc[f]).mean() for f in FRAMES])
    ok = (da == 0 and db == 0) or (da > 0 and abs(db / da - 1) < 0.35)
    check(name, ok, "ecart moyen a l'image propre (24 images) : Python %.4f, natif %.4f" % (da, db))

print("\n%d / %d comparaisons reussies" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
