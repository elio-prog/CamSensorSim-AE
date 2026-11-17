"""Tests de la couleur spectrale (spectres reconstruits + sensibilites mesurees des capteurs).

Usage : python tests/test_spectral.py   (depuis le dossier CameraSim_AE)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim, load_profile, spectral as sp   # noqa: E402
from camsim.color import AP1_TO_XYZ_D65, XYZ_D65_TO_AP1, CameraColor   # noqa: E402

results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


def lab(xyz, white):
    t = xyz / white
    f = np.where(t > (6 / 29) ** 3, np.cbrt(t), t / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


CAMS = {"sonya7s2": "Sony_ILCE-7SM2_380_780_5.json", "nikond850": "Nikon_D850_380_780_5.json",
        "canoneos5d4": "Canon_EOS_5D_Mark_IV_380_780_5.json"}

# 1. Donnees de reference : le blanc D65 calcule avec l'observateur CIE 1931 tombe sur (0,3127 ; 0,3290).
cmf, d65 = sp.load_cmf(), sp.load_d65()
xyz = d65 @ cmf
xy = xyz[:2] / xyz.sum()
check("donnees CIE", abs(xy[0] - 0.3127) < 5e-4 and abs(xy[1] - 0.3290) < 5e-4, "blanc D65 : x %.4f, y %.4f" % tuple(xy))

# 2. Reconstruction des spectres (Jakob et Hanika 2019) : couleur retrouvee, gris plat, couleur saturee etroite.
S = sp.Spectral(CAMS["sonya7s2"])
check("ajustement des spectres", np.percentile(S.fit_err, 99) < 0.01 and S.fit_err.max() < 0.05,
      "erreur sur la grille : 99 %% des couleurs a moins de %.2f %%, pire %.1f %% (bord du domaine)"
      % (100 * np.percentile(S.fit_err, 99), 100 * S.fit_err.max()))
grey_spec = sp.spectrum(S.coef[1, -1, -1])
check("spectre d'un gris", np.ptp(grey_spec) < 0.01 * grey_spec.mean(), "spectre plat a %.2f %% pres" % (100 * np.ptp(grey_spec) / grey_spec.mean()))
k = int(round(0.15 * (sp.GRID - 1)))                       # rouge sature (V et B a 15 % du R), pas le rouge limite d'ACEScg
red_spec = sp.spectrum(S.coef[0, k, k])
check("spectre d'un rouge sature", red_spec[-20] > 10 * red_spec[20] and S.fit_err[0, k, k] < 0.01,
      "%.2f a 680 nm contre %.3f a 480 nm ; couleur retrouvee a %.2f %% pres" % (red_spec[-20], red_spec[20], 100 * S.fit_err[0, k, k]))

# 3. Balance des blancs : les courbes mesurees redonnent celle des matrices Adobe (sources independantes).
worst = 0.0
for prof, f in CAMS.items():
    a = CameraColor(load_profile(prof)["xyz_d65_to_cam"]).cam_white
    b = sp.Spectral(f).white_raw
    worst = max(worst, float(np.abs(b / a - 1).max()))
check("blanc mesure contre matrices Adobe", worst < 0.07, "ecart maximal %.1f %% sur les trois appareils mesures" % (100 * worst))

# 4. Un gris traverse la camera spectrale sans teinte.
Q = dict(iso=800, shot_noise=False, read_noise_model="off", row_noise_gain=0.0, color_bias_gain=0.0, quantize=False,
         clip_full_well=False, clip_highlights=False, olpf_h_px=0.0, olpf_v_px=0.0)
o = CameraSim("sonya7s2", **Q).process(np.full((32, 32, 3), 0.18), 1)[8:-8, 8:-8]
check("gris neutre", np.abs(o / 0.18 - 1).max() < 0.005, "gris 0,18 -> %s" % np.round(o.reshape(-1, 3).mean(0), 4))

# 5. Les 190 couleurs de reference (eclairees par D65) : erreur de couleur d'un vrai appareil, faible mais non nulle.
refl = sp.load_training()
w = sp.Spectral._base["w_xyz"]
xyz_t = refl.T @ w                                      # (190, 3)
ap1_t = xyz_t @ XYZ_D65_TO_AP1.T
img = ap1_t.reshape(1, -1, 3)
white = np.ones(81) @ w
res = {}
for prof in CAMS:
    sim = CameraSim(prof, **dict(Q, demosaic="nearest"))
    cam = sim.scene_to_cam(img)
    out = sim.color.cam_wb_to_scene(cam * sim.color.wb_gains)
    de = np.linalg.norm(lab(out @ AP1_TO_XYZ_D65.T, white) - lab(xyz_t[None], white), axis=-1)[0]
    res[prof] = (float(de.mean()), float(np.percentile(de, 95)))
ok = all(0.1 < m < 4.0 and p < 10 for m, p in res.values())
check("190 couleurs de reference", ok, " ; ".join("%s : dE moyen %.2f, 95 %% sous %.1f" % (k, m, p) for k, (m, p) in res.items()))

# 6. Couleurs saturees : l'erreur grandit avec la saturation (limite physique d'une camera a 3 voies).
sim = CameraSim("sonya7s2", **Q)
sat = np.array([[0.02, 0.02, 0.30], [0.30, 0.02, 0.02], [0.02, 0.25, 0.02]])
neu = np.array([[0.20, 0.18, 0.17], [0.15, 0.16, 0.18], [0.12, 0.12, 0.12]])


def err(c):
    cam = sim.scene_to_cam(c[None])
    out = sim.color.cam_wb_to_scene(cam * sim.color.wb_gains)[0]
    return float(np.abs(out - c).max(axis=-1).mean() / c.max(axis=-1).mean())


check("erreur et saturation", err(sat) > 3 * err(neu), "erreur relative : %.1f %% (saturees), %.2f %% (presque neutres)"
      % (100 * err(sat), 100 * err(neu)))

# 7. Reglage : coupe = matrice d'avant (identique bit a bit) ; refus pour un CCD sans courbes.
a = CameraSim("sonya7s2", seed=2, spectral="off").process_frame(np.full((32, 32, 3), 0.2), 1)[0]
p = load_profile("sonya7s2")
for k in ("spectral_ssf", "spectral_default"):
    p.pop(k)
b = CameraSim(p, seed=2).process_frame(np.full((32, 32, 3), 0.2), 1)[0]
try:
    CameraSim("kai0373_3ccd", spectral="on")
    msg = None
except ValueError as e:
    msg = str(e)
check("reglage spectral", np.array_equal(a, b) and msg is not None, "coupe : identique a la matrice du profil ; CCD : %s" % msg)

# 8. Appareils voisins signales dans les profils.
check("appareils voisins signales", "VOISIN" in load_profile("canoneos70d")["spectral_source"] and
      "VOISIN" in load_profile("canoneos700d")["spectral_source"], "70D -> 200D, 700D -> 100D, notes dans les profils")

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
