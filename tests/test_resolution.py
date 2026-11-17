"""Tests de la resolution de simulation (grille de l'image ou vraie grille du capteur).

Usage : python tests/test_resolution.py   (depuis le dossier CameraSim_AE)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim, load_profile           # noqa: E402
from camsim import resolution                        # noqa: E402

results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


# 1. Cadrage dans la zone utile du capteur, avec la forme reelle des photosites.
cam = load_profile("icx409ak_camcorder")
g_sq = resolution.sensor_grid(cam, 1920, 1920)
g_169 = resolution.sensor_grid(cam, 1080, 1920)
check("grille du camescope, image carree", g_sq == (582, 560),
      "%d x %d photosites (582 lignes ; 582 x 6,25 / 6,50 = 560 colonnes)" % (g_sq[1], g_sq[0]))
check("grille du camescope, image 16:9", g_169 == (440, 752),
      "%d x %d photosites (752 colonnes ; 752 x 6,50 / 6,25 x 9/16 = 440 lignes)" % (g_169[1], g_169[0]))
g_kai = resolution.sensor_grid(load_profile("kai0373_3ccd"), 1920, 1920)
check("grille du KAI-0373, image carree", g_kai == (484, 568), "%d x %d (photosites de 11,6 x 13,6 um)" % (g_kai[1], g_kai[0]))
g_a7 = resolution.sensor_grid(load_profile("sonya7s2"), 2000, 3000)
check("grille du Sony A7S II, image 3:2", g_a7[1] == 4240 and abs(g_a7[0] - 2827) <= 1, "%d x %d" % (g_a7[1], g_a7[0]))

# 2. Recueil de la lumiere : la quantite de lumiere est conservee.
rng = np.random.default_rng(1)
img = rng.random((300, 400, 3))
small = resolution.to_grid(img, 97, 131)
check("lumiere conservee", abs(small.mean() - img.mean()) < 2e-3, "moyenne %.4f -> %.4f" % (img.mean(), small.mean()))

# 3. Flux optique ramene a la grille.
flow = np.zeros((100, 200, 2))
flow[..., 0], flow[..., 1] = 40.0, -10.0
f2 = resolution.flow_to_grid(flow, 50, 50)
check("flux optique en photosites", np.allclose(f2[..., 0], 10.0) and np.allclose(f2[..., 1], -5.0),
      "40 px / 200 -> 10 photosites / 50 ; -10 px / 100 -> -5")

# 4. Mode "image" : exactement le comportement d'origine.
scene = np.full((64, 64, 3), 0.18) + 0.05 * rng.random((64, 64, 3))
a = CameraSim("sonya7s2", seed=3).process_frame(scene, 2)[0]
b = CameraSim("sonya7s2", seed=3)._process_frame(scene, 2)[0]
check("mode image inchange", np.array_equal(a, b), "resultat identique bit a bit")

# 5. Mode "capteur" : tailles de sortie.
scene = np.full((192, 192, 3), 0.18)
alpha = np.ones((192, 192), dtype=np.float32)
sim = CameraSim("icx409ak_camcorder", iso=1250, sensor_resolution="capteur")
out, al = sim.process_frame(scene, 1, alpha=alpha, prev=scene, nxt=scene)
check("mode capteur, sortie a la taille du calque", out.shape == (192, 192, 3) and al.shape == (192, 192),
      "calcul sur %d x %d photosites, sortie %d x %d" % (sim.grid(192, 192)[1], sim.grid(192, 192)[0], out.shape[1], out.shape[0]))
sim = CameraSim("icx409ak_camcorder", iso=1250, sensor_resolution="capteur", sensor_output="capteur")
out2, _ = sim.process_frame(np.full((1920, 1920, 3), 0.18), 1)
check("mode capteur, sortie a la taille du capteur", out2.shape == (582, 560, 3), "sortie %d x %d" % (out2.shape[1], out2.shape[0]))

# 6. Le grain suit la taille des photosites : en mode capteur, des pixels voisins partagent le meme bruit.
def neighbour_corr(x, lag=1):
    y = x[..., 1] - x[..., 1].mean()
    return float((y[:, lag:] * y[:, :-lag]).mean() / (y * y).mean())


flat = np.full((960, 960, 3), 0.18)
c_img = neighbour_corr(CameraSim("sonya7s2", iso=6400, olpf_h_px=0, olpf_v_px=0).process_frame(flat, 1)[0])
c_sen = neighbour_corr(CameraSim("sonya7s2", iso=6400, olpf_h_px=0, olpf_v_px=0, sensor_resolution="capteur",
                                 sensor_upscale="nearest").process_frame(flat, 1)[0])
check("taille du grain", c_sen < c_img, "correlation entre voisins : %.2f en mode image, %.2f en mode capteur (A7S II : "
      "4240 photosites pour 960 px, le grain devient plus fin)" % (c_img, c_sen))
cam_img = CameraSim("icx409ak_camcorder", iso=10000, sensor_resolution="image").process_frame(flat, 1)[0]
cam_sen = CameraSim("icx409ak_camcorder", iso=10000, sensor_resolution="capteur", sensor_upscale="nearest").process_frame(flat, 1)[0]
ci, cs = neighbour_corr(cam_img, 3), neighbour_corr(cam_sen, 3)
check("grain du camescope", cs > ci + 0.15,
      "correlation a 3 px : %.2f en mode image, %.2f en mode capteur (560 photosites pour 960 px, grain plus gros)" % (ci, cs))

# 7. Tubes : 576 lignes de balayage.
sim = CameraSim("plumbicon_xq1410", sensor_resolution="capteur")
out, _ = sim.process_frame(np.full((480, 640, 3), 0.18), 1)
check("tube en mode capteur", sim.grid(480, 640) == (576, 768) and out.shape == (480, 640, 3) and np.isfinite(out).all(),
      "grille %d x %d (576 lignes PAL)" % (sim.grid(480, 640)[1], sim.grid(480, 640)[0]))

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
