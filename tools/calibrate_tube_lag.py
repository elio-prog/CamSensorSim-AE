"""Confronte le modele de tube aux mesures de remanence publiees, et regle le parametre g.

Mesures de reference :
  Plumbicon XQ1410 (manuel Mullard 1987, fiche XQ1410, "low key conditions") :
    signal 20 nA, faisceau 300 nA, lumiere de polarisation 3 nA
    remanence a la coupure : 7 % a 60 ms, 2 % a 200 ms ; montee : 98 % a 60 ms.
  Vidicon XQ1031 (manuel Philips T12, 1987) :
    signal 200 nA, courant d'obscurite 20 nA : 21 % a 60 ms.
  Vidicon XQ1240 (meme manuel, notes generales) : 8 % a 200 ms.

Definition (Mullard, notes generales 2.4) : cible eclairee au moins 5 s, puis lumiere
coupee ; courant residuel mesure 60 ms et 200 ms apres, a 50 Hz (3e et 10e trame).

Usage : python tools/calibrate_tube_lag.py
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim.tube import TubeTarget                    # noqa: E402


def base(**kw):
    p = dict(g_per_nA=0.05, white_nA=300.0, beam_nA=600.0, bias_nA=0.0, dark_nA=0.0, gamma=1.0,
             photo_lag=0.0, photo_lag_fields=5.0, noise_nA=0.0, noise_triangular=1.0, bloom_px=0.0,
             shading=0.0, spot_px=0.0, burn_gain=0.0, burn_seconds=30.0, temperature_c=30.0,
             dark_ref_c=30.0, dark_doubling_c=7.5, u_max=150.0)
    p.update(kw)
    return p


def decay(p, signal_nA, n_on=400, n_off=12):
    """Courant residuel (fraction du signal) a chaque trame apres la coupure de la lumiere."""
    tgt = TubeTarget(p, 0)
    on = np.full((1, 1), signal_nA / p["white_nA"])
    off = np.zeros((1, 1))
    for _ in range(n_on):
        steady = tgt.expose_field(on)[0, 0]
    return steady, [tgt.expose_field(off)[0, 0] / steady for _ in range(n_off)]


def buildup(p, signal_nA, n_dark=600, n_on=12):
    tgt = TubeTarget(p, 0)
    for _ in range(n_dark):
        tgt.expose_field(np.zeros((1, 1)))
    on = np.full((1, 1), signal_nA / p["white_nA"])
    return [tgt.expose_field(on)[0, 0] / signal_nA for _ in range(n_on)]


def solve_g(target, fn, lo=1e-4, hi=5.0):
    """Cherche g tel que fn(g) = target (fn decroissante en g), par dichotomie en log."""
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        if fn(mid) > target:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


print("=== Plumbicon XQ1410, signal 20 nA, faisceau 300 nA, polarisation 3 nA ===")
# 1. Prediction a partir de la physique seule, sans rien regler.
#    g = a * T_trame / C ; a = e / kT avec T_faisceau = 3000 K ; C = eps0 * eps_r * S / d.
a = 1.0 / (8.617e-5 * 3000.0)                              # 1 / volt
area = 12.8e-3 * 17.1e-3                                    # surface balayee d'un tube de 30 mm
for eps_r, d in ((12.0, 14e-6), (12.0, 10e-6), (12.0, 18e-6)):
    C = 8.854e-12 * eps_r * area / d
    g_phys = a * 0.02 * 1e-9 / C
    _, r = decay(base(g_per_nA=g_phys, beam_nA=300.0, bias_nA=3.0), 20.0)
    print("physique seule : couche %2.0f um, C = %.2f nF, g = %.4f /nA -> %.1f %% a 60 ms, %.1f %% a 200 ms"
          % (d * 1e6, C * 1e9, g_phys, 100 * r[2], 100 * r[9]))
# 2. Reglage de g sur la mesure a 60 ms, puis verification sur les deux autres mesures.
g_pl = solve_g(0.07, lambda g: decay(base(g_per_nA=g, beam_nA=300.0, bias_nA=3.0), 20.0)[1][2])
_, r = decay(base(g_per_nA=g_pl, beam_nA=300.0, bias_nA=3.0), 20.0)
b = buildup(base(g_per_nA=g_pl, beam_nA=300.0, bias_nA=3.0), 20.0)
print("g regle sur 7 %% a 60 ms : g = %.4f /nA" % g_pl)
print("  verification : %.1f %% a 200 ms (mesure : 2 %%) ; montee %.0f %% a 60 ms (mesure : 98 %%)" % (100 * r[9], 100 * b[2]))
_, r0 = decay(base(g_per_nA=g_pl, beam_nA=300.0, bias_nA=0.0), 20.0)
print("  sans lumiere de polarisation : %.1f %% a 60 ms (elle doit reduire la remanence : oui si > 7)" % (100 * r0[2]))
_, rw = decay(base(g_per_nA=g_pl, beam_nA=600.0, bias_nA=3.0), 300.0)
print("  au blanc (300 nA, faisceau 600) : %.2f %% a 60 ms (attendu : tres faible)" % (100 * rw[2]))

print("\n=== Vidicon XQ1031, signal 200 nA, obscurite 20 nA, faisceau regle a 2 fois le signal ===")
# Decharge seule : g deduit de la geometrie (couche de 2 a 3 um, 9,6 x 12,8 mm, eps_r suppose 10).
C_v = 8.854e-12 * 10.0 * (9.6e-3 * 12.8e-3) / 2.5e-6
g_v = a * 0.02 * 1e-9 / C_v
_, r = decay(base(g_per_nA=g_v, beam_nA=400.0, dark_nA=20.0, white_nA=200.0, gamma=0.74), 200.0)
print("decharge seule : C = %.1f nF, g = %.4f /nA -> %.1f %% a 60 ms, %.1f %% a 200 ms" % (C_v * 1e9, g_v, 100 * r[2], 100 * r[9]))
# Le manuel dit qu'aux forts signaux la remanence photoconductrice domine : on la regle sur les mesures.
best = None
for frac in np.linspace(0.2, 0.9, 36):
    for tau in np.linspace(1.5, 12.0, 43):
        _, r = decay(base(g_per_nA=g_v, beam_nA=400.0, dark_nA=20.0, white_nA=200.0, gamma=0.74,
                          photo_lag=frac, photo_lag_fields=tau), 200.0)
        err = (r[2] - 0.21) ** 2 + (r[9] - 0.08) ** 2
        if best is None or err < best[0]:
            best = (err, frac, tau, r[2], r[9])
print("remanence photoconductrice reglee : part piegee %.2f, constante %.1f trames -> %.1f %% a 60 ms (mesure 21), %.1f %% a 200 ms (mesure 8)"
      % (best[1], best[2], 100 * best[3], 100 * best[4]))
print("\nVALEURS A REPORTER : plumbicon g_per_nA = %.4f ; vidicon g_per_nA = %.4f, photo_lag = %.2f, photo_lag_fields = %.1f"
      % (g_pl, g_v, best[1], best[2]))
