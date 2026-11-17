"""Planche des pannes sur une image du rendu.

Usage : python tools/make_preview_faults.py <dossier_exr> <numero_image> <sortie.png>
Apercu en sRGB simple : sert a juger les defauts, pas les couleurs finales.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim                       # noqa: E402
from camsim.color import preview_srgb              # noqa: E402
from camsim.exr_io import read_exr                 # noqa: E402

VARIANTS = [
    ("Colonnes : decalage 25 DN, 6 mortes, pixels morts",
     dict(column_fpn_dn=25.0, dead_columns=6, dead_pixel_fraction=0.002, stuck_pixel_fraction=0.0005)),
    ("Adresse de ligne : bit 6 colle a 0", dict(row_address_stuck="6:0")),
    ("Adresses : 3 % de lignes et colonnes fausses", dict(row_address_error_rate=0.03, col_address_error_rate=0.03)),
    ("Convertisseur : bit 10 colle a 1, bit 8 a 0", dict(adc_stuck_bits="10:1,8:0")),
    ("Convertisseur : 5 bits, marches irregulieres", dict(adc_bits=5, adc_dnl_lsb=0.6)),
    ("Liaison serie : glissement de 2 bits", dict(bitslip=2)),
    ("Synchro de ligne perdue", dict(line_shift_rate=0.05, line_shift_max_px=12, line_shift_persist=True)),
    ("Motif de Bayer faux + noir mal regle", dict(isp_bayer_dx=1, isp_black_offset_dn=-60.0)),
    ("Parasite d'alimentation 120 DN + dechirure", dict(interference_dn=120.0, interference_period_px=733.7,
                                                         frame_tear_rate=1.0)),
]


def main(folder, n, dst, tile=480, expo=2.1):
    files = {int(f.stem[-4:]): f for f in sorted(Path(folder).glob("*.exr"))}
    cur, alpha, _ = read_exr(files[n])
    older = read_exr(files[n - 8])[0] if n - 8 in files else cur[:, ::-1].copy()
    tiles = []
    for label, kw in VARIANTS:
        sim = CameraSim("sonya7s2", iso=800, **kw)
        if "frame_tear_rate" in kw:
            sim.process(older, frame=n - 1)        # remplit la memoire tampon avec une image plus ancienne
        out = sim.process(cur, frame=n)
        tiles.append((label, Image.fromarray(preview_srgb(out, expo)).resize((tile, tile), Image.LANCZOS)))
        print("fait :", label)
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
    main(sys.argv[1], int(sys.argv[2]), sys.argv[3])
