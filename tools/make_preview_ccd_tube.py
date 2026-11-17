"""Planche CCD et tubes sur une image du rendu.

Le plan d'essai bouge trop peu pour montrer la remanence et les trainees. Pour la
demonstration seulement, ce script AJOUTE deux choses a l'image : un panoramique
artificiel (l'image glisse de PAN pixels par image) et une petite source tres
lumineuse. Ce sont des ajouts de demonstration, pas des effets de la camera.

Usage : python tools/make_preview_ccd_tube.py <image.exr> <sortie.png>
Apercu en sRGB simple : sert a juger les defauts, pas les couleurs finales.
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

PAN = 40          # pixels par image
FRAMES = 7
LIGHT = 400.0     # valeur de scene de la source ajoutee (environ 300 fois la saturation)

VARIANTS = [
    ("3 CCD KAI-0373, ISO 400", "kai0373_3ccd", dict(iso=400), False),
    ("3 CCD : trainee verticale, pose 1/2000 s", "kai0373_3ccd", dict(iso=400, exposure_time_s=1 / 2000), True),
    ("3 CCD use : eblouissement, transfert 3e-4, colonnes bloquees, rouge decale",
     "kai0373_3ccd", dict(iso=400, ccd_antiblooming=1.0, ccd_cti_v=3e-4, ccd_cti_h=3e-4, ccd_blocked_columns=5,
                          registration_r_dx=2.5, registration_b_dy=-2.0), True),
    ("3 CCD entrelace, panoramique", "kai0373_3ccd", dict(iso=400, interlace="fields"), False),
    ("3 Plumbicon XQ1410 : trainee de comete", "plumbicon_xq1410", dict(), True),
    ("Vidicon XQ1031 noir et blanc, panoramique", "vidicon_xq1031", dict(), True),
]


def main(src, dst, tile=640, expo=2.1):
    rgb, alpha, _ = read_exr(src)
    h, w, _ = rgb.shape
    yy, xx = np.mgrid[0:h, 0:w]
    disc = ((yy - 0.30 * h) ** 2 + (xx - 0.62 * w) ** 2) < 12 ** 2

    def scene(k, light):
        img = rgb.copy()
        if light:
            img[disc] = LIGHT
        return np.roll(img, PAN * k, axis=1)

    tiles = []
    for label, profile, kw, light in VARIANTS:
        t0 = time.time()
        sim = CameraSim(profile, **kw)
        moving = sim.needs_neighbors() or profile != "kai0373_3ccd"
        frames = range(FRAMES) if moving else [FRAMES - 1]
        for k in frames:
            cur = scene(k, light)
            # Mouvement connu exactement : PAN pixels par image vers la droite.
            flow = np.zeros((h, w, 2))
            flow[..., 0] = PAN
            out, _ = sim.process_frame(cur, frame=k, flow_next=flow, flow_prev=-flow)
        tiles.append((label, Image.fromarray(preview_srgb(out, expo)).resize((tile, tile), Image.LANCZOS)))
        print("%-70s %.0f s" % (label, time.time() - t0))

    cols, pad, head = 3, 8, 26
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tile + pad) + pad, rows * (tile + head + pad) + pad), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(tiles):
        x, y = pad + (i % cols) * (tile + pad), pad + (i // cols) * (tile + head + pad)
        d.text((x + 2, y + 6), label, fill=(230, 230, 230))
        sheet.paste(im, (x, y + head))
    sheet.save(dst)
    print("ecrit", dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
