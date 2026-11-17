"""Tests du noyau capteur : chaque etage est compare a la theorie.

Usage : python tests/test_core.py   (depuis le dossier CameraSim_AE)
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim                                        # noqa: E402
from camsim import color as C                                       # noqa: E402
from camsim.sensor import (dark_current_temperature_factor,         # noqa: E402
                           tukey_lambda_sample, tukey_lambda_variance)

PROFILE = "sonya7s2"
CLEAN = dict(shot_noise=False, read_noise_model="off", row_noise_gain=0.0, color_bias_gain=0.0,
             quantize=False, clip_full_well=False, clip_highlights=False,
             olpf_h_px=0.0, olpf_v_px=0.0,
             spectral="off")   # mecanique du moteur : matrice exacte (la couleur spectrale a ses propres tests)
results = []


def check(name, ok, detail):
    results.append(ok)
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


def flat(value, n=512):
    return np.full((n, n, 3), value, dtype=np.float64)


# 1. Couleur --------------------------------------------------------------
ref = np.array([0.6624541811, 0.1340042065, 0.1561876870])   # 1re ligne AP1 -> XYZ (doc ACES)
err = np.abs(C.AP1_TO_XYZ[0] - ref).max()
check("matrice ACEScg -> XYZ", err < 2e-4, "ecart max %.1e avec la valeur publiee" % err)
w = C.AP1_TO_SRGB @ np.ones(3)
check("blanc ACEScg -> blanc sRGB", np.abs(w - 1).max() < 1e-6, "blanc = %s" % np.round(w, 6))

# 2. Aller-retour sans defaut ----------------------------------------------
sim = CameraSim(PROFILE, iso=800, **CLEAN)
patch = np.empty((64, 64, 3))
patch[...] = [0.30, 0.12, 0.05]
err = np.abs(sim.process(patch) - patch).max()
check("aller-retour, aplat colore", err < 1e-9, "erreur max %.1e" % err)
yy, xx = np.mgrid[0:256, 0:256] / 255.0
grad = np.stack([0.05 + 0.5 * xx, 0.05 + 0.5 * yy, 0.3 - 0.2 * xx * yy], axis=-1)
for dm, tol in (("malvar", 2e-3), ("bilinear", 5e-3)):
    s2 = CameraSim(PROFILE, iso=800, demosaic=dm, **CLEAN)
    e = np.abs(s2.process(grad) - grad)[8:-8, 8:-8].max()
    check("aller-retour, degrade (%s)" % dm, e < tol, "erreur max %.1e" % e)

# 3. Loi de Tukey-lambda ----------------------------------------------------
rng = np.random.default_rng(0)
for lam in (-0.14, 0.0, 0.12):
    v = tukey_lambda_sample(rng, lam, 4_000_000).var()
    th = tukey_lambda_variance(lam)
    check("variance Tukey-lambda (lambda=%+.2f)" % lam, abs(v / th - 1) < 0.01, "mesuree %.4f, theorie %.4f" % (v, th))

# 4. Courbe de transfert de photons (Janesick ; EMVA 1288) ------------------
# Sur un aplat, variance = K * signal + variance de lecture (+ 1/12 de quantification).
# La pente doit redonner K, l'ordonnee a l'origine le bruit de lecture.
for iso in (100, 800, 3200):
    sim = CameraSim(PROFILE, iso=iso, read_noise_model="gauss", row_noise_gain=0.0, color_bias_gain=0.0)
    sn = sim.sensor
    means, variances = [], []
    for lvl in np.geomspace(0.002, 0.6, 12):
        a = sim.raw(flat(lvl), frame=1)[1::2, 0::2]      # photosites verts (G2)
        b = sim.raw(flat(lvl), frame=2)[1::2, 0::2]
        means.append(0.5 * (a.mean() + b.mean()) - sn.black)
        variances.append(0.5 * (a - b).var())            # la difference de deux images elimine le motif fixe
    slope, intercept = np.polyfit(means, variances, 1)
    check("transfert de photons ISO %d : gain K" % iso, abs(slope / sn.K - 1) < 0.02,
          "mesure %.4f DN/e-, attendu %.4f" % (slope, sn.K))
    # Bruit de lecture : mesure directe dans le noir (difference de deux images noires).
    d1, d2 = sim.raw(flat(0.0), frame=1), sim.raw(flat(0.0), frame=2)
    read_meas = math.sqrt(0.5 * (d1 - d2).var())
    read_th = math.sqrt(sn.read_gauss_sigma ** 2 + 1.0 / 12.0)
    check("image noire ISO %d : bruit de lecture" % iso, abs(read_meas / read_th - 1) < 0.02,
          "mesure %.3f DN, attendu %.3f DN (soit %.1f e-)" % (read_meas, read_th, sn.read_gauss_sigma / sn.K))

# 5. Bruit de ligne ---------------------------------------------------------
sim = CameraSim(PROFILE, iso=3200, shot_noise=False, read_noise_model="off", color_bias_gain=0.0, quantize=False)
dn = sim.raw(flat(0.0, 1024), frame=3)       # image noire : seul le bruit de ligne reste
row_sd = dn.mean(axis=1).std()
check("bruit de ligne", abs(row_sd / sim.sensor.row_sigma - 1) < 0.08 and dn.std(axis=1).max() < 1e-9,
      "ecart-type entre lignes %.3f DN, attendu %.3f ; constant dans une ligne" % (row_sd, sim.sensor.row_sigma))

# 6. Coherence dans le temps -------------------------------------------------
sim = CameraSim(PROFILE, iso=1600, prnu_sigma=0.01)
img = flat(0.2, 256)
a1, a1b, a2 = sim.raw(img, 10), sim.raw(img, 10), sim.raw(img, 11)
check("meme image, meme resultat", np.array_equal(a1, a1b), "reproductible")
check("images differentes, bruit different", not np.array_equal(a1, a2), "le bruit change d'une image a l'autre")
simf = CameraSim(PROFILE, iso=1600, prnu_sigma=0.01, **CLEAN)
f1, f2 = simf.raw(img, 10), simf.raw(img, 11)
g = f1[0::2, 1::2] - simf.sensor.black
rel = (g / g.mean()).std()
check("motif fixe (PRNU)", np.array_equal(f1, f2) and abs(rel / 0.01 - 1) < 0.05,
      "identique d'une image a l'autre, ecart relatif %.4f pour 0.0100 demande" % rel)

# 7. Saturation ---------------------------------------------------------------
sim = CameraSim(PROFILE, iso=100, read_noise_model="off", row_noise_gain=0.0, color_bias_gain=0.0, shot_noise=False)
sat = sim.sensor.span / sim.sensor.dn_per_scene_unit
out = sim.process(flat(10.0, 64))
check("saturation du capteur", abs(out.max() / sat - 1) < 1e-3 and np.ptp(out) < 1e-6,
      "une scene a 10.0 sort a %.3f (saturation attendue %.3f), en blanc neutre" % (out.max(), sat))

# 8. Courant d'obscurite et temperature ---------------------------------------
f20, f28, f60 = (dark_current_temperature_factor(t) for t in (20.0, 28.0, 60.0))
dbl = 8.0 / math.log2(f28)
check("courant d'obscurite : loi de temperature", abs(f20 - 1) < 1e-12 and 5.0 < dbl < 10.0,
      "double tous les %.1f degres pres de 20 C ; x%.0f a 60 C" % (dbl, f60))
sim = CameraSim(PROFILE, iso=3200, read_noise_model="off", row_noise_gain=0.0, color_bias_gain=0.0,
                dark_current_e_per_s=5.0, dsnu_rel_sigma=0.0, exposure_time_s=2.0, temperature_c=20.0, quantize=False)
dn = sim.raw(flat(0.0, 512), 1)
e = (dn - sim.sensor.black) / sim.sensor.K
check("courant d'obscurite : electrons", abs(e.mean() / 10.0 - 1) < 0.01 and abs(e.var() / 10.0 - 1) < 0.02,
      "moyenne %.2f e-, variance %.2f (attendu 10 et 10 : loi de Poisson)" % (e.mean(), e.var()))

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
