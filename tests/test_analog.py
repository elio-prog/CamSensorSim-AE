"""Tests du signal analogique (bande et liaison composite).

Usage : python tests/test_analog.py   (depuis le dossier CameraSim_AE)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim, analog                       # noqa: E402

results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


QUIET = {"analog_link_snr_db": 300, "analog_snr_db": 300, "analog_chroma_snr_db": 300, "analog_jitter_us": 0,
         "analog_skew_us": 0, "analog_head_switch_lines": 0, "analog_chroma_delay_us": 0, "seed": 1}


def P(**kw):
    return analog.params(dict(QUIET, **kw))


def raster(p):
    return p["lines"], p["samples"], p["fs"]


# 1. Lignes TV par MHz.
check("lignes par MHz", abs(analog.lines_per_mhz() - 78.0) < 1e-9, "2 x 1 MHz x 52 us x 3/4 = %.0f lignes (VHS 3 MHz : %.0f)"
      % (analog.lines_per_mhz(), 3 * analog.lines_per_mhz()))

# 2. Un gris traverse toutes les combinaisons sans changer.
grey = np.full((288, 384, 3), 0.18)
worst = 0.0
for st in ("pal", "ntsc"):
    for tape in ("none", "vhs", "svhs", "hi8"):
        for dec in ("notch", "comb"):
            o = analog.apply(grey, P(analog_standard=st, analog_tape=tape, analog_decoder=dec), 2)
            worst = max(worst, float(np.abs(o[20:-20, 20:-20] / 0.18 - 1).max()))
check("gris neutre", worst < 0.03, "ecart maximal %.1f %% (2 normes x 4 bandes x 2 decodeurs)" % (100 * worst))

# 3. Bande passante de la luminance : une mire a 4 MHz passe en S-VHS, pas en VHS.
p = P(analog_standard="pal", analog_tape="vhs", analog_composite=False)
L, N, fs = raster(p)


def luma_gain(tape, f):
    pp = P(analog_standard="pal", analog_tape=tape, analog_composite=False)
    t = np.arange(N) / fs
    Y = np.tile(0.5 + 0.2 * np.sin(2 * np.pi * f * t), (L, 1))
    Y2, _, _ = analog.tape_pass(Y.copy(), np.zeros_like(Y), np.zeros_like(Y), pp, np.random.default_rng(0), 0)
    return float(Y2[100:400, 100:-100].std() / Y[100:400, 100:-100].std())


g3, g45, s45 = luma_gain("vhs", 3.0e6), luma_gain("vhs", 4.5e6), luma_gain("svhs", 4.5e6)
check("bande passante de la luminance", abs(g3 - 0.707) < 0.05 and g45 < 0.25 and s45 > 0.6,
      "VHS : %.2f a 3 MHz (-3 dB, 234 lignes), %.2f a 4,5 MHz ; S-VHS : %.2f a 4,5 MHz" % (g3, g45, s45))

# 4. Bruit FM : son amplitude croit avec la frequence (spectre triangulaire).
pp = P(analog_standard="pal", analog_tape="vhs", analog_composite=False, analog_snr_db=43)
Y = np.full((L, N), 0.5)
Y2, _, _ = analog.tape_pass(Y.copy(), np.zeros_like(Y), np.zeros_like(Y), pp, np.random.default_rng(1), 0)
n = Y2 - Y2.mean(axis=1, keepdims=True)
ps = (np.abs(np.fft.rfft(n, axis=1)) ** 2).mean(axis=0)
f = np.fft.rfftfreq(N, 1 / fs)
lo, hi = ps[(f > 0.2e6) & (f < 0.7e6)].mean(), ps[(f > 2.0e6) & (f < 2.5e6)].mean()
sd = float(n[50:-50].std())
check("bruit FM", hi / lo > 8 and abs(sd / 10 ** (-43 / 20) - 1) < 0.15,
      "puissance du bruit vers 2,2 MHz / vers 0,5 MHz = %.0f ; ecart-type %.4f (43 dB : %.4f)" % (hi / lo, sd, 10 ** (-43 / 20)))

# 5. Couleur sur la bande : tres etroite, et en retard sur la luminance.
pp = P(analog_standard="pal", analog_tape="vhs", analog_composite=False, analog_chroma_delay_us=0.5)
U = np.zeros((L, N))
U[:, N // 2:] = 0.2
_, U2, _ = analog.tape_pass(np.full((L, N), 0.5), U.copy(), np.zeros_like(U), pp, np.random.default_rng(0), 0)
mid = np.argmin(np.abs(U2[200] - 0.1))
delay_us = (mid - N / 2) / fs * 1e6
rise = np.sum((U2[200] > 0.02) & (U2[200] < 0.18)) / fs * 1e6
check("couleur de la bande", abs(delay_us - 0.5) < 0.1 and rise > 1.0,
      "retard %.2f us (reglage 0,5) ; front de couleur etale sur %.1f us (luminance : quelques dixiemes)" % (delay_us, rise))

# 6. Liaison composite : un detail fin de luminance pres de la sous-porteuse devient de la fausse couleur
#    avec le filtre coupe-bande ; le filtre en peigne l'evite (detail vertical identique d'une ligne a l'autre).
t = np.arange(512)
stripes = np.zeros((288, 512, 3))
stripes[...] = (0.18 + 0.15 * np.sin(2 * np.pi * t / 3.4))[None, :, None]


def false_colour(dec):
    o = analog.apply(stripes, P(analog_standard="pal", analog_decoder=dec), 4)[40:-40, 60:-60]
    return float(np.abs(o - o.mean(axis=-1, keepdims=True)).mean())


fc_n, fc_c = false_colour("notch"), false_colour("comb")
check("irisations (fausse couleur)", fc_n > 4 * fc_c and fc_n > 0.005,
      "ecart moyen de couleur sur des rayures grises fines : %.4f (coupe-bande), %.4f (peigne)" % (fc_n, fc_c))

# 7. Chenillement de points : sur un aplat colore, le reste de sous-porteuse dans la luminance bouge d'une image a l'autre.
red = np.full((288, 384, 3), (0.35, 0.08, 0.08))
red[:, 1::24] = (0.06, 0.30, 0.08)                     # bords de couleur : c'est la que naissent les points
red[:, 2::24] = (0.06, 0.30, 0.08)
pp = P(analog_standard="ntsc", analog_decoder="notch")
a0, a1 = analog.apply(red, pp, 0), analog.apply(red, pp, 1)
d = float(np.abs(a0 - a1)[40:-40, 40:-40].mean())
check("chenillement de points", d > 1e-3, "difference entre deux images d'une mire coloree fixe : %.4f" % d)

# 8. Commutation des tetes : seules les dernieres lignes sont decalees.
ramp = np.tile(np.linspace(0.05, 0.6, 384)[None, :, None], (288, 1, 3))
clean = analog.apply(ramp, P(analog_standard="pal", analog_tape="vhs", analog_composite=False), 3)
hs = analog.apply(ramp, P(analog_standard="pal", analog_tape="vhs", analog_composite=False,
                          analog_head_switch_lines=6, analog_head_switch_us=4.0), 3)
diff = np.abs(hs - clean).mean(axis=(1, 2))
check("commutation des tetes", diff[-3:].mean() > 20 * max(diff[20:-20].mean(), 1e-6),
      "ecart en bas de l'image %.4f, au milieu %.6f" % (diff[-3:].mean(), diff[20:-20].mean()))

# 9. Pertes de signal : sans compensateur, des trainees claires ; avec, presque rien.
dark = np.full((288, 384, 3), 0.05)
no_doc = analog.apply(dark, P(analog_standard="pal", analog_tape="vhs", analog_composite=False, analog_dropouts=40,
                              analog_dropout_comp=False), 5)
doc = analog.apply(dark, P(analog_standard="pal", analog_tape="vhs", analog_composite=False, analog_dropouts=40), 5)
check("pertes de signal", (no_doc > 0.2).sum() > 200 and (doc > 0.2).sum() < 0.1 * (no_doc > 0.2).sum(),
      "points clairs : %d sans compensateur, %d avec" % ((no_doc > 0.2).sum(), (doc > 0.2).sum()))

# 10. Image par image : le resultat ne depend que du numero d'image (compatible avec un effet en direct).
pp = analog.params({"analog_standard": "pal", "analog_tape": "vhs", "analog_dropouts": 5, "analog_tracking": 0.5, "seed": 3})
a = analog.apply(grey, pp, 7)
analog.apply(grey, pp, 2)
b = analog.apply(grey, pp, 7)
check("independant de l'ordre", np.array_equal(a, b), "image 7 identique, calculee avant ou apres l'image 2")

# 11. Dans la camera : coupe = inchange ; actif = applique dans process_frame.
x = CameraSim("sonya7s2", seed=2).process_frame(grey, 1)[0]
y = CameraSim("sonya7s2", seed=2, analog_standard="off").process_frame(grey, 1)[0]
z = CameraSim("sonya7s2", seed=2, analog_standard="pal", analog_tape="vhs").process_frame(grey, 1)[0]
check("branchement dans la camera", np.array_equal(x, y) and not np.array_equal(x, z) and z.shape == x.shape,
      "coupe : identique bit a bit ; VHS : image modifiee, meme taille")

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
