"""Tests de l'enregistrement (codecs reels par ffmpeg).

Usage : python tests/test_record.py   (depuis le dossier CameraSim_AE)
"""
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import record                                  # noqa: E402
from camsim.exr_io import read_exr                         # noqa: E402
from camsim.job import run_job                             # noqa: E402
from camsim.pipeline import DEFAULTS                       # noqa: E402

results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


def fmt(name, **kw):
    return record.params(dict(DEFAULTS, rec_format=name, **kw))


# 1. Chaque format rend un gris juste (pas de derive de niveau ni de teinte).
flat = np.full((240, 320, 3), 0.18)
errs = {}
for name in record.FORMATS:
    o = record.record_sequence([flat] * 3, fmt(name), 25)[2][40:-40, 40:-40]
    errs[name] = float(np.abs(o / 0.18 - 1).max())
ok = all(v < (0.05 if k == "mjpeg" else 0.02) for k, v in errs.items())
check("gris juste dans chaque format", ok, ", ".join("%s %.1f %%" % (k, 100 * v) for k, v in errs.items()))

# 2. Cadrage sans deformation : une image carree dans le cadre 4:3 du DV PAL.
hr, wr, y0, x0 = record.region(record.FORMATS["dv_pal"], 1920, 1920)
check("cadrage dans le format", (hr, wr) == (576, 540) and x0 == 90,
      "image carree : %d x %d pixels DV (pixels de 16/15), centree" % (wr, hr))

# 3. Couleur sous-echantillonnee : le DV NTSC (4:1:1) perd plus de couleur horizontale que le DV PAL (4:2:0).
img = np.full((480, 720, 3), 0.18)
for x in range(0, 720, 8):
    img[:, x:x + 4] = (0.30, 0.10, 0.10)
    img[:, x + 4:x + 8] = (0.10, 0.20, 0.10)


def chroma_amp(out):
    c = out[100:380, 100:620, 0] - out[100:380, 100:620, 1]
    return float(c.std())


a_pal = chroma_amp(record.record_sequence([img] * 2, fmt("dv_pal"), 25)[1])
a_ntsc = chroma_amp(record.record_sequence([img] * 2, fmt("dv_ntsc"), 25)[1])
check("couleur sous-echantillonnee", a_ntsc < 0.7 * a_pal, "rayures colorees de 4 px : contraste de couleur %.3f en 4:2:0, %.3f en 4:1:1"
      % (a_pal, a_ntsc))

# 4. Debit : un debit 10 fois plus bas abime plus l'image.
rng = np.random.default_rng(0)
tex = np.clip(0.18 + 0.1 * cv2.GaussianBlur(rng.standard_normal((288, 352)), (0, 0), 1.5), 0, 1)[..., None] * np.ones(3)
frames = [np.roll(tex, 3 * k, axis=1) for k in range(12)]
# (Le MPEG-2 de ffmpeg a un plancher : son quantificateur plafonne. On mesure donc sur le H.264.)
e1 = np.mean([np.abs(o - f).mean() for o, f in zip(record.record_sequence(frames, fmt("h264_phone"), 25), frames)])
e2 = np.mean([np.abs(o - f).mean() for o, f in zip(record.record_sequence(frames, fmt("h264_phone", rec_bitrate_scale=0.01), 25), frames)])
check("debit", e2 > 1.5 * e1, "ecart moyen a la source : %.4f a 17 Mbit/s, %.4f a 0,17 Mbit/s" % (e1, e2))

# 5. Erreurs de flux : l'image est abimee, et de la meme facon a chaque calcul.
c1 = record.record_sequence(frames, fmt("dv_pal", rec_errors=0.0005), 25)
c2 = record.record_sequence(frames, fmt("dv_pal", rec_errors=0.0005), 25)
clean = record.record_sequence(frames, fmt("dv_pal"), 25)
dmg = np.mean([np.abs(a - b).max() for a, b in zip(c1, clean)])
same = all(np.array_equal(a, b) for a, b in zip(c1, c2))
check("erreurs de flux", dmg > 0.05 and same, "ecart maximal moyen %.2f avec 1 octet sur 2000 abime ; resultat reproductible : %s" % (dmg, same))

# 6. Paquets perdus : des images restent figees.
moving = [np.full((144, 176, 3), 0.05 + 0.02 * k) for k in range(30)]
d = record.record_sequence(moving, fmt("dv_pal", rec_drop=0.3), 25)
frozen = sum(1 for k in range(1, 30) if np.allclose(d[k], d[k - 1], atol=1e-4))
check("paquets perdus", len(d) == 30 and frozen >= 4, "%d images figees sur 30 (30 %% de paquets perdus)" % frozen)

# 7. Cadence du format : le 3GP enregistre 15 images/s ; relu a 25, certaines images sont repetees.
g = record.record_sequence(moving, fmt("3gp"), 25)
rep = sum(1 for k in range(1, 30) if np.allclose(g[k], g[k - 1], atol=1e-3))
g2 = record.record_sequence(moving, fmt("3gp", rec_native_fps=False), 25)
rep2 = sum(1 for k in range(1, 30) if np.allclose(g2[k], g2[k - 1], atol=1e-3))
check("cadence du format", len(g) == 30 and rep >= 9 and rep2 == 0,
      "15 im/s relues a 25 : %d images repetees sur 30 ; a la cadence de la sequence : %d" % (rep, rep2))

# 8. Rendu complet depuis le lanceur : sortie enregistree, sans alpha.
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    src = td / "src"
    src.mkdir()
    for n in range(1, 9):
        im = np.zeros((96, 128, 4), dtype=np.uint16)
        im[..., :3] = 20000 + 3000 * n
        im[..., 3] = 65535
        cv2.imwrite(str(src / ("p_%02d.png" % n)), im)
    job = {"src_file": str(src / "p_01.png"), "mode": "render", "out_dir": str(td / "out"), "first": 2, "last": 7,
           "profile": "icx409ak_camcorder", "settings": {"iso": 1250, "rec_format": "dv_pal"}, "status": str(td / "s.txt")}
    rc = run_job(job)
    outs = sorted((td / "out").glob("*.exr"))
    rgb, alpha, _ = read_exr(outs[0]) if outs else (None, "?", None)
    check("rendu avec enregistrement", rc == 0 and len(outs) == 6 and alpha is None and rgb.shape == (96, 128, 3),
          "%d images, sans couche alpha (les codecs n'en ont pas)" % len(outs))
    job = dict(job, mode="preview", preview_png=str(td / "apercu.png"), status=str(td / "s2.txt"))
    check("apercu avec enregistrement", run_job(job) == 0 and (td / "apercu.png").exists(), "planche ecrite")

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
