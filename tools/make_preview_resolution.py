"""Planche : simulation a la resolution de l'image ou a la vraie grille du capteur.

Recadrages a taille reelle au centre de l'image. Comme pour la planche du camescope,
une mire de barres de couleur est AJOUTEE a l'image pour la demonstration.

Usage : python tools/make_preview_resolution.py <image.exr> <sortie.png>
"""
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim                       # noqa: E402
from camsim.color import preview_srgb              # noqa: E402
from camsim.exr_io import read_exr                 # noqa: E402

TILE = 640
CAM = "icx409ak_camcorder"
VARIANTS = [
    ("Camescope, resolution de l'image (1920 x 1920)", CAM, dict(iso=1250), 1),
    ("Camescope, vraie grille 560 x 582, agrandi Lanczos", CAM, dict(iso=1250, sensor_resolution="capteur"), 1),
    ("Camescope, vraie grille, agrandi au plus proche", CAM, dict(iso=1250, sensor_resolution="capteur",
                                                                 sensor_upscale="nearest"), 1),
    ("3 CCD KAI-0373, vraie grille 568 x 484", "kai0373_3ccd", dict(iso=400, sensor_resolution="capteur"), 1),
    ("Plumbicon, 576 lignes de balayage", "plumbicon_xq1410", dict(sensor_resolution="capteur"), 4),
    ("Sony A7S II, vraie grille 2832 x 2832, ISO 6400", "sonya7s2", dict(iso=6400, sensor_resolution="capteur"), 1),
]
BARS = [(0.18, 0.18, 0.18), (0.30, 0.28, 0.02), (0.03, 0.22, 0.25), (0.04, 0.20, 0.03),
        (0.24, 0.03, 0.22), (0.25, 0.02, 0.02), (0.02, 0.03, 0.30), (0.01, 0.01, 0.01)]


def main(src, dst):
    rgb, _, _ = read_exr(src)
    h, w, _ = rgb.shape
    y0, x0 = (h - TILE) // 2, (w - TILE) // 2
    img = rgb.copy()
    bw = TILE // 2 // len(BARS)
    for i, c in enumerate(BARS):
        img[y0 + TILE - 170: y0 + TILE - 20, x0 + 20 + i * bw: x0 + 20 + (i + 1) * bw] = c
    tiles = []
    for label, profile, kw, n in VARIANTS:
        t0 = time.time()
        sim = CameraSim(profile, **kw)
        for k in range(n):                       # quelques images pour que le tube soit dans son etat normal
            out, _ = sim.process_frame(img, frame=k, prev=img, nxt=img)
        tiles.append((label, Image.fromarray(preview_srgb(out[y0:y0 + TILE, x0:x0 + TILE], 2.1))))
        print("%-60s %.1f s" % (label, time.time() - t0))
    cols, pad, head = 3, 8, 26
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (TILE + pad) + pad, rows * (TILE + head + pad) + pad), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(tiles):
        x, y = pad + (i % cols) * (TILE + pad), pad + (i // cols) * (TILE + head + pad)
        d.text((x + 2, y + 6), label, fill=(230, 230, 230))
        sheet.paste(im, (x, y + head))
    sheet.save(dst)
    print("ecrit", dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
