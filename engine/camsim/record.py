"""Enregistrement : le signal de la camera passe par un vrai codec (ffmpeg), puis est relu.

Chaine, pour chaque image sortie de la camera :
  ACEScg lineaire -> Rec.709 lineaire -> courbe BT.709 (signal video) -> image placee dans le
  cadre du format (sans deformation, forme reelle des pixels) -> Y'CbCr, sous-echantillonnage
  de la couleur -> codec -> [erreurs de flux] -> decodage -> retour en lumiere lineaire ACEScg
  -> taille du calque.

Deux familles de formats, a garder separees pour l'integration dans After Effects :
  "intra" : chaque image est codee seule (DV, MJPEG). Utilisable image par image, donc dans un
            futur effet en direct.
  "gop"   : groupes d'images (MPEG-2, H.264, H.263) : une image depend des precedentes et des
            suivantes. Il faut toute la sequence, dans l'ordre : calcul de sequence seulement.

Les erreurs de flux utilisent le filtre "noise" de ffmpeg sur les paquets compresses
(octets modifies, paquets perdus) : ce sont de vraies donnees abimees, decodees par un vrai
decodeur, avec ses propres masquages d'erreur.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

from .color import apply_matrix
from .resolution import sensor_grid
from .sources import AP1_TO_REC709, REC709_TO_AP1, bt709_decode, bt709_encode, find_ffmpeg

NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# size : pixels codes ; par : forme des pixels (largeur / hauteur) ; kind : "intra" ou "gop".
FORMATS = {
    "dv_pal": dict(
        label="DV / MiniDV PAL", size=(720, 576), par=16 / 15, fps=25, kind="intra", ext="dv", matrix="bt601",
        pix_fmt="yuv420p", interlaced=True, args=["-c:v", "dvvideo"],
        source="IEC 61834 / SMPTE 314M : 720 x 576, couleur 4:2:0, 25 Mbit/s fixes, compression DCT image par image, entrelace (trame basse en premier)."),
    "dv_ntsc": dict(
        label="DV / MiniDV NTSC", size=(720, 480), par=10 / 11, fps=30000 / 1001, kind="intra", ext="dv", matrix="bt601",
        pix_fmt="yuv411p", interlaced=True, args=["-c:v", "dvvideo"],
        source="IEC 61834 / SMPTE 314M : 720 x 480, couleur 4:1:1 (resolution de couleur divisee par 4 horizontalement), 25 Mbit/s."),
    "dvd": dict(
        label="DVD (MPEG-2)", size=(720, 576), par=16 / 15, fps=25, kind="gop", ext="m2v", matrix="bt601",
        pix_fmt="yuv420p", interlaced=True, bitrate=5.0e6, gop=12, bframes=2,
        args=["-c:v", "mpeg2video", "-maxrate", "9.8M", "-bufsize", "1835k"],
        source="DVD-Video : MPEG-2 MP@ML, 720 x 576, 4:2:0, debit video maximal 9,8 Mbit/s, groupes de 15 images au plus en PAL. Debit de 5 Mbit/s et groupes de 12 : valeurs courantes, libres."),
    "hdv": dict(
        label="HDV 1080 (MPEG-2)", size=(1440, 1080), par=4 / 3, fps=25, kind="gop", ext="m2v", matrix="bt709",
        pix_fmt="yuv420p", interlaced=True, bitrate=25.0e6, gop=15, bframes=2,
        args=["-c:v", "mpeg2video", "-maxrate", "25M", "-bufsize", "8M"],
        source="HDV 1080i : MPEG-2 MP@H-14, 1440 x 1080 (pixels 4:3, affiche en 1920), 4:2:0, 25 Mbit/s constants, groupes de 15 images."),
    "mjpeg": dict(
        label="Motion JPEG (appareil compact)", size=(640, 480), par=1.0, fps=30, kind="intra", ext="avi", matrix="bt601",
        pix_fmt="yuvj420p", interlaced=False, qscale=6, args=["-c:v", "mjpeg"],
        source="Video des appareils photo compacts des annees 2000 : Motion JPEG 640 x 480. Qualite (qscale 6) : valeur libre."),
    "h264_phone": dict(
        label="H.264 telephone 1080p", size=(1920, 1080), par=1.0, fps=30, kind="gop", ext="mp4", matrix="bt709",
        pix_fmt="yuv420p", interlaced=False, bitrate=17.0e6, gop=30, bframes=0,
        args=["-c:v", "libx264", "-profile:v", "high", "-preset", "veryfast"],
        source="Ordre de grandeur des telephones (1080p30, environ 17 Mbit/s, une image cle par seconde, pas d'images B) : valeurs courantes, libres."),
    "h264_dslr": dict(
        label="H.264 reflex 1080p (IPB)", size=(1920, 1080), par=1.0, fps=25, kind="gop", ext="mov", matrix="bt709",
        pix_fmt="yuv420p", interlaced=False, bitrate=45.0e6, gop=12, bframes=2,
        args=["-c:v", "libx264", "-profile:v", "high", "-preset", "fast"],
        source="Ordre de grandeur des reflex Canon en IPB (environ 45 Mbit/s, groupes d'une demi-seconde) : valeurs courantes, libres."),
    "3gp": dict(
        label="Telephone 3GP (H.263, QCIF)", size=(176, 144), par=12 / 11, fps=15, kind="gop", ext="3gp", matrix="bt601",
        pix_fmt="yuv420p", interlaced=False, bitrate=64.0e3, gop=15, bframes=0, args=["-c:v", "h263"],
        source="3GPP (TS 26.234) : H.263 en QCIF 176 x 144. 15 images/s et 64 kbit/s : valeurs courantes des premiers telephones."),
}


def params(settings):
    """Format choisi et reglages, ou None si l'enregistrement est coupe."""
    name = settings.get("rec_format", "off") or "off"
    if name == "off":
        return None
    if name not in FORMATS:
        raise ValueError("rec_format inconnu : %r" % name)
    f = dict(FORMATS[name])
    f["name"] = name
    f["bitrate_scale"] = float(settings.get("rec_bitrate_scale", 1.0))
    gop = settings.get("rec_gop")
    if gop not in (None, "") and f["kind"] == "gop":
        f["gop"] = int(gop)
    f["errors"] = float(settings.get("rec_errors", 0.0))
    f["drop"] = float(settings.get("rec_drop", 0.0))
    f["frame_rate_change"] = bool(settings.get("rec_native_fps", True))
    f["seed"] = int(settings.get("seed", 1))
    return f


def region(f, h, w):
    """Zone du cadre du format qui recoit l'image (h, w), sans deformation : (hr, wr, y0, x0)."""
    W, H = f["size"]
    hr, wr = sensor_grid({"sensor_pixels": [W, H], "pixel_pitch_um": [f["par"], 1.0], "name": f["label"]}, h, w)
    return hr, wr, (H - hr) // 2, (W - wr) // 2


class Recorder:
    """Pousser les images de la camera dans l'ordre, puis relire la sequence enregistree."""

    def __init__(self, f, h, w, fps):
        self.f, self.h, self.w, self.fps = f, h, w, float(fps)
        self.hr, self.wr, self.y0, self.x0 = region(f, h, w)
        self.dir = Path(tempfile.mkdtemp(prefix="camsim_rec_"))
        self.file = self.dir / ("enregistrement." + f["ext"])
        self.count = 0
        W, H = f["size"]
        enc_fps = f["fps"] if f["frame_rate_change"] else self.fps
        vf = "scale=out_color_matrix=%s:out_range=%s:flags=accurate_rnd+full_chroma_int,format=%s" % (
            f["matrix"], "pc" if f["pix_fmt"].startswith("yuvj") else "tv", f["pix_fmt"])
        if f["frame_rate_change"] and abs(enc_fps - self.fps) > 1e-3:
            vf = "fps=%s," % repr(enc_fps) + vf            # cadence du format : images sautees ou repetees
        cmd = [find_ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", "%dx%d" % (W, H),
               "-r", repr(self.fps), "-i", "-", "-vf", vf] + list(f["args"])
        if f["kind"] == "gop":
            cmd += ["-b:v", "%d" % max(int(f["bitrate"] * f["bitrate_scale"]), 8000), "-g", str(f["gop"]),
                    "-bf", str(f.get("bframes", 0))]
        if f.get("qscale") is not None:
            cmd += ["-q:v", str(max(1, min(31, int(round(f["qscale"] / max(f["bitrate_scale"], 1e-3))))))]
        if f["interlaced"]:
            cmd += ["-flags", "+ildct+ilme", "-top", "0"]
        bsf = []
        if f["errors"] > 0:
            bsf.append("amount=%d" % max(1, int(round(1.0 / f["errors"]))))
        if f["drop"] > 0:
            bsf.append("drop=gt(random(%d)\\,%f)" % (f["seed"] % 9 + 1, 1.0 - f["drop"]))
        if bsf:
            cmd += ["-bsf:v", "noise=" + ":".join(bsf)]
        cmd += [str(self.file)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NOWIN)

    def _to_frame(self, ap1):
        """Image de la camera -> cadre du format en RVB 16 bits (signal video BT.709)."""
        W, H = self.f["size"]
        v = bt709_encode(np.clip(apply_matrix(ap1, AP1_TO_REC709), 0.0, 1.0))
        v = cv2.resize(np.ascontiguousarray(v), (self.wr, self.hr), interpolation=cv2.INTER_AREA)
        frame = np.zeros((H, W, 3))
        frame[self.y0:self.y0 + self.hr, self.x0:self.x0 + self.wr] = v
        return (np.clip(frame, 0.0, 1.0) * 65535.0 + 0.5).astype("<u2")

    def push(self, ap1):
        self.proc.stdin.write(self._to_frame(ap1).tobytes())
        self.count += 1

    def finish(self):
        """Ferme l'enregistrement et renvoie un iterateur des images relues (ACEScg, taille du calque)."""
        self.proc.stdin.close()
        err = self.proc.stderr.read().decode("utf-8", "replace")
        if self.proc.wait() != 0 or not self.file.exists():
            raise RuntimeError("enregistrement %s impossible : %s" % (self.f["label"], err.strip()[-400:]))
        return self._read()

    def _read(self):
        W, H = self.f["size"]
        rng = "pc" if self.f["pix_fmt"].startswith("yuvj") else "tv"
        vf = "scale=in_color_matrix=%s:in_range=%s:out_range=pc:flags=accurate_rnd+full_chroma_int" % (self.f["matrix"], rng)
        # Retour a la cadence de la sequence (les images du format sont repetees si besoin).
        vf = "fps=%s," % repr(self.fps) + vf
        cmd = [find_ffmpeg(), "-v", "error", "-err_detect", "ignore_err", "-i", str(self.file), "-vf", vf,
               "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NOWIN)
        size = W * H * 3 * 2
        last = None
        n = 0
        try:
            while n < self.count:
                buf = proc.stdout.read(size)
                if len(buf) < size:
                    break                     # flux trop abime : on repete la derniere image lue
                last = self._from_frame(np.frombuffer(buf, dtype="<u2").reshape(H, W, 3))
                n += 1
                yield last
            while n < self.count:             # images perdues en fin de sequence
                n += 1
                yield last if last is not None else np.zeros((self.h, self.w, 3))
        finally:
            proc.kill()
            shutil.rmtree(self.dir, ignore_errors=True)

    def _from_frame(self, raw):
        v = raw.astype(np.float64)[self.y0:self.y0 + self.hr, self.x0:self.x0 + self.wr] / 65535.0
        lin = apply_matrix(bt709_decode(v), REC709_TO_AP1)
        return cv2.resize(np.ascontiguousarray(lin), (self.w, self.h), interpolation=cv2.INTER_LINEAR)

    def abort(self):
        try:
            self.proc.kill()
        except Exception:
            pass
        shutil.rmtree(self.dir, ignore_errors=True)


def record_sequence(frames, f, fps):
    """Pratique pour les essais : liste d'images -> liste d'images enregistrees puis relues."""
    h, w = frames[0].shape[:2]
    rec = Recorder(f, h, w, fps)
    for fr in frames:
        rec.push(fr)
    return list(rec.finish())
