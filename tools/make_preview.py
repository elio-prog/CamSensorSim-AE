"""Planche de comparaison : une image du rendu passee dans la camera a plusieurs reglages.

Usage : python tools/make_preview.py <image.exr> <sortie.png>
L'apercu utilise une conversion simple vers sRGB (pas la vue AgX/ACES d'AE ou de
Blender) : il sert a juger le bruit, pas les couleurs finales.
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

VARIANTS = [
    ("Original (rendu)", None),
    ("ISO 800", dict(iso=800)),
    ("ISO 6400", dict(iso=6400)),
    ("ISO 51200 (extrapole)", dict(iso=51200)),
]


def main(src, dst, crop=480):
    rgb, alpha, _ = read_exr(src)
    frame = int("".join(c for c in Path(src).stem[-4:] if c.isdigit()) or 0)
    h, w, _ = rgb.shape
    # Zone de detail : la fenetre la plus contrastee parmi une grille de candidates.
    lum = rgb.mean(axis=-1)
    best, by, bx = -1.0, 0, 0
    for y in range(0, h - crop + 1, crop // 2):
        for x in range(0, w - crop + 1, crop // 2):
            win = lum[y:y + crop, x:x + crop]
            score = win.std() * min(win.mean(), 0.2)
            if score > best:
                best, by, bx = score, y, x
    expo = 0.18 / max(np.percentile(lum[lum > 0], 75), 1e-4) if (lum > 0).any() else 1.0
    expo = float(min(max(expo, 1.0), 16.0))

    tiles = []
    for label, settings in VARIANTS:
        t0 = time.time()
        img = rgb if settings is None else CameraSim("sonya7s2", **settings).process(rgb, frame=frame)
        full = Image.fromarray(preview_srgb(img, expo)).resize((crop, crop), Image.LANCZOS)
        detail = Image.fromarray(preview_srgb(img[by:by + crop, bx:bx + crop], expo))
        tiles.append((label, full, detail))
        print("%-24s %.1f s" % (label, time.time() - t0))

    pad, head = 8, 26
    sheet = Image.new("RGB", (len(tiles) * (crop + pad) + pad, head + 2 * (crop + pad) + pad), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for i, (label, full, detail) in enumerate(tiles):
        x = pad + i * (crop + pad)
        d.text((x + 2, 7), label, fill=(230, 230, 230))
        sheet.paste(full, (x, head))
        sheet.paste(detail, (x, head + crop + pad))
    sheet.save(dst)
    print("apercu : exposition x%.1f, detail a (%d, %d), ecrit dans %s" % (expo, bx, by, dst))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
