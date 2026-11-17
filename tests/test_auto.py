"""Tests des automatismes (exposition, gain, balance des blancs).

Usage : python tests/test_auto.py   (depuis le dossier CameraSim_AE)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim                          # noqa: E402

results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


Q = dict(iso=800, shot_noise=False, read_noise_model="off", row_noise_gain=0.0, color_bias_gain=0.0, prnu_sigma=0.0,
         dark_current_e_per_s=0.0, quantize=False, olpf_h_px=0.0, olpf_v_px=0.0, clip_highlights=False,
         clip_full_well=False, fps=25)
flat = lambda v, n=64: np.full((n, n, 3), v, dtype=np.float64)


def run(levels, **kw):
    sim = CameraSim("sonya7s2", **dict(Q, **kw))
    xs, means = [], []
    for k, L in enumerate(levels):
        img = flat(L) if np.isscalar(L) else L
        o, _ = sim.process_frame(img, k)
        xs.append(sim.auto.x)
        means.append(float(o.mean()))
    return sim, np.array(xs), np.array(means)


# 1. Automatismes coupes : resultat identique a avant.
a = CameraSim("sonya7s2", seed=2).process_frame(flat(0.2), 1)[0]
b = CameraSim("sonya7s2", seed=2, auto_exposure=False, auto_wb=False).process_frame(flat(0.2), 1)[0]
check("automatismes coupes", np.array_equal(a, b), "identique bit a bit")

# 2. Echelon de lumiere x4 : la camera revient vers le gris moyen, avec une image de retard.
levels = [0.18] * 10 + [0.72] * 140
sim, xs, means = run(levels, auto_exposure=True, ae_deadband_ev=0.0)
check("exposition automatique", abs(xs[-1] + 2.0) < 0.02 and abs(means[-1] - 0.18) < 0.004,
      "apres l'echelon : %.2f diaphragmes (attendu -2), luminosite %.3f (visee 0,18)" % (xs[-1], means[-1]))
check("une image de retard", abs(means[10] - 0.72) < 0.005 and means[11] < means[10],
      "image du changement : %.3f (pas encore corrigee) ; suivante : %.3f" % (means[10], means[11]))

# 3. Amortissement : 1 = pas de depassement ; 0,2 = la camera depasse et revient (pompage).
_, x1, _ = run(levels, auto_exposure=True, ae_deadband_ev=0.0, ae_damping=1.0)
_, x2, _ = run(levels, auto_exposure=True, ae_deadband_ev=0.0, ae_damping=0.2, ae_max_rate_ev_s=20)
over1, over2 = -2.0 - x1.min(), -2.0 - x2.min()
swings = int(np.sum(np.diff(np.sign(np.diff(x2[10:]))) != 0))
check("amortissement", over1 < 0.02 and over2 > 0.4 and swings >= 3,
      "depassement %.2f diaph. (amortissement 1) contre %.2f (0,2), %d allers-retours" % (over1, over2, swings))

# 4. Zone morte : un petit changement de lumiere ne fait pas reagir la camera.
_, x3, _ = run([0.18] * 10 + [0.18 * 2 ** 0.1] * 40, auto_exposure=True, ae_deadband_ev=0.15)
check("zone morte", np.abs(x3).max() == 0.0, "lumiere +0,1 diaph. avec une tolerance de 0,15 : aucune correction")

# 5. Dans le noir : diaphragme en butee, puis gain (plus de bruit).
sim, xs, _ = run([0.18 / 32] * 150, auto_exposure=True, ae_deadband_ev=0.0)
iris, gain = sim.auto.split()
check("gain automatique", abs(iris - 2.0) < 1e-9 and abs(gain - 3.0) < 0.05 and sim.sensor.iso > 800 * 7.5,
      "diaphragme %.1f (butee +2), gain %.2f diaph. (%.1f dB), ISO effectif %.0f" % (iris, gain, gain * 6.02, sim.sensor.iso))
sim, xs, means = run([0.18 / 512] * 150, auto_exposure=True, ae_deadband_ev=0.0)
check("gain plafonne", abs(sim.auto.split()[1] - 18 / 6.0206) < 1e-6 and means[-1] < 0.05,
      "tres sombre : gain plafonne a 18 dB, image %.3f (reste sombre)" % means[-1])

# 6. Mesure "peak" : une petite source tres claire fait baisser l'exposition bien plus que la mesure moyenne.
img = flat(0.18)
img[28:36, 28:36] = 20.0
_, xa, _ = run([img] * 120, auto_exposure=True, ae_deadband_ev=0.0, ae_metering="average")
_, xp, _ = run([img] * 120, auto_exposure=True, ae_deadband_ev=0.0, ae_metering="peak")
check("mesures", xp[-1] < xa[-1] - 2.0, "moyenne : %.2f diaph. ; hautes lumieres : %.2f diaph." % (xa[-1], xp[-1]))

# 7. Balance des blancs automatique sous une lumiere orangee.
warm = flat(0.18) * np.array([1.6, 1.0, 0.6])
sim, _, _ = run([warm] * 150, auto_wb=True, awb_deadband=0.0)
o = sim.process_frame(warm, 151)[0].reshape(-1, 3).mean(0)
sim7, _, _ = run([warm] * 150, auto_wb=True, awb_deadband=0.0, awb_strength=0.7)
o7 = sim7.process_frame(warm, 151)[0].reshape(-1, 3).mean(0)
check("balance des blancs automatique", abs(o[0] / o[1] - 1) < 0.02 and abs(o[2] / o[1] - 1) < 0.02,
      "lumiere orangee (R/V 1,6 ; B/V 0,6) corrigee : R/V %.3f, B/V %.3f" % (o[0] / o[1], o[2] / o[1]))
check("correction partielle", 1.05 < o7[0] / o7[1] < 1.4 and 0.65 < o7[2] / o7[1] < 0.95,
      "part 0,7 : R/V %.2f, B/V %.2f (il reste de la chaleur)" % (o7[0] / o7[1], o7[2] / o7[1]))

# 8. Les tubes ne sont pas concernes.
try:
    CameraSim("vidicon_xq1031", auto_exposure=True).process_frame(flat(0.18), 0)
    msg = None
except ValueError as e:
    msg = str(e)
check("refus pour les tubes", msg is not None, msg or "aucune erreur")

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
