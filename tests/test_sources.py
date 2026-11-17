"""Tests des sources deja traitees (PNG, TIFF, videos) : mode d'entree B.

Usage : python tests/test_sources.py   (depuis le dossier CameraSim_AE)
Les videos d'essai sont fabriquees par ffmpeg dans un dossier temporaire.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import sources                                 # noqa: E402
from camsim.exr_io import read_exr                         # noqa: E402
from camsim.job import run_job                             # noqa: E402
from camsim.pipeline import DEFAULTS                       # noqa: E402

results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


S = dict(DEFAULTS)

# 1. Courbes de transfert : valeurs de reference des normes.
check("courbe sRGB", abs(sources.srgb_decode(np.array(0.5)) - 0.2140) < 1e-4 and
      abs(sources.srgb_decode(np.array(0.04045)) - 0.04045 / 12.92) < 1e-12,
      "0,5 -> %.4f (IEC 61966-2-1 : 0,2140)" % sources.srgb_decode(np.array(0.5)))
check("courbe BT.709", abs(sources.bt709_decode(np.array(0.081)) - 0.018) < 1e-4 and
      abs(sources.bt709_encode(np.array(1.0)) - 1.0) < 1e-12, "0,081 -> 0,018 et 1 -> 1 (ITU-R BT.709)")
x = np.linspace(0, 1, 101)
check("courbe de tonalite de Brooks", np.allclose(sources.smoothstep(sources.inverse_smoothstep(x)), x, atol=1e-12),
      "smoothstep(inverse(y)) = y")

# 2. Le blanc de l'ecran devient un blanc neutre de valeur 1 en ACEScg.
p = sources.input_params(S, "image")
w = sources.display_to_scene(np.array([[255, 255, 255]]), 256, dict(p, dither=False))
g = sources.display_to_scene(np.array([[128, 128, 128]]), 256, dict(p, dither=False))
check("blanc et gris neutres", np.allclose(w, 1.0, atol=1e-9) and np.ptp(g) < 1e-9,
      "blanc -> %s ; gris 128 -> %.4f sur les trois canaux" % (np.round(w[0], 6), g[0, 0]))

# 3. Aller-retour : source -> scene -> meme codage.
for tr in ("srgb", "bt709", "gamma22", "gamma24"):
    for tone in ("none", "smoothstep"):
        pp = sources.input_params(dict(S, input_transfer=tr, input_tone=tone, input_exposure_ev=1.0), "image")
        v = np.random.default_rng(0).integers(0, 65536, (32, 32, 3))
        back = sources.scene_to_display(sources.display_to_scene(v, 65536, dict(pp, dither=False)), pp)
        if not np.allclose(back, v / 65535.0, atol=1e-6):
            break
    else:
        continue
    break
check("aller-retour des codages", np.allclose(back, v / 65535.0, atol=1e-6),
      "4 courbes x 2 tonalites, exposition +1 : on retrouve la source")

# 4. Marches 8 bits : le tremblement les efface sans changer la moyenne.
ramp = np.repeat(np.arange(256)[None, :], 64, axis=0)[..., None].repeat(3, axis=-1)
lin0 = sources.display_to_scene(ramp, 256, dict(p, dither=False))
lin1 = sources.display_to_scene(ramp, 256, p, np.random.default_rng(1))
dev = np.abs(lin1.mean(axis=0) - lin0.mean(axis=0))[1:-1, 1].max()
check("tremblement d'un demi-pas", len(np.unique(lin1[..., 1])) > 50 * len(np.unique(lin0[..., 1])) and dev < 2e-3,
      "%d valeurs distinctes au lieu de %d ; moyenne par marche inchangee a %.1e pres"
      % (len(np.unique(lin1[..., 1])), len(np.unique(lin0[..., 1])), dev))

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    # 5. PNG 16 bits avec alpha.
    img = np.zeros((40, 50, 4), dtype=np.uint16)
    img[..., 2], img[..., 1], img[..., 0] = 60000, 30000, 10000           # BGR : rouge fort
    img[..., 3] = 32768
    seq = td / "png"
    seq.mkdir()
    for n in range(101, 105):
        cv2.imwrite(str(seq / ("plan_%04d.png" % n)), img)
    src = sources.open_source(seq / "plan_0102.png", dict(S, input_dequantize=False))
    rgb, alpha, info = src.get(103)
    want = sources.display_to_scene(np.array([[60000, 30000, 10000]]), 65536, dict(src.params, dither=False))
    check("sequence PNG 16 bits", src.numbers == [101, 102, 103, 104] and np.allclose(rgb[5, 5], want[0]) and
          abs(alpha.mean() - 32768 / 65535) < 1e-6, "4 images numerotees, couleurs et alpha relus")

    # 6. Video 8 bits generee par ffmpeg : nombre d'images, acces au hasard, gris neutre.
    ff = sources.find_ffmpeg()
    vid = td / "gris.mp4"
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=c=0x808080:s=64x48:r=25:d=0.4",
                    "-vf", "format=rgb24,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", "-color_trc", "bt709", "-color_primaries", "bt709", "-colorspace", "bt709",
                    str(vid)], check=True)
    vs = sources.open_source(vid, dict(S, input_dequantize=False))
    a7 = vs.get(7)[0]
    a3 = vs.get(3)[0]
    a4 = vs.get(4)[0]
    want = sources.bt709_decode(np.array(128 / 255.0))     # le passage par YUV 8 bits arrondit a +-1 code
    check("video : images et acces au hasard", len(vs.numbers) == 10 and a3 is not None and a4 is not None and a7 is not None,
          "%d images ; lecture de la 7e puis retour a la 3e" % len(vs.numbers))
    check("video : gris neutre BT.709", np.ptp(a4[20, 30]) < 0.01 and abs(a4[20, 30].mean() - want) < 0.01,
          "gris 128 -> %.4f (attendu %.4f, courbe BT.709 inverse)" % (a4[20, 30].mean(), want))
    vs.close()

    # 7. Video 10 bits (ProRes 422 HQ) : les 10 bits sont gardes.
    vid10 = td / "degrade.mov"
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "gradients=s=256x64:r=25:d=0.12:c0=0x000000:c1=0xffffff:x0=0:y0=0:x1=255:y1=0",
                    "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", str(vid10)], check=True)
    vs = sources.open_source(vid10, dict(S, input_dequantize=False))
    raw = vs.reader.get(0)
    levels = len(np.unique(raw[32, :, 1]))
    check("video 10 bits", sources.video_bits(vs.reader.info["pix_fmt"]) == 10 and levels > 200,
          "%s : %d niveaux distincts sur une ligne de 256 points" % (vs.reader.info["pix_fmt"], levels))
    vs.close()

    # 8. Rendu complet d'une sequence PNG, sortie PNG 16 bits dans le meme codage : sans bruit, on retrouve la source.
    flat = np.zeros((48, 64, 3), dtype=np.uint16)
    flat[...] = (20000, 40000, 30000)
    seq2 = td / "png2"
    seq2.mkdir()
    for n in range(1, 4):
        cv2.imwrite(str(seq2 / ("f_%03d.png" % n)), flat)
    quiet = dict(iso=100, shot_noise=False, read_noise_model="off", olpf_h_px=0.0, olpf_v_px=0.0, prnu_sigma=0.0,
                 dark_current_e_per_s=0.0, quantize=False, color_bias_gain=0.0, row_noise_gain=0.0,
                 input_dequantize=False, output_format="png16", spectral="off")
    job = {"src_file": str(seq2 / "f_001.png"), "mode": "render", "out_dir": str(td / "sortie"),
           "first": 1, "last": 3, "profile": "sonya7s2", "settings": quiet, "status": str(td / "etat.txt")}
    rc = run_job(job)
    outs = sorted((td / "sortie").glob("*.png"))
    back = cv2.imread(str(outs[0]), cv2.IMREAD_UNCHANGED) if outs else None
    ok = rc == 0 and len(outs) == 3 and back is not None and np.abs(back[10:-10, 10:-10].astype(float) - flat[10:-10, 10:-10]).max() < 300
    check("rendu PNG -> PNG 16 bits", ok, "3 images ; ecart maximal %s sur 65535 (camera sans bruit)" %
          (int(np.abs(back[10:-10, 10:-10].astype(float) - flat[10:-10, 10:-10]).max()) if back is not None else "?"))

    # 9. Rendu d'une video vers EXR, refus d'ecrire dans le dossier de la video.
    job = {"src_file": str(vid), "mode": "render", "out_dir": str(td / "sortie_video"), "first": 2, "last": 4,
           "profile": "icx409ak_camcorder", "settings": {"iso": 1250}, "status": str(td / "etat2.txt")}
    rc = run_job(job)
    exrs = sorted((td / "sortie_video").glob("*.exr"))
    meta = json.loads((td / "sortie_video" / "reglages.json").read_text(encoding="utf-8")) if exrs else {}
    check("rendu video -> EXR", rc == 0 and [f.name for f in exrs] == ["gris_00002_cam.exr", "gris_00003_cam.exr", "gris_00004_cam.exr"]
          and meta.get("source_kind") == "video" and read_exr(exrs[0])[0].shape == (48, 64, 3),
          "%d images nommees d'apres la video, codage d'entree note dans reglages.json" % len(exrs))
    job = dict(job, out_dir=str(td), status=str(td / "etat3.txt"))
    check("dossier de la video protege", run_job(job) == 1 and "dossier source" in (td / "etat3.txt").read_text(encoding="utf-8"),
          "refus d'ecrire a cote de la source")

    # 10. Image fixe dont le nom porte un numero, a cote d'autres fichiers numerotes.
    job = {"src_file": str(seq / "plan_0102.png"), "mode": "render", "out_dir": str(td / "sortie_fixe"), "still": True,
           "first": 0, "last": 0, "profile": "sonya7s2", "settings": {}, "status": str(td / "etat4.txt")}
    rc = run_job(job)
    outs = [f.name for f in (td / "sortie_fixe").glob("*.exr")]
    check("image fixe numerotee", rc == 0 and outs == ["plan_0102_cam.exr"], "une seule image rendue : %s" % outs)

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
