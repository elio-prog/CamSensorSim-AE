"""Tests du camescope a un CCD (mosaique complementaire), contre la theorie des brevets.

Usage : python tests/test_camcorder.py   (depuis le dossier CameraSim_AE)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim                          # noqa: E402
from camsim import camcorder                          # noqa: E402
from camsim.sensor import mosaic_cmyg                 # noqa: E402

results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


CAM = "icx409ak_camcorder"
QUIET = dict(iso=1250, shot_noise=False, read_noise_model="off", olpf_h_px=0.0, olpf_v_px=0.0, prnu_sigma=0.0,
             dark_current_e_per_s=0.0, quantize=False, ccd_smear_gain=0.0, ccd_cti_v=0.0, ccd_cti_h=0.0,
             clip_highlights=False)


def flat_cam(rgb, h=64, w=64):
    return np.broadcast_to(np.asarray(rgb, dtype=np.float64), (h, w, 3)).copy()


R, G, B = 0.3, 0.5, 0.2

# 1. Mosaique : chaque photosite recoit la somme de deux primaires (ou le vert seul).
m = mosaic_cmyg(flat_cam((R, G, B), 8, 8))
want = np.array([[G + B, R + G], [G, R + B], [G + B, R + G], [R + B, G]])
check("mosaique Cy Ye / G Mg / Cy Ye / Mg G (fiche Sony)", np.allclose(m[:4, :2], want) and np.allclose(m[4:8, :2], want),
      "Cy = G+B, Ye = R+G, Mg = R+B (brevet US 5 136 370)")

# 2. Lumiere recueillie : un gris donne 1,75 fois plus de charge par photosite qu'une mosaique de Bayer.
mg = mosaic_cmyg(flat_cam((1.0, 1.0, 1.0), 8, 8))
check("sensibilite des filtres complementaires", abs(mg.mean() - 1.75) < 1e-12,
      "charge moyenne par photosite : %.2f (Bayer : 1) ; c'est la raison d'etre de ces filtres" % mg.mean())

# 3. Lecture par trames : sommes de lignes de la theorie, trame A et trame B.
sim = CameraSim(CAM, **QUIET)
sn = sim.sensor
RESP = np.asarray(sn.cmyg_response)
Rs = R * RESP[0]          # le capteur voit le rouge attenue
cam = flat_cam((R, G, B))
dn = sn.expose(cam, 1)
p = 2.0 * (dn - sn.black) / sn.dn_per_scene_unit       # sommes en unites RVB de la camera
Cy, Ye, Mg = G + B, Rs + G, Rs + B
blue_line = (Cy + Mg, Ye + G)
red_line = (Cy + G, Ye + Mg)
ok = True
for r in range(0, 16):
    want = red_line if (r // 2) % 2 == 0 else blue_line
    ok &= np.allclose(p[r, 0:2], want) and np.allclose(p[r, 2:4], want)
check("sommes de lignes (trames A et B)", ok,
      "lignes alternees (G+Cy)(Mg+Ye) puis (Mg+Cy)(G+Ye), comme dans la fiche Sony (lignes A1, A2)")
check("luminance identique sur toutes les lignes", np.allclose(p[:, 0] + p[:, 1], 2 * Rs + 3 * G + 2 * B),
      "S1 + S2 = 2R + 3G + 2B")

# 4. Separation : une couleur unie ressort exacte (filtres ideaux, sans bruit).
rgb = camcorder.separate(p)
inner = rgb[4:-4, 4:-4]
check("separation d'une couleur unie", np.allclose(inner, (Rs, G, B), atol=1e-12),
      "R, G, B retrouves : %s" % np.round(inner[10, 10], 6))

# 5. Couleur ligne par ligne : une limite horizontale rouge / bleu donne une ligne de couleur fausse.
h, w = 32, 32
img = np.zeros((h, w, 3))
img[:16] = (0.6, 0.1, 0.1)
img[16:] = (0.1, 0.1, 0.6)
sim_nf = CameraSim(CAM, **QUIET)
p2 = 2.0 * (sim_nf.sensor.expose(img, 1) - sim_nf.sensor.black) / sim_nf.sensor.dn_per_scene_unit
out = camcorder.separate(p2)
err = np.abs(out[:, 8:24] - (img * RESP)[:, 8:24]).max(axis=(1, 2))
bad = np.nonzero(err > 1e-6)[0]
check("retard 1H : couleur decalee d'une ligne", len(bad) > 0 and bad.min() >= 14 and bad.max() <= 18,
      "lignes fausses autour de la limite : %s ; partout ailleurs, couleur exacte" % bad.tolist())
out2 = camcorder.separate(p2, vertical="2H")
bad2 = np.nonzero(np.abs(out2[:, 8:24] - (img * RESP)[:, 8:24]).max(axis=(1, 2)) > 1e-6)[0]
check("mode 2H : erreur des deux cotes", len(bad2) >= len(bad), "lignes touchees : %s" % bad2.tolist())

# 6. Fausses couleurs sur un contour vertical gris : la difference entre voisins melange le detail.
img = np.zeros((32, 64, 3))
img[:, 33:] = 0.5
p3 = 2.0 * (sim_nf.sensor.expose(img, 1) - sim_nf.sensor.black) / sim_nf.sensor.dn_per_scene_unit
out3 = camcorder.separate(p3) / RESP
chroma = np.abs(out3 - out3.mean(axis=-1, keepdims=True)).max(axis=-1)
check("fausses couleurs sur un contour", chroma[8:24, 31:36].max() > 0.02 and chroma[8:24, 5:25].max() < 1e-9
      and chroma[8:24, 42:60].max() < 1e-9,
      "ecart de couleur au contour : %.3f ; loin du contour : 0" % chroma[8:24, 31:36].max())

# 7. Bande passante des couleurs : plus elle est etroite, plus la couleur bave.
img = np.zeros((16, 128, 3))
img[:, :, :] = 0.2
img[:, 64:, 0] = 0.6
p4 = 2.0 * (sim_nf.sensor.expose(img, 1) - sim_nf.sensor.black) / sim_nf.sensor.dn_per_scene_unit


def spread(bw):
    o = camcorder.separate(p4, chroma_f3db=bw / 14.1875) / RESP
    red = o[6, :, 0] - o[6, :, 1]
    return int(((red > 0.04) & (red < 0.36)).sum())


check("couleurs qui bavent", spread(0.5) > spread(1.5) > spread(0),
      "largeur de la transition : %d px a 0,5 MHz, %d px a 1,5 MHz, %d px sans filtre" % (spread(0.5), spread(1.5), spread(0)))

# 8. Bruit : la matrice amplifie le bruit de couleur (brevets ; comparaison des filtres primaires / complementaires).
rng = np.random.default_rng(0)
noise = rng.standard_normal((256, 256)) * 0.01
o = camcorder.separate(np.full((256, 256), 1.0) + noise)
gain = o[8:-8, 8:-8, 0].std() / 0.01
check("bruit amplifie par la matrice", gain > 0.5, "bruit du rouge / bruit des sommes : %.2f" % gain)

# 9. Camera complete : un gris ressort gris, les deux trames sont lues.
sim = CameraSim(CAM, **QUIET)
scene = np.full((64, 64, 3), 0.18)
out = sim.process(scene, 1, second_field=scene)
check("camera complete : gris neutre", np.abs(out[8:-8, 8:-8] / 0.18 - 1).max() < 1e-6,
      "un gris de 0,180 ressort a %.4f" % out[8:-8, 8:-8].mean())
out0 = CameraSim(CAM, cmyg_chroma_gain=0.0, **QUIET).process(np.full((64, 64, 3), (0.3, 0.1, 0.05)), 1)
dev = np.abs(out0[8:-8, 8:-8] / out0[8:-8, 8:-8].mean(axis=-1, keepdims=True) - 1).max()
check("niveau de couleur a zero", dev < 1e-3,
      "image en noir et blanc (ecart entre canaux %.4f %%)" % (100 * dev))

# 11. Mesure des rapports de sensibilite de la fiche Sony : halogene 3200 K, photosites lus un par un.
xyz = np.array([1.0612, 1.0, 0.4448])                 # corps noir a 3200 K (Y = 1)
M709 = np.array([[3.2404542, -1.5371385, -0.4985314], [-0.969266, 1.8760108, 0.041556],
                 [0.0556434, -0.2040259, 1.0572252]])
lamp = M709 @ xyz
m = mosaic_cmyg(flat_cam(lamp, 8, 8), CameraSim(CAM, **QUIET).sensor.cmyg_response)
s_cy, s_ye, s_g, s_mg = m[0, 0], m[0, 1], m[1, 0], m[1, 1]
check("rapports de sensibilite (fiche Sony)", 0.93 <= s_mg / s_g <= 1.35 and 1.15 <= s_ye / s_cy <= 1.48,
      "Mg/G = %.2f (fiche : 0,93 a 1,35) ; Ye/Cy = %.2f (fiche : 1,15 a 1,48)" % (s_mg / s_g, s_ye / s_cy))

# 12. Trainee : la methode de la fiche (tache sur 1/10 de la hauteur, 500 fois l'eclairement donnant 200 mV) redonne -110 dB.
sim = CameraSim(CAM, **dict(QUIET, ccd_smear_gain=1.0, clip_full_well=False))
sn = sim.sensor
n = 200
scene = np.zeros((n, n, 3))
scene[90:110] = 0.02
dn = sn.expose(scene, 1)
sig = (dn[100, 40:44] - sn.black).mean()
sm = (dn[20, 40:44] - sn.black).mean()
db = 20 * np.log10(sm / sig)
check("trainee (fiche Sony)", abs(db - (-110)) < 0.5, "trainee / signal de la tache = %.1f dB (fiche : -110 dB)" % db)

# 13. Le circuit garde un gris neutre d'une ligne a l'autre : chaque ligne de sommes est celle d'un gris,
#     avec une luminosite differente a chaque ligne. Malgre le retard 1H, la sortie doit rester grise.
#     (Des rayures d'UN photosite de haut, elles, donnent de vraies fausses couleurs : les deux lignes
#     additionnees n'ont pas les memes filtres.)
from camsim.camcorder import line_types              # noqa: E402
resp = np.asarray(CameraSim(CAM, **QUIET).sensor.cmyg_response)
Cy_w, Ye_w, Mg_w, G_w = resp[1] + resp[2], resp[0] + resp[1], resp[0] + resp[2], resp[1]
lum = 0.05 + 0.3 * np.random.default_rng(5).random(64)
pairs = {1: (Cy_w + G_w, Ye_w + Mg_w), 0: (Cy_w + Mg_w, Ye_w + G_w)}
pn = np.empty((64, 64))
for r, tt in enumerate(line_types(64)):
    pn[r, 0::2], pn[r, 1::2] = pairs[tt][0] * lum[r], pairs[tt][1] * lum[r]
o = camcorder.separate(pn, 1.0 / 14.1875, white=resp) / resp
o = o[4:-4, 8:-8]
dev = np.abs(o / o.mean(axis=-1, keepdims=True) - 1).max()
check("gris neutre malgre le retard 1H", dev < 1e-9,
      "ecart entre canaux %.1e : la balance des blancs agit sur R-Y et B-Y avant le retard" % dev)

# 10. Avec le bruit : la camera tourne et reste stable.
sim = CameraSim(CAM, iso=5000)
scene = np.full((96, 96, 3), 0.18)
out = sim.process(scene, 3, second_field=scene)
check("camera complete avec bruit", np.isfinite(out).all() and abs(out.mean() / 0.18 - 1) < 0.05,
      "moyenne %.4f, ecart-type %.4f" % (out.mean(), out.std()))

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
