"""Lanceur utilise par le panneau After Effects : execute un travail decrit dans un fichier JSON.

  python -m camsim.job --job travail.json

Champs du travail :
  src_file     un fichier de la source : sequence EXR (ACEScg lineaire), sequence ou image
               PNG / TIFF / JPEG, ou video (voir sources.py)
  mode         "render" (sequence en sortie) ou "preview" (une planche PNG avant / apres)
  out_dir      dossier de sortie (render) ; jamais le dossier source
  preview_png  fichier PNG a ecrire (preview)
  first, last  numeros de la premiere et de la derniere image a traiter
  profile      nom du profil de camera
  settings     reglages qui different des valeurs par defaut
  status       fichier d'etat lu par le panneau (lignes cle=valeur)
  still        vrai pour une image fixe (un seul fichier, image numero 0)
  warmup       nombre d'images traitees avant la premiere, sans les ecrire, pour que les
               cameras a memoire (tubes) soient dans leur etat normal

Le dossier source n'est jamais modifie.
"""
import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                          # noqa: E402

from camsim.exr_io import write_exr                        # noqa: E402
from camsim.pipeline import DEFAULTS, CameraSim            # noqa: E402
from camsim.sources import input_params, numbered, open_source, scene_to_display  # noqa: E402
from camsim import record                                  # noqa: E402


class Status:
    def __init__(self, path):
        self.path = Path(path) if path else None

    def write(self, **kw):
        if self.path is None:
            return
        text = "".join("%s=%s\n" % (k, str(v).replace("\r", " ").replace("\n", " ")) for k, v in kw.items())
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)


def sequence_files(src_file):
    """Renvoie {numero: chemin} pour la sequence EXR a laquelle appartient src_file."""
    return numbered(Path(src_file), {".exr"})


def clean_settings(raw):
    """Garde les reglages connus et leur donne le bon type."""
    out = {}
    for k, v in (raw or {}).items():
        if k not in DEFAULTS:
            raise ValueError("reglage inconnu : %s" % k)
        d = DEFAULTS[k]
        if v is None or v == "":
            if d is None or isinstance(d, str):
                if isinstance(d, str) and v == "":
                    out[k] = ""
                continue
            continue
        if isinstance(d, bool):
            v = bool(v)
        elif isinstance(d, int):
            v = int(round(float(v)))
        elif isinstance(d, float):
            v = float(v)
        elif d is None and k != "interlace":
            v = float(v)
        out[k] = v
    return out


def run_frame(sim, loader, n, need_neighbors):
    """loader : une source (sources.py). Renvoie (scene, sortie, alpha) ou None si l'image est illisible."""
    cur = loader.get(n)
    if cur is None:
        return None
    rgb, alpha, _ = cur
    prev = nxt = None
    if need_neighbors:
        p_, n_ = loader.get(n - 1), loader.get(n + 1)
        prev = p_[0] if p_ is not None and p_[0].shape == rgb.shape else None
        nxt = n_[0] if n_ is not None and n_[0].shape == rgb.shape else None
    out, alpha_out = sim.process_frame(rgb, frame=n, alpha=alpha, prev=prev, nxt=nxt)
    return rgb, out, alpha_out


def preview_sheet(before, after, dst, tile=480):
    """Planche avant / apres : image entiere en haut, detail a 100 % en bas."""
    from PIL import Image, ImageDraw

    from camsim.color import preview_srgb
    h, w, _ = before.shape
    lum = before.mean(axis=-1)
    ch, cw = min(tile // 2, h), min(tile, w)
    best, by, bx = -1.0, 0, 0
    for y in range(0, h - ch + 1, max(ch // 2, 1)):
        for x in range(0, w - cw + 1, max(cw // 2, 1)):
            win = lum[y:y + ch, x:x + cw]
            score = win.std() * min(win.mean(), 0.2)
            if score > best:
                best, by, bx = score, y, x
    pos = lum[lum > 0]
    expo = float(min(max(0.18 / max(np.percentile(pos, 75), 1e-4), 1.0), 16.0)) if pos.size else 1.0
    fw = tile
    fh = max(int(round(tile * h / w)), 1)
    pad, head = 6, 22
    sheet = Image.new("RGB", (2 * fw + 3 * pad, head + fh + ch + 3 * pad), (28, 28, 28))
    d = ImageDraw.Draw(sheet)
    for i, (label, img) in enumerate((("Avant", before), ("Apres", after))):
        x = pad + i * (fw + pad)
        d.text((x + 2, 5), label, fill=(230, 230, 230))
        sheet.paste(Image.fromarray(preview_srgb(img, expo)).resize((fw, fh), Image.LANCZOS), (x, head))
        crop = Image.fromarray(preview_srgb(img[by:by + ch, bx:bx + cw], expo))
        sheet.paste(crop, (x + (fw - cw) // 2, head + fh + pad))
    sheet.save(dst)


def write_png16(dst, ap1, alpha, params):
    """Sortie dans le meme codage que la source (courbe, tonalite, exposition), PNG 16 bits."""
    import cv2
    v = scene_to_display(ap1, params)
    img = (v[..., ::-1] * 65535.0 + 0.5).astype(np.uint16)
    if alpha is not None:
        a = (np.clip(alpha, 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)
        img = np.concatenate([img, a[..., None]], axis=-1)
    if not cv2.imwrite(str(dst), img):
        raise IOError("ecriture impossible : %s" % dst)


def run_job(job):
    status = Status(job.get("status"))
    source = None
    try:
        settings = clean_settings(job.get("settings"))
        source = open_source(job["src_file"], {**DEFAULTS, **settings})
        if job.get("still") and source.kind != "video":
            source.files = {0: source.src}              # image fixe : ce fichier seul, meme s'il porte un numero
        numbers = source.numbers
        if not numbers:
            raise ValueError("aucune image trouvee pour %s" % job["src_file"])
        first = int(job.get("first", numbers[0]))
        last = int(job.get("last", first if job.get("mode") == "preview" else numbers[-1]))
        todo = [n for n in numbers if first <= n <= last]
        if not todo:
            raise ValueError("aucune image entre %d et %d" % (first, last))
        sim = CameraSim(job.get("profile", "sonya7s2"), **settings)
        need = sim.needs_neighbors()
        mode = job.get("mode", "render")
        total = len(todo)
        status.write(state="running", phase="demarrage", frame=0, total=total)

        # Mise en etat des cameras a memoire : on traite quelques images avant la premiere.
        # Automatismes : deux secondes d'images avant la premiere, pour que l'exposition et la
        # balance des blancs aient le temps de se regler (comme une camera deja en marche).
        default_warm = 6 if sim.tube is not None else 0
        if sim.auto.active:
            secs = 1.0 if job.get("mode") == "preview" else 2.0      # apercu : une seconde suffit, plus rapide
            default_warm = max(default_warm, int(round(secs * float(sim.settings["fps"]))))
        warm = int(job.get("warmup", default_warm))
        # Enregistrement (codec) : les images de la camera sont poussees dans l'ordre, puis relues.
        rec_params = record.params({**DEFAULTS, **settings})
        rec = {"obj": None, "pushed": 0, "last": None}

        def push(img):
            if rec_params is None:
                return
            if rec["obj"] is None:
                rec["obj"] = record.Recorder(rec_params, img.shape[0], img.shape[1], float(sim.settings["fps"]))
            rec["obj"].push(img)
            rec["pushed"] += 1
            rec["last"] = img

        if warm > 0:
            before = [n for n in numbers if n < todo[0]][-warm:]
            seq = before if before else [todo[0]] * warm
            for i, n in enumerate(seq):
                status.write(state="running", phase="mise en etat", frame=i + 1, total=len(seq))
                r = run_frame(sim, source, n, need)
                if r is not None:
                    push(r[1])
        n_warm = rec["pushed"]

        if mode == "preview":
            res = run_frame(sim, source, todo[0], need)
            if res is None:
                raise ValueError("image %d illisible (rendu en cours ?)" % todo[0])
            dst = Path(job["preview_png"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            after = res[1]
            if rec_params is not None:
                push(after)
                status.write(state="running", phase="enregistrement", frame=1, total=1)
                for after in rec["obj"].finish():
                    pass                                    # derniere image relue = image demandee
            if after.shape[:2] != res[0].shape[:2]:          # sortie a la taille du capteur : agrandie pour la planche
                from camsim.resolution import to_image
                after = to_image(after, res[0].shape[0], res[0].shape[1], "nearest")
            preview_sheet(res[0], after, dst)
            status.write(state="done", mode="preview", output=str(dst), frame=1, total=1)
            return 0

        out_dir = Path(job["out_dir"])
        if out_dir.resolve() == source.folder():
            raise ValueError("le dossier de sortie est le dossier source")
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = job.get("suffix", "_cam")
        fmt = settings.get("output_format", DEFAULTS["output_format"])
        if fmt not in ("exr", "png16"):
            raise ValueError("output_format inconnu : %r" % fmt)
        params = source.params or input_params({**DEFAULTS, **settings}, "image")
        first_out, done, skipped = None, 0, 0
        t0 = time.time()

        def write(n, img, alpha):
            if fmt == "exr":
                dst = out_dir / (source.stem(n) + suffix + ".exr")
                write_exr(dst, img, alpha)
            else:
                dst = out_dir / (source.stem(n) + suffix + ".png")
                write_png16(dst, img, alpha, params)
            return dst

        recorded = []                                       # numeros des images a relire apres l'enregistrement
        for i, n in enumerate(todo):
            res = run_frame(sim, source, n, need)
            if res is None:
                skipped += 1
                if rec_params is not None and rec["last"] is not None:
                    push(rec["last"])                       # la bande continue : l'image precedente est repetee
                    recorded.append(None)
            elif rec_params is not None:
                push(res[1])
                recorded.append(n)
            else:
                dst = write(n, res[1], res[2])
                first_out = first_out or dst
                done += 1
            per = (time.time() - t0) / (i + 1)
            status.write(state="running", phase="rendu", frame=i + 1, total=total,
                         remaining_s=int(per * (total - i - 1)))
        if rec_params is not None and rec["obj"] is not None:
            # Les codecs n'ont pas de couche alpha : la sortie enregistree n'en a pas non plus.
            status.write(state="running", phase="enregistrement", frame=0, total=len(recorded))
            frames = rec["obj"].finish()
            for _ in range(n_warm):
                next(frames, None)
            for k, n in enumerate(recorded):
                img = next(frames, None)
                if n is None or img is None:
                    continue
                dst = write(n, img, None)
                first_out = first_out or dst
                done += 1
                status.write(state="running", phase="enregistrement", frame=k + 1, total=len(recorded))
        if first_out is None:
            raise ValueError("aucune image n'a pu etre lue")
        (out_dir / "reglages.json").write_text(
            json.dumps({"profile": job.get("profile"), "source": str(job["src_file"]), "source_kind": source.kind,
                        "input": source.params, "settings": sim.settings, "camera": sim.describe(),
                        "enregistrement": rec_params},
                       indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        status.write(state="done", mode="render", output=str(first_out), frame=done, total=total,
                     skipped=skipped, first=todo[0])
        return 0
    except Exception as e:
        traceback.print_exc()
        status.write(state="error", message="%s : %s" % (type(e).__name__, e))
        return 1
    finally:
        if source is not None:
            source.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="CameraSim : travail lance par le panneau After Effects")
    ap.add_argument("--job", required=True)
    a = ap.parse_args(argv)
    job = json.loads(Path(a.job).read_text(encoding="utf-8-sig"))
    return run_job(job)


if __name__ == "__main__":
    sys.exit(main())
