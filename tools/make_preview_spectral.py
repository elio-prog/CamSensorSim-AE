"""Planche : couleur vue par les vraies courbes des capteurs (spectral) contre la verite.

Haut de chaque vignette : les 190 couleurs de reference (eclairees par D65). Bas : un cercle de
couleurs tres saturees. Camera sans bruit, pour ne voir que la couleur. Colonne de gauche : la
verite ; puis chaque appareil.

Usage : python tools/make_preview_spectral.py <sortie.png>
"""
import colorsys
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim, spectral as sp       # noqa: E402
from camsim.color import XYZ_D65_TO_AP1, preview_srgb   # noqa: E402
from camsim.sources import REC709_TO_AP1           # noqa: E402

Q = dict(iso=800, shot_noise=False, read_noise_model="off", row_noise_gain=0.0, color_bias_gain=0.0, quantize=False,
         clip_full_well=False, clip_highlights=False, olpf_h_px=0.0, olpf_v_px=0.0, demosaic="nearest")
CAMS = [("Sony A7S II (mesure)", "sonya7s2"), ("Nikon D850 (mesure)", "nikond850"),
        ("Canon 5D Mark IV (mesure)", "canoneos5d4"), ("Canon 700D (courbes du 100D)", "canoneos700d"),
        ("Canon 70D (courbes du 200D)", "canoneos70d")]
P = 22


def chart():
    sp.Spectral("Sony_ILCE-7SM2_380_780_5.json")
    refl = sp.load_training()
    ap1 = (refl.T @ sp.Spectral._base["w_xyz"]) @ XYZ_D65_TO_AP1.T * 0.8
    cols = 19
    rows = int(np.ceil(len(ap1) / cols))
    img = np.full((rows * P, cols * P, 3), 0.05)
    for i, c in enumerate(ap1):
        r, k = divmod(i, cols)
        img[r * P:(r + 1) * P - 2, k * P:(k + 1) * P - 2] = c
    # Cercle de couleurs saturees (Rec.709 tres sature, converti en ACEScg)
    wheel = np.full((4 * P, cols * P, 3), 0.05)
    for k in range(cols):
        for r in range(4):
            rgb = np.array(colorsys.hsv_to_rgb(k / cols, 1.0 - 0.25 * r, 0.6)) ** 2.2
            wheel[r * P:(r + 1) * P - 2, k * P:(k + 1) * P - 2] = rgb @ REC709_TO_AP1.T
    return np.concatenate([img, wheel], axis=0)


def main(dst):
    truth = chart()
    tiles = [("Verite (couleurs de la scene)", truth)]
    for label, prof in CAMS:
        sim = CameraSim(prof, **Q)
        tiles.append((label, sim.process(truth, 1)))
        print(label, "fini")
    h, w = truth.shape[:2]
    pad, head = 8, 22
    sheet = Image.new("RGB", (2 * w + 3 * pad, 3 * (h + head + pad) + pad), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(tiles):
        x, y = pad + (i % 2) * (w + pad), pad + (i // 2) * (h + head + pad)
        d.text((x + 2, y + 5), label, fill=(230, 230, 230))
        sheet.paste(Image.fromarray(preview_srgb(im, 1.0)), (x, y + head))
    sheet.save(dst)
    print("ecrit", dst)


if __name__ == "__main__":
    main(sys.argv[1])
