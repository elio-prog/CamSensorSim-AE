"""Demonstration du signal analogique (bande, composite) sur l'animation du rendu (50 images).

Une mire de barres de couleur est AJOUTEE en bas de l'image pour la demonstration.

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

from camsim import CameraSim, analog, sources      # noqa: E402
from camsim.color import preview_srgb              # noqa: E402
from camsim.exr_io import read_exr                 # noqa: E402
from camsim.pipeline import DEFAULTS               # noqa: E402

S = 480
VARIANTS = [
    ("Camescope, sans analogique", None),
    ("VHS (SP), PAL composite", dict(analog_standard="pal", analog_tape="vhs")),
    ("Hi8, PAL, decodeur en peigne", dict(analog_standard="pal", analog_tape="hi8", analog_decoder="comb")),
    ("NTSC composite seul, teinte +15", dict(analog_standard="ntsc", analog_hue_deg=15)),
    ("VHS (LP), 3e copie, pertes", dict(analog_standard="pal", analog_tape="vhs_lp", analog_generations=3,
                                         analog_dropouts=6, analog_sharpness=0.8)),
    ("VHS use : calage, pertes non masquees", dict(analog_standard="ntsc", analog_tape="vhs_lp", analog_tracking=0.7,
                                                   analog_dropouts=15, analog_dropout_comp=False, analog_jitter_us=0.25,
                                                   analog_head_switch_lines=10, analog_link_snr_db=32)),
]
BARS = [(0.18, 0.18, 0.18), (0.30, 0.28, 0.02), (0.03, 0.22, 0.25), (0.04, 0.20, 0.03),
        (0.24, 0.03, 0.22), (0.25, 0.02, 0.02), (0.02, 0.03, 0.30), (0.01, 0.01, 0.01)]


def main(seq_dir, out_dir):
    out_dir = Path(out_dir)
    files = sorted(Path(seq_dir).glob("*.exr"))
    sim = CameraSim("icx409ak_camcorder", iso=1250, fps=25)
    cam = []
    prev = None
    for k, f in enumerate(files):
        rgb = cv2.resize(read_exr(f)[0], (S, S), interpolation=cv2.INTER_AREA)
        bw = S // len(BARS)
        for i, c in enumerate(BARS):
            rgb[S - 70:S - 10, i * bw:(i + 1) * bw] = c
        o, _ = sim.process_frame(rgb, k, prev=prev, nxt=None)
        prev = rgb
        cam.append(o)
    print("camescope :", len(cam), "images")
    outs = []
    for label, kw in VARIANTS:
        if kw is None:
            outs.append(cam)
            continue
        p = analog.params(dict(DEFAULTS, **kw))
        outs.append([analog.apply(c, p, k) for k, c in enumerate(cam)])
        print(label, "fini")
    tmp = out_dir / "_ana_frames"
    tmp.mkdir(parents=True, exist_ok=True)
    for k in range(len(cam)):
        tiles = []
        for (label, _), o in zip(VARIANTS, outs):
            im = Image.fromarray(preview_srgb(o[k], 1.0))
            ImageDraw.Draw(im).text((6, 6), label, fill=(255, 255, 0))
            tiles.append(np.asarray(im))
        grid = np.concatenate([np.concatenate(tiles[:3], axis=1), np.concatenate(tiles[3:], axis=1)], axis=0)
        Image.fromarray(grid).save(tmp / ("f_%03d.png" % k))
    mp4 = out_dir / "analogique_demo.mp4"
    subprocess.run([sources.find_ffmpeg(), "-v", "error", "-y", "-framerate", "25", "-i", str(tmp / "f_%03d.png"),
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "12", str(mp4)], check=True)
    Image.open(tmp / ("f_%03d.png" % 30)).save(out_dir / "apercu_analogique.png")
    for f in tmp.glob("*.png"):
        f.unlink()
    tmp.rmdir()
    print("ecrit", mp4)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
