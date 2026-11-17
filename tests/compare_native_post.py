"""Compare l'etage video et le signal analogique du coeur natif au moteur Python.

Usage : python tests/compare_native_post.py   (apres compilation de native/)
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from camsim import analog, camdsp                      # noqa: E402

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
        args = [str(CLI), str(a), str(b), str(w), str(h), str(frame), "camera=0"] + ["%s=%s" % (k, v) for k, v in kw.items()]
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        return np.fromfile(b, dtype="<f4").reshape(h, w, 3).astype(np.float64), float(r.stdout.split()[0])


h, w = 120, 160
yy, xx = np.mgrid[0:h, 0:w] / np.array([h, w])[:, None, None]
img = np.stack([0.02 + 0.5 * xx, 0.03 + 0.4 * yy, 0.05 + 0.3 * (1 - xx)], -1)
img[20:50, 20:60] = (0.35, 0.05, 0.03)
img[60:100, 80:140] = (0.03, 0.06, 0.30)
img[:, 145:] = np.where((np.arange(w)[145:] % 2) == 0, 0.5, 0.05)[None, :, None]
img[5:15, 100:110] = 3.0                                 # haute lumiere (coude, ecretage)
img = img.astype(np.float32).astype(np.float64)

# 1. Etage video.
P = dict(enabled=True, knee_point=0.85, knee_slope=0.25, white_clip=1.07, detail_h=0.6, detail_v=0.4,
         detail_coring=0.01, detail_limit=0.12, highlight_chroma=0.8)
worst = 0.0
for inter in (False, True):
    for gain in (1.0, 4.0):
        a = camdsp.apply(img, P, interlaced=inter, gain=gain)
        b, _ = native(img, 1, dsp_enabled=1, interlaced=int(inter), dsp_gain=gain, dsp_knee_point=0.85, dsp_knee_slope=0.25,
                      dsp_white_clip=1.07, dsp_detail_h=0.6, dsp_detail_v=0.4, dsp_detail_coring=0.01,
                      dsp_detail_limit=0.12, dsp_highlight_chroma=0.8)
        worst = max(worst, float(np.abs(a - b).max() / np.abs(a).max()))
check("etage video, au pixel pres", worst < 1e-5, "4 cas (entrelace ou non, gain 1 ou 4) : ecart maximal %.1e" % worst)

# 2. Signal analogique, hasard coupe : meme image.
QUIET_PY = dict(analog_link_snr_db=300, analog_snr_db=300, analog_chroma_snr_db=300, analog_jitter_us=0,
                analog_skew_us=0, analog_head_switch_lines=0, analog_chroma_delay_us=0.5, analog_sharpness=0.8,
                analog_generations=2, seed=1)
QUIET_C = dict(analog_link_snr_db=300, analog_snr_db=300, analog_chroma_snr_db=300, analog_jitter_us=0,
               analog_skew_us=0, analog_head_switch_lines=0, analog_chroma_delay_us=0.5, analog_sharpness=0.8,
               analog_generations=2)
STD = {"pal": 1, "ntsc": 2}
TAPE = {"none": 0, "vhs": 1, "hi8": 5}
worst, n = 0.0, 0
for st in ("pal", "ntsc"):
    for tape in ("none", "vhs", "hi8"):
        for dec in ("notch", "comb"):
            for compo in (True, False):
                if tape == "none" and not compo:
                    continue
                pp = analog.params(dict(QUIET_PY, analog_standard=st, analog_tape=tape, analog_decoder=dec,
                                        analog_composite=compo))
                a = analog.apply(img, pp, 3)
                b, _ = native(img, 3, analog_standard=STD[st], analog_tape=TAPE[tape], analog_comb=int(dec == "comb"),
                              analog_composite=int(compo), **QUIET_C)
                worst = max(worst, float(np.abs(a - b).max() / np.abs(a).max()))
                n += 1
check("analogique sans hasard, au pixel pres", worst < 1e-4, "%d cas (2 normes x 3 bandes x 2 decodeurs, avec ou sans composite) : ecart maximal %.1e" % (n, worst))

# 3. Signal analogique avec le hasard : meme quantite de bruit, memes defauts en moyenne.
grey = np.full((240, 320, 3), 0.18)
ref_py = analog.apply(grey, analog.params(dict(analog_standard="pal", analog_tape="vhs", analog_head_switch_lines=0,
                                               analog_skew_us=0, analog_jitter_us=0)), 3)
ref_c, _ = native(grey, 3, analog_standard=1, analog_tape=1, analog_head_switch_lines=0, analog_skew_us=0, analog_jitter_us=0)
inner = (slice(20, -20), slice(20, -20))
s_py, s_c = ref_py[inner].std((0, 1)), ref_c[inner].std((0, 1))
check("bruit de bande", np.all(np.abs(s_c / s_py - 1) < 0.15), "ecart-type Python %s, natif %s" % (np.round(s_py, 4), np.round(s_c, 4)))

# Les pertes sont tirees au hasard (une dizaine par image) : moyenne sur 8 images.
d_py, d_c = [], []
for f in range(8):
    full_py = analog.apply(grey, analog.params(dict(analog_standard="pal", analog_tape="vhs_lp", analog_dropouts=10,
                                                    analog_dropout_comp=False)), f)
    full_c, _ = native(grey, f, analog_standard=1, analog_tape=2, analog_dropouts=10, analog_dropout_comp=0)
    d_py.append(np.abs(full_py - full_py.mean()).mean())
    d_c.append(np.abs(full_c - full_c.mean()).mean())
d_py, d_c = float(np.mean(d_py)), float(np.mean(d_c))
check("defauts de bande (commutation, pertes)", abs(d_c / d_py - 1) < 0.25,
      "ecart moyen au gris sur 8 images : Python %.4f, natif %.4f" % (d_py, d_c))

big = np.full((1080, 1920, 3), 0.18)
_, ms = native(big, 1, analog_standard=1, analog_tape=1)
check("vitesse de l'analogique", ms < 3000, "%.0f ms pour une image 1920 x 1080 (VHS + composite PAL)" % ms)

# Anamorphose : l'image remplit toute la trame (hasard coupe).
pp = analog.params(dict(QUIET_PY, analog_standard="pal", analog_tape="vhs", analog_framing="anamorphic"))
a = analog.apply(img, pp, 3)
b, _ = native(img, 3, analog_standard=1, analog_tape=1, analog_anamorphic=1, **QUIET_C)
e = float(np.abs(a - b).max() / np.abs(a).max())
check("analogique en anamorphose", e < 1e-3, "ecart maximal %.1e" % e)

print("\n%d / %d comparaisons reussies" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
