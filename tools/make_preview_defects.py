"""Planche des defauts : filtre passe-bas, obturateur deroulant, scintillement.

Usage : python tools/make_preview_defects.py <dossier_exr> <numero_image> <sortie.png>
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
from camsim.shutter import estimate_flow           # noqa: E402


def main(folder, n, dst, crop=480, by=240, bx=1200, expo=2.1):
    files = {int(f.stem[-4:]): f for f in sorted(Path(folder).glob("*.exr"))}
    cur, alpha, _ = read_exr(files[n])
    prev = read_exr(files[n - 1])[0] if n - 1 in files else None
    nxt = read_exr(files[n + 1])[0] if n + 1 in files else None
    if nxt is not None:
        fl = estimate_flow(cur, nxt)
        mag = np.hypot(fl[..., 0], fl[..., 1])[alpha > 0.5]
        print("mouvement entre images : median %.2f px, 95e centile %.2f px" % (np.median(mag), np.percentile(mag, 95)))

    def run(**kw):
        sim = CameraSim("sonya7s2", iso=800, **kw)
        return sim.process_frame(cur, frame=n, alpha=alpha, prev=prev, nxt=nxt)[0]

    det = lambda im: Image.fromarray(preview_srgb(im[by:by + crop, bx:bx + crop], expo))
    full = lambda im: Image.fromarray(preview_srgb(im, expo)).resize((crop, crop), Image.LANCZOS)
    tiles = [
        ("Sans filtre passe-bas (detail)", det(run(olpf_h_px=0, olpf_v_px=0))),
        ("Filtre passe-bas 1 px (detail)", det(run())),
        ("Obturateur global", full(run())),
        ("Obturateur deroulant, lecture 1/25 s", full(run(readout_time_s=0.04))),
        ("Deroulant 1/25 s x mouvement x8 (exagere)", None),
        ("Scintillement 100 Hz, pose 1/400 s", full(run(readout_time_s=0.03, exposure_time_s=1 / 400,
                                                         flicker_hz=100.0, flicker_depth=0.3))),
    ]
    # Tuile exageree : meme calcul, avec un flux multiplie par 8 pour rendre la deformation lisible.
    if nxt is not None and prev is not None:
        sim = CameraSim("sonya7s2", iso=800, readout_time_s=0.04)
        fn, fp = estimate_flow(cur, nxt) * 8, estimate_flow(cur, prev) * 8
        tiles[4] = (tiles[4][0], full(sim.process_frame(cur, frame=n, alpha=alpha, flow_next=fn, flow_prev=fp)[0]))
    tiles = [t for t in tiles if t[1] is not None]
    cols, pad, head = 3, 8, 26
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (crop + pad) + pad, rows * (crop + head + pad) + pad), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(tiles):
        x, y = pad + (i % cols) * (crop + pad), pad + (i // cols) * (crop + head + pad)
        d.text((x + 2, y + 6), label, fill=(230, 230, 230))
        sheet.paste(im, (x, y + head))
    sheet.save(dst)
    print("ecrit", dst)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), sys.argv[3])
