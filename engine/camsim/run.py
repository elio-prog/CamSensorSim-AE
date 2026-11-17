"""Ligne de commande : fait passer une sequence EXR dans la camera simulee.

Exemple (depuis le dossier CameraSim_AE) :
  .venv/Scripts/python.exe -m camsim.run --in "D:/rendus/plan" --out "out/plan_iso6400" \
      --profile sonya7s2 --set iso=6400 --set ev=0

Le dossier d'entree n'est jamais modifie. Les images illisibles (rendu en cours
d'ecriture) sont ignorees et signalees.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camsim.exr_io import read_exr, write_exr          # noqa: E402
from camsim.pipeline import DEFAULTS, CameraSim         # noqa: E402


def parse_value(key, text):
    default = DEFAULTS.get(key)
    if text.lower() in ("none", "null"):
        return None
    if isinstance(default, bool):
        return text.lower() in ("1", "true", "oui", "yes", "on")
    if isinstance(default, str):
        return text
    try:
        return int(text) if re.fullmatch(r"[+-]?\d+", text) and isinstance(default, int) else float(text)
    except ValueError:
        return text


def frame_number(path):
    m = re.search(r"(\d+)(?=\.[^.]+$)", path.name)
    return int(m.group(1)) if m else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="CameraSim : sequence EXR -> camera simulee")
    ap.add_argument("--in", dest="src", required=True, help="dossier ou fichier EXR d'entree (ACEScg lineaire)")
    ap.add_argument("--out", dest="dst", required=True, help="dossier de sortie")
    ap.add_argument("--profile", default="sonya7s2")
    ap.add_argument("--frames", default=None, help="plage d'images, par exemple 1001-1010")
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="CLE=VALEUR")
    ap.add_argument("--suffix", default="_cam")
    a = ap.parse_args(argv)

    src, dst = Path(a.src), Path(a.dst)
    files = [src] if src.is_file() else sorted(src.glob("*.exr"))
    if a.frames:
        lo, _, hi = a.frames.partition("-")
        lo, hi = int(lo), int(hi or lo)
        files = [f for f in files if lo <= frame_number(f) <= hi]
    if not files:
        print("aucune image EXR trouvee dans", src)
        return 1
    if dst.resolve() == files[0].parent.resolve():
        print("refus : le dossier de sortie est le dossier d'entree")
        return 1
    dst.mkdir(parents=True, exist_ok=True)

    settings = {}
    for item in a.sets:
        k, _, v = item.partition("=")
        settings[k.strip()] = parse_value(k.strip(), v.strip())
    sim = CameraSim(a.profile, **settings)
    info = sim.describe()
    print(json.dumps(info, indent=1, ensure_ascii=False))
    if not info.get("K_dans_plage_calibree", True):
        print("attention : cet ISO sort de la plage calibree du profil (extrapolation).")

    need_neighbors = sim.needs_neighbors()
    all_files = [src] if src.is_file() else sorted(src.glob("*.exr"))
    by_frame = {frame_number(f): f for f in all_files}
    cache = {}

    def load(n):
        """Lit l'image numero n (ou None si absente ou illisible), avec un petit cache."""
        if n not in cache:
            try:
                cache[n] = read_exr(by_frame[n]) if n in by_frame else None
            except Exception as e:                  # image en cours d'ecriture par le rendu
                print("ignoree (illisible) :", by_frame[n].name, "-", type(e).__name__)
                cache[n] = None
            for k in [k for k in cache if k < n - 2]:
                del cache[k]
        return cache[n]

    done = 0
    for f in files:
        t0 = time.time()
        n = frame_number(f)
        cur = load(n)
        if cur is None:
            continue
        rgb, alpha, _ = cur
        prev = nxt = None
        if need_neighbors:
            p_, n_ = load(n - 1), load(n + 1)
            prev = p_[0] if p_ is not None and p_[0].shape == rgb.shape else None
            nxt = n_[0] if n_ is not None and n_[0].shape == rgb.shape else None
        out, alpha_out = sim.process_frame(rgb, frame=n, alpha=alpha, prev=prev, nxt=nxt)
        write_exr(dst / (f.stem + a.suffix + ".exr"), out, alpha_out)
        done += 1
        print("%s  %.1f s" % (f.name, time.time() - t0))
    (dst / "reglages.json").write_text(
        json.dumps({"profile": a.profile, "settings": sim.settings, "capteur": info}, indent=1, ensure_ascii=False),
        encoding="utf-8")
    print("%d image(s) ecrite(s) dans %s" % (done, dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
