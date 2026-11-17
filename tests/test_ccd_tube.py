"""Tests des capteurs CCD et des tubes, contre la theorie et contre les fiches techniques.

Usage : python tests/test_ccd_tube.py   (depuis le dossier CameraSim_AE)
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim, load_profile          # noqa: E402
from camsim import ccd                              # noqa: E402
from camsim.tube import (TubeTarget, beam_scan, f_landing, gaussian_blur,   # noqa: E402
                         spot_sigma_from_modulation, triangular_noise)

results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


def flat(v, n=128):
    return np.full((n, n, 3), v, dtype=np.float64)


CCD = "kai0373_3ccd"
QUIET = dict(iso=100, shot_noise=False, read_noise_model="off", row_noise_gain=0.0, color_bias_gain=0.0,
             olpf_h_px=0.0, olpf_v_px=0.0, prnu_sigma=0.0, dark_current_e_per_s=0.0, quantize=False)

print("--- CCD ---")
# 1. Eblouissement : la charge en trop deborde dans la colonne, sans se perdre ni depasser le puits.
fw = 1000.0
e = np.full((201, 9), 100.0)
e[100, 4] = 40 * fw
out = ccd.blooming(e, fw, 1.0)
streak = int((out[:, 4] >= fw - 1e-6).sum())
check("eblouissement sans protection", abs(out.sum() - e.sum()) < 1e-3 * e.sum() and out.max() <= fw + 1e-9
      and np.allclose(out[:, :4], 100.0) and np.allclose(out[:, 4], out[::-1, 4]),
      "charge conservee, colonne saturee sur %d photosites, symetrique, colonnes voisines intactes" % streak)
out = ccd.blooming(e, fw, 300.0)
check("protection 300x (fiche KAI-0373)", out[100, 4] == fw and np.allclose(np.delete(out[:, 4], 100), 100.0),
      "40 fois la saturation : aucun debordement")
e2 = e.copy()
e2[100, 4] = 400 * fw
out = ccd.blooming(e2, fw, 300.0)
check("au-dela de la protection", (out[:, 4] >= fw - 1e-6).sum() > 50, "400 fois la saturation : la colonne deborde")

# 2. Trainee verticale : la definition de la fiche doit redonner la valeur de la fiche.
sim = CameraSim(CCD, exposure_time_s=0.033, **QUIET)
sn = sim.sensor
n = 200
scene = np.zeros((n, n, 3))
sat_scene = sn.span / sn.dn_per_scene_unit
scene[90:110] = 0.999 * sat_scene                      # tache blanche sur 10 % de la hauteur, juste sous la saturation
dn = sim.raw(scene, 1)[..., 1]
ratio = (dn[10, 50] - sn.black) / (dn[100, 50] - sn.black)
check("trainee verticale, conditions de la fiche", abs(ratio / 1e-4 - 1) < 0.02 and abs(dn[10, 50] - dn[190, 50]) < 1e-9,
      "trainee = %.4f %% du blanc (fiche : 0,01 %%), identique en haut et en bas" % (100 * ratio))
sim2 = CameraSim(CCD, exposure_time_s=0.033 / 33, **QUIET)
dn2 = sim2.raw(scene, 1)[..., 1]
ratio2 = (dn2[10, 50] - sn.black) / (dn2[100, 50] - sn.black)
check("trainee et obturateur electronique", abs(ratio2 / ratio / 33 - 1) < 0.02,
      "pose 33 fois plus courte : trainee relative x%.1f (la fiche : 'increase by the same factor')" % (ratio2 / ratio))
scene[90:110] = 500 * sat_scene                        # soleil dans le champ
dn3 = sim.raw(scene, 1)[..., 1]
check("trainee d'une source tres surexposee", (dn3[10, 50] - sn.black) / sn.span > 0.04,
      "500 fois la saturation : la colonne monte a %.0f %% du blanc" % (100 * (dn3[10, 50] - sn.black) / sn.span))

# 3. Transfert imparfait : un paquet a n transferts de la sortie garde exp(-n eps) de sa charge.
e = np.zeros((600, 5))
e[399, 2] = 1e5
for eps in (1e-5, 1e-3):
    out = ccd.transfer_inefficiency(e, eps, 0.0)
    lam = 400 * eps
    check("transfert imparfait, eps = %g" % eps,
          abs(out[399, 2] / 1e5 - math.exp(-lam)) < 1e-6 and abs(out[400, 2] / 1e5 - lam * math.exp(-401 * eps) * 401 / 400) < 2e-3
          and abs(out.sum() / 1e5 - 1) < 1e-3,
          "reste %.2f %% dans le paquet (theorie %.2f %%), %.2f %% dans le suivant, charge conservee"
          % (100 * out[399, 2] / 1e5, 100 * math.exp(-lam), 100 * out[400, 2] / 1e5))
dn = CameraSim(CCD, **QUIET).raw(flat(0.2), 1)
dn_bad = CameraSim(CCD, ccd_cti_v=0.0, ccd_cti_h=0.0, **QUIET).raw(flat(0.2), 1)
check("CTE de la fiche (0,99999)", np.abs(dn / dn_bad - 1).max() < 5e-3, "effet inferieur a 0,5 % sur 128 lignes : invisible, comme attendu")

# 4. Bruit et gain du profil CCD.
sim = CameraSim(CCD, iso=100, read_noise_model="gauss", prnu_sigma=0.0, dark_current_e_per_s=0.0, olpf_h_px=0, olpf_v_px=0)
sn = sim.sensor
d1, d2 = sim.raw(flat(0.0, 256), 1)[..., 1], sim.raw(flat(0.0, 256), 2)[..., 1]
read_e = math.sqrt(0.5 * (d1 - d2).var()) / sn.K
check("bruit de lecture du CCD", abs(read_e / 55.0 - 1) < 0.03 and abs(sn.full_well_e / 55000 - 1) < 1e-9,
      "%.1f e- mesures (fiche : 55 e-), puits %.0f e- (fiche : 55 000)" % (read_e, sn.full_well_e))

# 5. Trois capteurs : aller-retour exact, defaut d'alignement.
yy, xx = np.mgrid[0:128, 0:128] / 127.0
from camsim import color as C                        # noqa: E402
rec709 = np.stack([0.05 + 0.5 * xx, 0.05 + 0.5 * yy, 0.3 - 0.2 * xx * yy], axis=-1)
grad = C.apply_matrix(rec709, np.linalg.inv(C.AP1_TO_SRGB))   # couleurs que la camera Rec.709 peut voir
clean = dict(QUIET, ccd_smear_gain=0.0, ccd_cti_v=0.0, ccd_cti_h=0.0, clip_full_well=False, clip_highlights=False)
err = np.abs(CameraSim(CCD, **clean).process(grad) - grad).max()
check("trois capteurs, aller-retour sans defaut", err < 1e-9, "erreur max %.1e" % err)
bar = np.zeros((64, 64, 3))
bar[:, 30:34] = 0.3
shifted = CameraSim(CCD, registration_r_dx=1.5, **clean)._to_camera(bar)
cx = lambda p: (p.sum(axis=0) * np.arange(64)).sum() / p.sum()
check("defaut d'alignement du rouge", abs(cx(shifted[..., 0]) - cx(shifted[..., 1]) - 1.5) < 0.02,
      "le plan rouge est decale de %.2f px par rapport au vert" % (cx(shifted[..., 0]) - cx(shifted[..., 1])))

# 6. Lecture entrelacee par addition de deux lignes.
ramp = np.repeat((0.05 + 0.4 * np.arange(128) / 127.0)[:, None, None], 128, axis=1) * np.ones(3)
sim = CameraSim(CCD, interlace="fields", **clean)
out = sim.process(ramp)[..., 1]
g = ramp[..., 1]
exp_even = 0.5 * (g[0::2, 0] + g[1::2, 0])
exp_odd = 0.5 * (g[1:-1:2, 0] + g[2::2, 0])
check("lecture entrelacee", np.abs(out[0::2, 0] - exp_even).max() < 1e-9 and np.abs(out[1:-1:2, 0] - exp_odd).max() < 1e-9,
      "chaque ligne de sortie = moyenne de deux lignes du capteur, decalees d'une ligne entre les deux trames")

print("--- Tubes ---")
PL = load_profile("plumbicon_xq1410")["tube"]
VI = load_profile("vidicon_xq1031")["tube"]


def params(base, **kw):
    p = dict(base, burn_gain=0.0, burn_seconds=30.0, temperature_c=30.0, spot_px=0.0, noise_nA=0.0,
             bloom_px=0.0, shading=0.0)
    p.update(kw)
    return p


def run(p, levels, n=1):
    """Suite des courants de sortie pour une suite d'eclairements (un element de cible)."""
    t = TubeTarget(p, 0)
    return [t.expose_field(np.full((n, n), lv))[0, 0] for lv in levels]


# 7. Resolution de l'equation du faisceau.
u = np.linspace(-12, 140, 4001)
for beta in (0.5, 5.0, 35.0):
    ua = beam_scan(u, beta)
    res = np.abs(f_landing(ua) - (f_landing(u) - beta)).max()
    check("equation du faisceau, beta = %g" % beta, res < 1e-8 and np.all(ua <= u), "residu max %.1e" % res)

# 8. Regime etabli : le tube rend exactement le signal recu, tant que le faisceau suffit.
for base, name in ((PL, "Plumbicon"), (VI, "vidicon")):
    p = params(base, gamma=1.0, photo_lag=0.0)
    lv = 0.5
    out = run(p, [lv] * 300)[-1]
    check("regime etabli (%s)" % name, abs(out / (lv * p["white_nA"]) - 1) < 1e-6, "%.3f nA rendus pour %.3f recus" % (out, lv * p["white_nA"]))

# 9. Limite du faisceau et trainee de comete.
p = params(PL, gamma=1.0, photo_lag=0.0)
seq = run(p, [4.0] * 5 + [0.0] * 12)                     # 5 trames a 4 fois le blanc (600 nA, faisceau 300), puis le noir
check("limite du faisceau", abs(seq[4] / p["beam_nA"] - 1) < 0.02, "600 nA recus, %.0f nA rendus : le faisceau de 300 nA sature" % seq[4])
tail = [x for x in seq[5:] if x > 0.5 * p["beam_nA"]]
check("trainee de comete", len(tail) >= 3 and seq[-1] < 0.05 * p["white_nA"],
      "apres la coupure, le blanc persiste %d trames (%d ms) avant de retomber" % (len(tail), 20 * len(tail)))
seq2 = run(params(PL, gamma=1.0, photo_lag=0.0, beam_nA=1200.0), [4.0] * 5 + [0.0] * 12)
check("faisceau renforce", seq2[5] < 0.2 * 600, "avec un faisceau de 1200 nA, pas de trainee (%.0f nA a la 1re trame noire)" % seq2[5])

# 10. Remanence : comparaison aux fiches techniques (definition Mullard : 3e et 10e trame apres la coupure).
p = params(PL, beam_nA=300.0, bias_nA=3.0)
lv = (20.0 / p["white_nA"]) ** (1 / p["gamma"])
seq = run(p, [lv] * 400 + [0.0] * 10)
r60, r200 = seq[402] / seq[399], seq[409] / seq[399]
check("remanence du Plumbicon en basse lumiere", 0.06 < r60 < 0.09 and 0.01 < r200 < 0.03,
      "%.1f %% a 60 ms (Mullard : 7 %%), %.1f %% a 200 ms (Mullard : 2 %%)" % (100 * r60, 100 * r200))
seq = run(p, [0.0] * 600 + [lv] * 3)
check("montee du Plumbicon", 0.95 < seq[602] / 20.0 < 1.0, "%.0f %% a 60 ms (Mullard : 98 %%)" % (100 * seq[602] / 20.0))
seq = run(params(PL, beam_nA=300.0, bias_nA=0.0), [lv] * 400 + [0.0] * 3)
check("effet de la lumiere de polarisation", seq[402] / seq[399] > 1.5 * r60,
      "sans polarisation : %.1f %% ; avec 3 nA : %.1f %%" % (100 * seq[402] / seq[399], 100 * r60))
seq = run(p, [1.0] * 400 + [0.0] * 3)
check("remanence plus faible en pleine lumiere", seq[402] / seq[399] < 0.5 * r60,
      "au blanc (150 nA) : %.1f %% ; a 20 nA : %.1f %%" % (100 * seq[402] / seq[399], 100 * r60))
pv = params(VI)
seq = run(pv, [1.0] * 500 + [0.0] * 10)
r60, r200 = seq[502] / seq[499], seq[509] / seq[499]
check("remanence du vidicon", 0.19 < r60 < 0.23 and 0.06 < r200 < 0.10,
      "%.1f %% a 60 ms (Philips : 21 %%), %.1f %% a 200 ms (Philips : 8 %%)" % (100 * r60, 100 * r200))

# 11. Gamma de la couche.
pv0 = params(VI, photo_lag=0.0)
a, b = run(pv0, [0.2] * 400)[-1], run(pv0, [0.8] * 400)[-1]
gam = math.log(b / a) / math.log(4.0)
check("gamma du vidicon", abs(gam - 0.74) < 0.005, "pente mesuree %.3f (Philips : 0,74)" % gam)

# 12. Courant d'obscurite et temperature.
d30 = run(params(VI, photo_lag=0.0), [0.0] * 5)
t = TubeTarget(params(VI, temperature_c=37.5), 0)
u0 = t.expose_field(np.zeros((1, 1)))
check("niveau noir recale", abs(d30[-1]) < 1e-6 and abs(u0[0, 0]) < 1e-6, "le courant d'obscurite (20 nA, 40 nA a 37,5 C) est retire par la camera")

# 13. Tache du faisceau : contraste a 400 lignes TV.
H = 576
sig = spot_sigma_from_modulation(H, 0.55)
yy = np.arange(H)[:, None] * np.ones((1, 64))
wave = 1.0 + 0.5 * np.cos(2 * np.pi * (200.0 / H) * yy)
blurred = gaussian_blur(wave, sig)
m = (blurred[100:-100].max() - blurred[100:-100].min()) / 1.0
check("resolution du Plumbicon", abs(m - 0.55) < 0.005, "contraste restant a 400 lignes TV : %.3f (Mullard : 0,55)" % m)

# 14. Bruit du preamplificateur : spectre qui monte avec la frequence.
nz = triangular_noise(np.random.default_rng(0), (256, 1024), 2.0, 1.0)
ps = (np.abs(np.fft.rfft(nz, axis=1)) ** 2).mean(axis=0)
lo, hi = ps[1:100].mean(), ps[-100:].mean()
check("bruit triangulaire", abs(nz.std() - 2.0) < 1e-6 and hi / lo > 30, "ecart-type %.2f nA ; %.0f fois plus de puissance dans les hautes frequences" % (nz.std(), hi / lo))

# 15. Camera complete : un gris stable ressort a sa valeur.
for prof in ("plumbicon_xq1410", "vidicon_xq1031"):
    sim = CameraSim(prof, tube_noise_nA=0.0, tube_spot_px=0.0, tube_shading=0.0)
    for k in range(120):
        out = sim.process(flat(0.18, 16), k)
    check("camera complete (%s)" % prof, np.abs(out / 0.18 - 1).max() < 0.01, "un gris de 0.180 ressort a %.4f" % out.mean())

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
