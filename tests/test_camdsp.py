"""Tests de l'etage video (coude, accentuation, couleurs dans les blancs) et des defauts de la fiche Sony.

Usage : python tests/test_camdsp.py   (depuis le dossier CameraSim_AE)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim, camdsp, load_profile        # noqa: E402
from camsim.sources import bt709_decode, bt709_encode, REC709_TO_AP1, AP1_TO_REC709   # noqa: E402
from camsim.color import apply_matrix                      # noqa: E402

results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


P = dict(enabled=True, knee_point=0.85, knee_slope=0.25, white_clip=1.07, detail_h=0.6, detail_v=0.4,
         detail_coring=0.01, detail_limit=0.12, highlight_chroma=0.8)


def lin709(v):
    """Niveau video BT.709 -> image ACEScg grise."""
    return apply_matrix(np.broadcast_to(bt709_decode(np.asarray(v, dtype=float))[..., None], np.shape(v) + (3,)), REC709_TO_AP1)


def video(ap1):
    return bt709_encode(np.maximum(apply_matrix(ap1, AP1_TO_REC709), 0.0))


# 1. Coude : sous le point, rien ne change ; au-dessus, la pente est divisee.
v = np.array([0.5, 0.85, 1.25])
k = camdsp.knee(v, 0.85, 0.25)
check("coude", np.allclose(k, [0.5, 0.85, 0.95]), "0,50 -> %.2f ; 0,85 -> %.2f ; 1,25 -> %.2f (85 %% + 40 %% x 0,25)" % tuple(k))

# 2. Un aplat gris sous le coude traverse l'etage sans changer.
flat = lin709(np.full((32, 32), 0.45))
out = camdsp.apply(flat, P)
check("aplat inchange", np.allclose(out, flat, atol=1e-12), "ecart %.1e" % np.abs(out - flat).max())

# 3. Accentuation : un contour vertical prend un halo clair d'un cote, sombre de l'autre, borne par le limiteur.
edge = np.full((32, 64), 0.30)
edge[:, 32:] = 0.60
o = video(camdsp.apply(lin709(edge), P))[16, :, 1]
over, under = o[32:36].max() - 0.60, 0.30 - o[28:32].min()
check("halos de contour", over > 0.05 and under > 0.05 and over <= 0.12 + 1e-9 and under <= 0.12 + 1e-9
      and abs(o[5] - 0.30) < 1e-9 and abs(o[60] - 0.60) < 1e-9,
      "depassement %.3f au-dessus, %.3f au-dessous (limite 0,12) ; loin du bord, rien" % (over, under))

# 4. Seuil : un petit detail (bruit) n'est pas accentue.
small = 0.45 + 0.003 * np.where(np.arange(64) % 4 < 2, 1.0, -1.0)[None, :] * np.ones((32, 1))
o = video(camdsp.apply(lin709(small), P))[..., 1]
check("seuil des details", np.allclose(o, small, atol=1e-9), "detail de 0,3 % : inchange (seuil 1 %)")

# 5. Hautes lumieres : la couleur tend vers le blanc au-dessus du coude, pas en dessous.
def sat(ap1):
    v_ = video(ap1)
    return float((v_.max(-1) - v_.min(-1)).mean())


red_low = apply_matrix(np.broadcast_to(np.array([0.30, 0.08, 0.08]), (8, 8, 3)).copy(), REC709_TO_AP1)
red_high = red_low * 6.0
check("couleurs dans les blancs", abs(sat(camdsp.apply(red_low, P)) - sat(red_low)) < 1e-9 and
      sat(camdsp.apply(red_high, P)) < 0.5 * sat(red_high),
      "rouge sous le coude : couleur gardee ; 6 fois plus fort : ecart entre voies %.2f -> %.2f"
      % (sat(red_high), sat(camdsp.apply(red_high, P))))

# 6. Ecretage du blanc.
o = video(camdsp.apply(lin709(np.full((8, 8), 3.0)), P))
check("ecretage du blanc", abs(o.max() - 1.07) < 1e-9, "niveau maximal %.3f (107 %%)" % o.max())

# 7. Le camescope a l'etage actif, les autres non (resultats d'avant inchanges).
check("etage actif selon le profil", CameraSim("icx409ak_camcorder").dsp["enabled"] and not CameraSim("sonya7s2").dsp["enabled"]
      and CameraSim("sonya7s2", dsp_enabled="on").dsp["enabled"] and not CameraSim("icx409ak_camcorder", dsp_enabled="off").dsp["enabled"],
      "camescope : actif ; appareils photo : inactif ; reglable dans les deux sens")

# 8. Ombrage : aux coins, la sensibilite baisse de la valeur reglee (definition de la fiche Sony).
Q = dict(iso=1250, shot_noise=False, read_noise_model="off", olpf_h_px=0.0, olpf_v_px=0.0, prnu_sigma=0.0,
         dark_current_e_per_s=0.0, quantize=False, ccd_smear_gain=0.0, ccd_cti_v=0.0, ccd_cti_h=0.0,
         clip_highlights=False, dsp_enabled="off")
sim = CameraSim("kai0373_3ccd", sensor_shading=0.2, **dict(Q, iso=100))
dn = sim.raw(np.full((101, 101, 3), 0.18), 1)[..., 1] - sim.sensor.black
check("ombrage du capteur", abs(dn[0, 0] / dn[50, 50] - 0.8) < 1e-9 and abs(dn[50, 0] / dn[50, 50] - 0.9) < 1e-9,
      "coin / centre = %.3f (reglage 0,20) ; bord / centre = %.3f" % (dn[0, 0] / dn[50, 50], dn[50, 0] / dn[50, 50]))

# 9. Remanence : apres une image claire, une image noire garde une part de la charge.
sim = CameraSim("kai0373_3ccd", ccd_lag=0.05, **dict(Q, iso=100))
sim.raw(np.full((32, 32, 3), 0.18), 1)
dn_dark = sim.raw(np.zeros((32, 32, 3)), 2)[..., 1] - sim.sensor.black
dn_ref = CameraSim("kai0373_3ccd", **dict(Q, iso=100)).raw(np.full((32, 32, 3), 0.18), 1)[..., 1] - sim.sensor.black
check("remanence", abs(dn_dark.mean() / dn_ref.mean() - 0.05) < 1e-9, "image noire apres une image claire : %.3f du signal (reglage 0,05)"
      % (dn_dark.mean() / dn_ref.mean()))

# 10. Papillotement entre trames et rampement de lignes du camescope.
grey = np.full((64, 64, 3), 0.18)
y = lambda img: (img @ np.array([0.2126, 0.7152, 0.0722]))[8:-8, 8:-8]
o = CameraSim("icx409ak_camcorder", cmyg_field_flicker=0.02, **Q).process(grey, 1, second_field=grey)
ratio = y(o)[1::2].mean() / y(o)[0::2].mean()
check("ecart entre trames", abs(ratio - 1.02) < 0.002, "trame B / trame A = %.4f (reglage 0,02)" % ratio)
o = CameraSim("icx409ak_camcorder", cmyg_line_crawl=0.03, **Q).process(grey, 1, second_field=grey)
rows = y(o).mean(axis=1)
r = rows[0::4].mean() / rows[2::4].mean()
# Definition de la fiche Sony (methode 10) : Lc = ecart de luminance entre lignes d'une meme trame / signal.
check("ecart entre lignes", abs(abs(r - 1.0) - 0.03) < 0.003,
      "ecart de luminance entre lignes rouges et bleues : %.2f %% (reglage 0,03, definition de la fiche)" % (100 * abs(r - 1.0)))

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
