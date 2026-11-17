"""Compare les CCD (camera 3 CCD, camescope a mosaique complementaire) du coeur natif au moteur Python.

Usage : python tests/compare_native_ccd.py   (apres compilation de native/)
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from camsim import CameraSim                          # noqa: E402

CLI = ROOT / "native" / "build" / "core" / "Release" / "camsim_cli.exe"
CCD3, CAM = 5, 6                                       # rang des profils dans native/core/profiles_data.h
results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


def native(img, frame, **kw):
    h, w, _ = img.shape
    with tempfile.TemporaryDirectory() as td:
        a, b = Path(td) / "in.raw", Path(td) / "out.raw"
        img.astype("<f4").tofile(a)
        args = [str(CLI), str(a), str(b), str(w), str(h), str(frame)] + ["%s=%s" % (k, v) for k, v in kw.items()]
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        return np.fromfile(b, dtype="<f4").reshape(h, w, 3).astype(np.float64)


DET_PY = dict(shot_noise=False, read_noise_model="off", row_noise_gain=0.0, prnu_sigma=0.0, dark_current_e_per_s=0.0,
              quantize=False, olpf_h_px=0.0, olpf_v_px=0.0, clip_highlights=False, dsp_enabled="off")
DET_C = dict(shot_noise=0, read_model=2, row_noise_gain=0, prnu_sigma=0, dark_current=0, quantize=0, olpf_h_px=0,
             olpf_v_px=0, clip_highlights=0)

h, w = 64, 96
yy, xx = np.mgrid[0:h, 0:w] / np.array([h, w])[:, None, None]
img = np.stack([0.02 + 0.5 * xx, 0.02 + 0.4 * yy, 0.05 + 0.3 * (1 - xx)], -1)
img[10:30, 10:40] = (0.35, 0.05, 0.03)
img[36:56, 50:80] = (0.03, 0.06, 0.30)
img[:, 85:] = np.where((np.arange(w)[85:] % 2) == 0, 0.5, 0.05)[None, :, None]
img = img.astype(np.float32).astype(np.float64)
hot = img.copy()
hot[28:32, 60:64] = 400.0                              # source tres lumineuse : trainee, eblouissement


def compare(name, prof_py, prof_c, py, c, image=img, tol=2e-4):
    a = CameraSim(prof_py, **dict(DET_PY, **py)).process(image, 2)
    b = native(image, 2, profile=prof_c, **dict(DET_C, **c))
    e = float(np.abs(a - b).max() / np.abs(a).max())
    check(name, e < tol, "ecart maximal %.1e" % e)


# 1. Camera 3 CCD, sans hasard.
compare("3 CCD", "kai0373_3ccd", CCD3, dict(iso=400), dict(iso=400))
compare("3 CCD entrelace", "kai0373_3ccd", CCD3, dict(iso=400, interlace="fields"), dict(iso=400, interlace=1))
compare("3 CCD, prisme desaligne", "kai0373_3ccd", CCD3, dict(iso=400, registration_r_dx=2.5, registration_b_dy=-1.7),
        dict(iso=400, registration_r_dx=2.5, registration_b_dy=-1.7), tol=1e-3)
compare("3 CCD, trainee verticale", "kai0373_3ccd", CCD3, dict(iso=400, ccd_smear_gain=50.0, exposure_time_s=0.001),
        dict(iso=400, ccd_smear_gain=50, exposure_time_s=0.001), image=hot)
compare("3 CCD, eblouissement", "kai0373_3ccd", CCD3, dict(iso=400, ccd_antiblooming=1.0, clip_full_well=True),
        dict(iso=400, ccd_antiblooming=1, clip_full_well=1), image=hot)
compare("3 CCD, transfert imparfait", "kai0373_3ccd", CCD3, dict(iso=400, ccd_cti_v=3e-4, ccd_cti_h=3e-4),
        dict(iso=400, ccd_cti_v=3e-4, ccd_cti_h=3e-4))
compare("3 CCD, ombrage", "kai0373_3ccd", CCD3, dict(iso=400, sensor_shading=0.3), dict(iso=400, sensor_shading=0.3))

# 2. Camescope, sans hasard.
compare("camescope", "icx409ak_camcorder", CAM, dict(iso=1250), dict(iso=1250))
compare("camescope, couleur 2H", "icx409ak_camcorder", CAM, dict(iso=1250, cmyg_chroma_vertical="2H"),
        dict(iso=1250, cmyg_vertical_2h=1))
compare("camescope, couleur etroite et forte", "icx409ak_camcorder", CAM,
        dict(iso=1250, cmyg_chroma_bw_mhz=0.4, cmyg_chroma_gain=1.6), dict(iso=1250, cmyg_chroma_bw_mhz=0.4, cmyg_chroma_gain=1.6))
compare("camescope, defauts de la fiche", "icx409ak_camcorder", CAM,
        dict(iso=1250, cmyg_field_flicker=0.02, cmyg_line_crawl=0.03, sensor_shading=0.2),
        dict(iso=1250, cmyg_field_flicker=0.02, cmyg_line_crawl=0.03, sensor_shading=0.2))
compare("camescope, trainee et eblouissement", "icx409ak_camcorder", CAM,
        dict(iso=1250, ccd_smear_gain=200.0, ccd_antiblooming=1.0, clip_full_well=True),
        dict(iso=1250, ccd_smear_gain=200, ccd_antiblooming=1, clip_full_well=1), image=hot)

# 3. Avec le bruit : memes niveaux et meme bruit.
flat = np.full((128, 128, 3), 0.18)
for name, key, idx, kw in (("3 CCD avec bruit", "kai0373_3ccd", CCD3, dict(iso=1600)),
                           ("camescope avec bruit", "icx409ak_camcorder", CAM, dict(iso=5000))):
    a = CameraSim(key, dsp_enabled="off", olpf_h_px=0.0, olpf_v_px=0.0, **kw).process(flat, 3)[8:-8, 8:-8]
    b = native(flat, 3, profile=idx, olpf_h_px=0, olpf_v_px=0, **kw)[8:-8, 8:-8]
    dm = np.abs(a.mean((0, 1)) / b.mean((0, 1)) - 1).max()
    ds = np.abs(a.std((0, 1)) / b.std((0, 1)) - 1).max()
    check(name, dm < 0.01 and ds < 0.08, "moyenne a %.2f %% pres, bruit a %.1f %% pres" % (100 * dm, 100 * ds))

print("\n%d / %d comparaisons reussies" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
