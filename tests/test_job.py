"""Tests du lanceur utilise par le panneau After Effects.

Usage : python tests/test_job.py [dossier_de_sequence_EXR]
Sans dossier, une petite sequence EXR de synthese est fabriquee.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from camsim.exr_io import read_exr, write_exr       # noqa: E402
from camsim.pipeline import DEFAULTS, PROFILE_DIR   # noqa: E402
from camsim.schema import all_keys, build_schema    # noqa: E402

results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


# 1. Chaque reglage du moteur est decrit une fois et une seule dans l'interface.
keys = all_keys()
missing = sorted(set(DEFAULTS) - set(keys))
extra = sorted(set(keys) - set(DEFAULTS))
dup = sorted({k for k in keys if keys.count(k) > 1})
check("description des reglages", not missing and not extra and not dup,
      "%d reglages decrits ; manquants %s, inconnus %s, en double %s" % (len(keys), missing, extra, dup))
schema_file = ROOT / "ae" / "settings_schema.json"
on_disk = json.loads(schema_file.read_text(encoding="ascii")) if schema_file.exists() else None
fresh = build_schema(on_disk["profiles"] if on_disk else [])
check("fichier lu par le panneau a jour", on_disk is not None and on_disk["groups"] == json.loads(json.dumps(fresh["groups"]))
      and {p["key"] for p in on_disk["profiles"]} == {f.stem for f in PROFILE_DIR.glob("*.json")},
      "settings_schema.json correspond au moteur et liste tous les profils")

tmp = Path(tempfile.mkdtemp(prefix="camsim_job_"))
if len(sys.argv) > 1:
    src_dir = Path(sys.argv[1])
    files = sorted(src_dir.glob("*.exr"))
    src_file, first = files[2], None
else:
    src_dir = tmp / "src"
    src_dir.mkdir()
    yy, xx = np.mgrid[0:96, 0:128] / 127.0
    for i in range(6):
        img = np.stack([0.1 + 0.4 * xx, 0.1 + 0.3 * yy, 0.2 + 0.0 * xx], axis=-1)
        img = np.roll(img, 4 * i, axis=1)
        write_exr(src_dir / ("plan_v001_%04d.exr" % (1001 + i)), img, np.ones((96, 128), dtype=np.float32))
    src_file = src_dir / "plan_v001_1003.exr"
import re                                            # noqa: E402
num = int(re.search(r"(\d+)\.exr$", src_file.name).group(1))
py = sys.executable


def launch(job, name):
    jf = tmp / (name + ".json")
    job = dict(job, status=str(tmp / (name + ".status.txt")))
    jf.write_text(json.dumps(job), encoding="utf-8")
    r = subprocess.run([py, "-m", "camsim.job", "--job", str(jf)], cwd=str(ROOT / "engine"),
                       capture_output=True, text=True)
    st = dict(line.split("=", 1) for line in Path(job["status"]).read_text(encoding="utf-8").splitlines() if "=" in line)
    return r, st


# 2. Apercu.
r, st = launch({"src_file": str(src_file), "mode": "preview", "first": num, "profile": "sonya7s2",
                "settings": {"iso": 6400, "adc_stuck_bits": "", "tube_beam_nA": None},
                "preview_png": str(tmp / "apercu.png")}, "apercu")
check("apercu", r.returncode == 0 and st.get("state") == "done" and Path(st.get("output", "x")).exists(),
      "planche ecrite (%s)" % (Path(st.get("output", "?")).name if st.get("state") == "done" else st.get("message", r.stderr[-200:])))

# 3. Rendu de deux images, avec une camera a memoire et ses images voisines.
for profile, settings in (("sonya7s2", {"iso": 3200, "readout_time_s": 0.02}), ("plumbicon_xq1410", {}),
                          ("kai0373_3ccd", {"interlace": "fields"})):
    out_dir = tmp / ("out_" + profile)
    r, st = launch({"src_file": str(src_file), "mode": "render", "first": num, "last": num + 1, "profile": profile,
                    "settings": settings, "out_dir": str(out_dir)}, "rendu_" + profile)
    outs = sorted(out_dir.glob("*_cam.exr")) if out_dir.exists() else []
    ok = r.returncode == 0 and st.get("state") == "done" and len(outs) == 2
    detail = st.get("message", r.stderr[-300:])
    if ok:
        a, alpha, hdr = read_exr(outs[0])
        src_rgb, _, _ = read_exr(src_file)
        ok = a.shape == src_rgb.shape and alpha is not None and np.isfinite(a).all() and hdr.get("colorInteropID") == "lin_ap1_scene"
        detail = "2 images EXR ACEScg ecrites, meme taille que la source, alpha conserve"
    check("rendu (%s)" % profile, ok, detail)

# 4. Garde-fous.
r, st = launch({"src_file": str(src_file), "mode": "render", "first": num, "last": num, "profile": "sonya7s2",
                "out_dir": str(src_file.parent)}, "refus")
check("refus d'ecrire dans le dossier source", st.get("state") == "error" and "source" in st.get("message", ""), st.get("message", "?"))
r, st = launch({"src_file": str(src_file), "mode": "render", "first": num, "last": num, "profile": "sonya7s2",
                "settings": {"reglage_bidon": 1}, "out_dir": str(tmp / "x")}, "inconnu")
check("reglage inconnu signale", st.get("state") == "error" and "reglage_bidon" in st.get("message", ""), st.get("message", "?"))

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
