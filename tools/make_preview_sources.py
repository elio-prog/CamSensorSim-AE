"""Planche : la meme image passee dans une camera depuis l'EXR (lumiere de scene) ou depuis un PNG 8 bits.

Le PNG est fabrique ici a partir de l'EXR (ACEScg -> Rec.709, courbe sRGB, ecretage a 1,
8 bits), comme le serait un rendu deja traite. Il est ecrit dans out/, jamais a cote de la source.

Usage : python tools/make_preview_sources.py <image.exr> <sortie.png>
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim, sources               # noqa: E402
from camsim.color import preview_srgb               # noqa: E402
from camsim.exr_io import read_exr                  # noqa: E402
from camsim.pipeline import DEFAULTS                # noqa: E402

TILE = 640


def main(src, dst):
    rgb, _, _ = read_exr(src)
    h, w, _ = rgb.shape
    # Source "deja traitee" : PNG 8 bits sRGB, exposee d'un diaphragme de plus pour etre lisible.
    expo = 2.0
    p = sources.input_params(dict(DEFAULTS, input_transfer="srgb"), "image")
    disp = sources.scene_to_display(rgb * expo, dict(p, exposure=1.0))
    png = Path(dst).with_name("source_8bits_1010.png")
    cv2.imwrite(str(png), (disp[..., ::-1] * 255.0 + 0.5).astype(np.uint8))
    src_png = sources.open_source(png, dict(DEFAULTS))
    from_png = src_png.get(src_png.numbers[0])[0]
    src_png8 = sources.open_source(png, dict(DEFAULTS, input_dequantize=False))
    from_png_nodither = src_png8.get(src_png8.numbers[0])[0]
    y0, x0 = (h - TILE) // 2, (w - TILE) // 2

    variants = [
        ("EXR, sans camera", rgb * expo, None, {}),
        ("PNG 8 bits relu (courbe sRGB defaite), sans camera", from_png, None, {}),
        ("PNG 8 bits sans tremblement, x4 en lumiere", from_png_nodither * 4.0, None, {}),
        ("Camescope depuis l'EXR", rgb * expo, "icx409ak_camcorder", dict(iso=5000)),
        ("Camescope depuis le PNG 8 bits", from_png, "icx409ak_camcorder", dict(iso=5000)),
        ("Sony A7S II ISO 12800 depuis le PNG", from_png, "sonya7s2", dict(iso=12800)),
    ]
    tiles = []
    for label, img, prof, kw in variants:
        t0 = time.time()
        out = img if prof is None else CameraSim(prof, **kw).process_frame(img, 1)[0]
        tiles.append((label, Image.fromarray(preview_srgb(out[y0:y0 + TILE, x0:x0 + TILE], 1.0))))
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
