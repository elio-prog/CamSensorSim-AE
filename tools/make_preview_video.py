"""Planche : lecture video des appareils photo (sauts de lignes, regroupement, recadrage).

Pour la demonstration, une mire de cercles concentriques (mire classique du moire) est
AJOUTEE a l'image, dans le quart droit du recadrage. Ce n'est pas un effet de camera.
Recadrages a taille reelle du calque (1920 x 1920).

Usage : python tools/make_preview_video.py <image.exr> <sortie.png>
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
VARIANTS = [
    ("Canon 700D, photo (tous les photosites)", "canoneos700d", dict(iso=800)),
    ("Canon 700D, video 1080p (1 ligne sur 3)", "canoneos700d", dict(iso=800, video_mode="1080p")),
    ("Canon 700D, 1080p lu comme un 5D Mark III (3 x 3)", "canoneos700d", dict(iso=800, video_mode="1080p",
                                                                             video_readout="bin3x3")),
    ("Nikon D850, video 1080p (1 ligne sur 3)", "nikond850", dict(iso=800, video_mode="1080p")),
    ("Canon 5D Mark IV, 4K (recadrage, tout lu)", "canoneos5d4", dict(iso=800, video_mode="4k")),
    ("Sony A7S II, 4K (tout lu puis reduit)", "sonya7s2", dict(iso=800, video_mode="4k")),
]


def zone_plate(n, kmax):
    """Cercles concentriques dont la frequence monte du centre vers le bord (jusqu'a kmax cycles/px)."""
    y, x = np.mgrid[0:n, 0:n] - n / 2.0
    r2 = x * x + y * y
    return 0.5 + 0.5 * np.cos(np.pi * kmax * r2 / (n / 2.0))


def main(src, dst):
    rgb, _, _ = read_exr(src)
    h, w, _ = rgb.shape
    y0, x0 = (h - TILE) // 2, (w - TILE) // 2
    img = rgb.copy()
    zp = zone_plate(300, 0.5)
    img[y0 + 320:y0 + 620, x0 + 330:x0 + 630] = (0.02 + 0.3 * zp)[..., None]
    tiles = []
    for label, prof, kw in VARIANTS:
        t0 = time.time()
        out, _ = CameraSim(prof, **kw).process_frame(img, 1)
        tiles.append((label, Image.fromarray(preview_srgb(out[y0:y0 + TILE, x0:x0 + TILE], 2.1))))
        print("%-55s %.1f s" % (label, time.time() - t0))
    cols, pad, head = 3, 8, 26
    sheet = Image.new("RGB", (cols * (TILE + pad) + pad, 2 * (TILE + head + pad) + pad), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(tiles):
        x, y = pad + (i % cols) * (TILE + pad), pad + (i // cols) * (TILE + head + pad)
        d.text((x + 2, y + 6), label, fill=(230, 230, 230))
        sheet.paste(im, (x, y + head))
    sheet.save(dst)
    print("ecrit", dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
