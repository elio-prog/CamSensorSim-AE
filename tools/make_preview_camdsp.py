"""Planche de l'etage video du camescope (coude, accentuation, couleurs dans les blancs) et des defauts de la fiche.

Le rendu d'essai est presque gris : pour montrer le traitement des couleurs, ce script
AJOUTE une mire de barres de couleur dans l'image. C'est un ajout de demonstration,
pas un effet de la camera. Les vignettes sont des recadrages a taille reelle (1 pixel
d'image = 1 pixel de la planche), sans quoi les defauts fins disparaissent.

Usage : python tools/make_preview_camcorder.py <image.exr> <sortie.png>
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
    ("Camescope sans etage video", CAM, dict(iso=1250, dsp_enabled="off")),
    ("Camescope avec etage video (profil)", CAM, dict(iso=1250)),
    ("Accentuation forte des contours", CAM, dict(iso=1250, dsp_detail_h=2.0, dsp_detail_v=1.5, dsp_detail_limit=0.3)),
    ("Surexpose +2 diaph., sans etage video", CAM, dict(iso=1250, ev=2, dsp_enabled="off")),
    ("Surexpose +2 diaph., avec coude et blancs", CAM, dict(iso=1250, ev=2)),
    ("Defauts de la fiche au maximum, vraie grille", CAM, dict(iso=1250, sensor_shading=0.2, cmyg_line_crawl=0.03,
                                                                cmyg_field_flicker=0.02, ccd_lag=0.005,
                                                                sensor_resolution="capteur")),
]

# Barres de couleur en ACEScg (valeurs lineaires de scene)
BARS = [(0.18, 0.18, 0.18), (0.30, 0.28, 0.02), (0.03, 0.22, 0.25), (0.04, 0.20, 0.03),
        (0.24, 0.03, 0.22), (0.25, 0.02, 0.02), (0.02, 0.03, 0.30), (0.01, 0.01, 0.01)]


def main(src, dst):
    rgb, _, _ = read_exr(src)
    h, w, _ = rgb.shape
    y0, x0 = (h - TILE) // 2, (w - TILE) // 2
    img = rgb.copy()
    bw = TILE // 2 // len(BARS)
    for i, c in enumerate(BARS):                      # mire dans le quart bas-gauche du recadrage
        img[y0 + TILE - 170: y0 + TILE - 20, x0 + 20 + i * bw: x0 + 20 + (i + 1) * bw] = c
    tiles = []
    for label, profile, kw in VARIANTS:
        t0 = time.time()
        sim = CameraSim(profile, **kw)
        out, _ = sim.process_frame(img, frame=1)
        crop = out[y0:y0 + TILE, x0:x0 + TILE]
        tiles.append((label, Image.fromarray(preview_srgb(crop, 2.1))))
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
