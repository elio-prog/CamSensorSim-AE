"""Tests du mode video des appareils photo (sauts de lignes, regroupement, recadrage).

Usage : python tests/test_video.py   (depuis le dossier CameraSim_AE)
"""
import copy
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim, load_profile           # noqa: E402
from camsim.sensor import mosaic_rggb                 # noqa: E402
from camsim.video import _same_colour_indices, bin_same_colour, output_count   # noqa: E402

results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


# 1. Photosites regroupes : meme couleur, la mosaique de Bayer est conservee.
idx3 = _same_colour_indices(12, 3, np.arange(output_count(12, 3)))
idx2 = _same_colour_indices(16, 2, np.arange(output_count(16, 2)))
par_ok = all((row % 2 == k % 2).all() for k, row in enumerate(idx3)) and all((row % 2 == k % 2).all() for k, row in enumerate(idx2))
check("regroupement de meme couleur", par_ok and idx3[1].tolist() == [1, 3, 5] and idx3[2].tolist() == [4, 6, 8]
      and idx2[0].tolist() == [0, 2] and idx2[1].tolist() == [1, 3] and idx2[2].tolist() == [4, 6],
      "par 3 : sortie 1 = photosites 1, 3, 5 ; sortie 2 = 4, 6, 8 ; par 2 : (0, 2), (1, 3), (4, 6)")
flat = mosaic_rggb(np.broadcast_to(np.array([0.3, 0.5, 0.2]), (36, 48, 3)).copy())
b = bin_same_colour(flat, 3, 3) / 9.0
check("mosaique conservee", np.allclose(b, mosaic_rggb(np.broadcast_to(np.array([0.3, 0.5, 0.2]), b.shape + (3,)).copy())),
      "une couleur unie regroupee 3 x 3 redonne la mosaique RGGB de cette couleur (%d x %d)" % (b.shape[1], b.shape[0]))

# 2. Bruit de photons : regrouper n photosites divise l'ecart-type par racine de n.
QUIET = dict(iso=800, read_noise_model="off", row_noise_gain=0.0, color_bias_gain=0.0, prnu_sigma=0.0,
             dark_current_e_per_s=0.0, quantize=False, olpf_h_px=0.0, olpf_v_px=0.0)
sim = CameraSim("sonya7s2", **QUIET)
cam = np.full((240, 240, 3), 0.18)
g = lambda d: d[0::2, 1::2]
s1 = g(sim.sensor.expose(cam, 1)).std()
s3 = g(sim.sensor.expose(cam, 1, binning=(1, 3))).std()
s9 = g(sim.sensor.expose(cam, 1, binning=(3, 3))).std()
check("bruit de photons regroupe", abs(s1 / s3 - np.sqrt(3)) < 0.08 and abs(s1 / s9 - 3.0) < 0.15,
      "ecart-type divise par %.2f (3 photosites, theorie 1,73) et %.2f (9 photosites, theorie 3)" % (s1 / s3, s1 / s9))

# 3. Une ligne sur trois : des rayures horizontales de periode 3 lignes se replient en un aplat.
prof = copy.deepcopy(load_profile("sonya7s2"))
prof["video_modes"] = {"1080p": {"window": [192, 108], "readout": "skip3", "size": [64, 36]}}
prof["pixel_pitch_um"] = [1.0, 1.0]
img = np.full((108, 192, 3), 0.05)
img[0::3] = 0.5                                       # une ligne claire toutes les 3 lignes de photosites
q = dict(QUIET, shot_noise=False, clip_highlights=False, clip_full_well=False)
skip = CameraSim(prof, video_mode="1080p", sensor_output="capteur", **q).process_frame(img, 1)[0]
full = CameraSim(prof, video_mode="1080p", video_readout="full", sensor_output="capteur", **q).process_frame(img, 1)[0]
check("repli des rayures (une ligne sur trois)", abs(skip[4:-4, 4:-4].mean() - 0.5) < 0.02 and abs(full[4:-4, 4:-4].mean() - 0.2) < 0.02,
      "lecture partielle : %.3f (seules les lignes claires sont lues) ; lecture complete : %.3f (moyenne 0,200)"
      % (skip[4:-4, 4:-4].mean(), full[4:-4, 4:-4].mean()))
img2 = np.full((108, 192, 3), 0.05)
img2[1::3] = 0.5                                       # la meme mire decalee d'une ligne
skip2 = CameraSim(prof, video_mode="1080p", sensor_output="capteur", **q).process_frame(img2, 1)[0]
check("la mire disparait si on la decale d'une ligne", abs(skip2[4:-4, 4:-4].mean() - 0.05) < 0.02,
      "%.3f : aucune ligne claire n'est lue (c'est le scintillement des fins details en video)" % skip2[4:-4, 4:-4].mean())

# 4. Tailles de sortie et modes de chaque appareil.
img = np.full((270, 480, 3), 0.18)
shapes = {}
for p_, m in (("canoneos70d", "1080p"), ("canoneos700d", "1080p"), ("canoneos5d4", "1080p"), ("nikond850", "1080p")):
    shapes[p_] = CameraSim(p_, video_mode=m, sensor_output="capteur", iso=400).process_frame(img, 1)[0].shape
check("1080p des reflex", all(v == (1080, 1920, 3) for v in shapes.values()), "%s" % sorted(set(shapes.values())))
o = CameraSim("canoneos5d4", video_mode="4k", sensor_output="capteur", iso=400).process_frame(img, 1)[0]
check("4K du 5D Mark IV (recadrage DCI)", o.shape == (2160, 3840, 3),
      "image 16:9 placee dans la fenetre 4096 x 2160 : %d x %d" % (o.shape[1], o.shape[0]))
try:
    CameraSim("canoneos70d", video_mode="4k").process_frame(img, 1)
    msg = None
except ValueError as e:
    msg = str(e)
check("mode absent refuse", msg is not None and "1080p" in msg, msg or "aucune erreur")
try:
    CameraSim("plumbicon_xq1410", video_mode="1080p").process_frame(img, 1)
    msg = None
except ValueError as e:
    msg = str(e)
check("mode video refuse pour un tube", msg is not None, msg or "aucune erreur")

# 5. Gris neutre et mode "off" inchange.
o = CameraSim("canoneos700d", video_mode="1080p", **dict(QUIET, shot_noise=False)).process_frame(img, 1)[0]
check("gris neutre en video", np.abs(o[8:-8, 8:-8] / 0.18 - 1).max() < 0.01, "moyenne %.4f" % o[8:-8, 8:-8].mean())
a = CameraSim("canoneos700d", seed=4).process_frame(img, 2)[0]
b = CameraSim("canoneos700d", seed=4, video_mode="off").process_frame(img, 2)[0]
check("mode photo inchange", np.array_equal(a, b), "identique bit a bit")

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
