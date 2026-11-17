"""Compare le signal analogique sur la carte graphique au processeur.

Memes tirages (meme plan des evenements, meme bruit a compteur). Les filtres en frequence sont des
convolutions en simple precision compensee sur la carte, des transformees de Fourier en double sur le
processeur : ecarts de l'ordre du millionieme.

Usage : python tests/compare_gpu_analog.py
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


def native(img, frame, **kw):
    h, w, _ = img.shape
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        img.astype("<f4").tofile(td / "in.raw")
        args = [str(CLI), str(td / "in.raw"), str(td / "out.raw"), str(w), str(h), str(frame), "camera=0"]
        args += ["%s=%s" % (k, v) for k, v in kw.items()]
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        return np.fromfile(td / "out.raw", dtype="<f4").reshape(h, w, 3).astype(np.float64), r.stdout.strip(), r.stderr


_, _, info = native(np.zeros((16, 16, 3), np.float32), 0, gpu=1, analog_standard=1)
if "NVIDIA" not in info:
    print("pas de carte graphique utilisable : test saute")
    sys.exit(0)

rng = np.random.default_rng(2)
img = (0.18 * np.exp(cv2.GaussianBlur(rng.standard_normal((270, 480, 3)).astype(np.float32), (0, 0), 2) * 1.2)).astype(np.float32)
img[100:160, 50:200] = (0.5, 0.05, 0.03)
img[30:80, 300:420] = (0.03, 0.08, 0.45)

QUIET = dict(analog_link_snr_db=300, analog_snr_db=300, analog_chroma_snr_db=300, analog_jitter_us=0, analog_skew_us=0,
             analog_head_switch_lines=0)
CASES = [
    ("PAL composite seul, sans hasard", dict(analog_standard=1, analog_link_snr_db=300)),
    ("NTSC composite, teinte, sans hasard", dict(analog_standard=2, analog_link_snr_db=300, analog_hue_deg=12)),
    ("PAL VHS sans hasard, peigne", dict(analog_standard=1, analog_tape=1, analog_comb=1, **QUIET)),
    ("NTSC S-VHS sans hasard, liaison separee", dict(analog_standard=2, analog_tape=3, analog_composite=0, **QUIET)),
    ("PAL VHS complet", dict(analog_standard=1, analog_tape=1)),
    ("NTSC VHS LP, pertes, calage, 2 copies, accentuation",
     dict(analog_standard=2, analog_tape=2, analog_dropouts=30, analog_dropout_comp=0, analog_tracking=0.8,
          analog_generations=2, analog_sharpness=1)),
    ("PAL Hi8, pertes compensees, calage, peigne, anamorphose",
     dict(analog_standard=1, analog_tape=5, analog_dropouts=20, analog_tracking=0.5, analog_comb=1, analog_anamorphic=1)),
]
for name, kw in CASES:
    a, _, _ = native(img, 4, gpu=0, **kw)
    b, _, err = native(img, 4, gpu=1, **kw)
    used = "carte graphique : oui" in err or "NVIDIA" in err
    d = np.abs(a - b)
    rel = float(d.max() / np.abs(a).max())
    med = float(np.median(d) / np.abs(a).mean())
    check(name, rel < 2e-3 and med < 1e-5, "ecart maximal %.1e, median %.1e" % (rel, med))

# Vitesse en 1920 x 1920.
big = (0.18 * np.exp(rng.standard_normal((1920, 1920, 3)))).astype(np.float32)
t = {}
for g in (0, 1):
    _, ms, _ = native(big, 3, gpu=g, warmup=1, analog_standard=1, analog_tape=1)
    t[g] = float(ms.split()[0])
check("vitesse 1920 x 1920 (VHS + composite PAL)", t[1] < t[0] / 2, "processeur %.0f ms, carte graphique %.0f ms" % (t[0], t[1]))

print("\n%d / %d comparaisons reussies" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
