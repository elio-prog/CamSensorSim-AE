"""Demonstration de l'enregistrement sur l'animation du rendu (50 images).

Le camescope est calcule une seule fois par image (en 480 x 480 pour aller vite), puis le
meme signal est enregistre dans plusieurs formats reels. Sorties : une video MP4 (mosaique
3 x 2) et une planche PNG d'une image, recadree a taille reelle.

Usage : python tools/make_preview_record.py <dossier de la sequence EXR> <dossier de sortie>
"""
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim, record, sources      # noqa: E402
from camsim.color import preview_srgb              # noqa: E402
from camsim.exr_io import read_exr                 # noqa: E402
from camsim.pipeline import DEFAULTS               # noqa: E402

S = 480
VARIANTS = [
    ("Camescope, sans enregistrement", None),
    ("DV PAL (MiniDV)", dict(rec_format="dv_pal")),
    ("DVD MPEG-2, debit au plancher", dict(rec_format="dvd", rec_bitrate_scale=0.05)),
    ("H.264 telephone a 0,3 Mbit/s", dict(rec_format="h264_phone", rec_bitrate_scale=0.018)),
    ("DV PAL, bande abimee", dict(rec_format="dv_pal", rec_errors=0.0002, rec_drop=0.05)),
    ("Telephone 3GP (QCIF, 15 im/s)", dict(rec_format="3gp")),
]


def main(seq_dir, out_dir):
    out_dir = Path(out_dir)
    files = sorted(Path(seq_dir).glob("*.exr"))
    sim = CameraSim("icx409ak_camcorder", iso=1250, fps=25)
    cam = []
    prev = None
    for k, f in enumerate(files):
        rgb = cv2.resize(read_exr(f)[0], (S, S), interpolation=cv2.INTER_AREA)
        o, _ = sim.process_frame(rgb, k, prev=prev, nxt=None)
        prev = rgb
        cam.append(o)
    print("camescope :", len(cam), "images")
    outs = []
    for label, kw in VARIANTS:
        if kw is None:
            outs.append(cam)
            continue
        f = record.params(dict(DEFAULTS, **kw))
        outs.append(record.record_sequence(cam, f, 25))
        print(label, "fini")
    tmp = out_dir / "_rec_frames"
    tmp.mkdir(parents=True, exist_ok=True)
    for k in range(len(cam)):
        tiles = []
        for (label, _), o in zip(VARIANTS, outs):
            im = Image.fromarray(preview_srgb(o[k], 1.0))
            ImageDraw.Draw(im).text((6, 6), label, fill=(255, 255, 0))
            tiles.append(np.asarray(im))
        grid = np.concatenate([np.concatenate(tiles[:3], axis=1), np.concatenate(tiles[3:], axis=1)], axis=0)
        Image.fromarray(grid).save(tmp / ("f_%03d.png" % k))
    mp4 = out_dir / "enregistrement_demo.mp4"
    subprocess.run([sources.find_ffmpeg(), "-v", "error", "-y", "-framerate", "25", "-i", str(tmp / "f_%03d.png"),
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "12", str(mp4)], check=True)
    Image.open(tmp / ("f_%03d.png" % 30)).save(out_dir / "apercu_enregistrement.png")
    for f in tmp.glob("*.png"):
        f.unlink()
    tmp.rmdir()
    print("ecrit", mp4)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
