"""Sources d'images : sequences EXR, sequences ou images PNG / TIFF / JPEG, videos.

Mode d'entree A (EXR ACEScg lineaire) : la lumiere de la scene est lue telle quelle.
Mode d'entree B (images deja traitees, "display-referred") : il faut retrouver une
lumiere de scene plausible en defaisant ce que l'image a subi. On suit l'ordre de
"Unprocessing Images for Learned Raw Denoising" (Brooks et al., CVPR 2019, depot
timothybrooks/unprocessing, fonction unprocess) :

  1. valeurs codees -> [0, 1]                (avec, au choix, un leger tremblement d'un
                                              demi-pas de code pour effacer les marches 8 bits)
  2. courbe de tonalite inverse              (Brooks : inverse de la courbe "smoothstep",
                                              0.5 - sin(asin(1 - 2y) / 3)) ; aucune par defaut
  3. courbe de transfert inverse             (sRGB IEC 61966-2-1, BT.709, gamma 2,2 / 2,4)
  4. primaires Rec.709 / sRGB -> ACEScg      (matrices calculees depuis les primaires, Bradford)
  5. exposition                              (le blanc de l'ecran devient 1,0 en lumiere de scene)

Ce qui ne peut PAS etre retrouve : les hautes lumieres ecretees (au-dela du blanc de
l'ecran) et la vraie couleur des sources. C'est une limite de toute methode de ce type.

Les videos sont decodees par ffmpeg en RVB 16 bits (rgb48le / rgba64le), ce qui garde
les videos 10 ou 12 bits (ProRes, DNxHR). ffmpeg est cherche dans config.json (cle
"ffmpeg"), puis dans le PATH.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .color import AP1_TO_XYZ_D65, D65_WHITE, SRGB_PRIMS, apply_matrix, rgb_to_xyz_matrix
from .exr_io import read_exr

ROOT = Path(__file__).resolve().parent.parent.parent
SEQ = re.compile(r"^(.*?)(\d+)(\.[A-Za-z0-9]+)$")
EXR_EXT = {".exr"}
IMAGE_EXT = {".png", ".tif", ".tiff", ".jpg", ".jpeg"}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mxf", ".webm"}

REC709_TO_AP1 = np.linalg.inv(AP1_TO_XYZ_D65) @ rgb_to_xyz_matrix(SRGB_PRIMS, D65_WHITE)
AP1_TO_REC709 = np.linalg.inv(REC709_TO_AP1)


# ---------------------------------------------------------------- courbes
def srgb_decode(v):
    """IEC 61966-2-1 : valeur codee -> lumiere lineaire."""
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def srgb_encode(x):
    x = np.maximum(x, 0.0)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - 0.055)


# Courbe de prise de vue BT.709. Avec les constantes arrondies de la norme (1,099 et 0,018),
# les deux branches ne se rejoignent pas (0,0810 contre 0,0813) : certaines valeurs codees
# n'ont pas d'antecedent. On prend les valeurs exactes de la meme courbe donnees par
# ITU-R BT.2020 (alpha = 1,09929682680944 ; beta = 0,018053968510807), qui la rendent continue.
_A709, _B709 = 1.09929682680944, 0.018053968510807


def bt709_decode(v):
    """Inverse de la courbe de prise de vue ITU-R BT.709 (OETF) : retrouve la lumiere vue par la camera."""
    return np.where(v < 4.5 * _B709, v / 4.5, ((np.maximum(v, 0.0) + _A709 - 1.0) / _A709) ** (1 / 0.45))


def bt709_encode(x):
    x = np.maximum(x, 0.0)
    return np.where(x < _B709, 4.5 * x, _A709 * x ** 0.45 - (_A709 - 1.0))


TRANSFERS = {
    "srgb": (srgb_decode, srgb_encode),
    "bt709": (bt709_decode, bt709_encode),
    "gamma22": (lambda v: np.maximum(v, 0.0) ** 2.2, lambda x: np.maximum(x, 0.0) ** (1 / 2.2)),
    "gamma24": (lambda v: np.maximum(v, 0.0) ** 2.4, lambda x: np.maximum(x, 0.0) ** (1 / 2.4)),
    "linear": (lambda v: v, lambda x: x),
}


def inverse_smoothstep(y):
    """Brooks et al. 2019 : inverse de la courbe de tonalite 3y^2 - 2y^3."""
    y = np.clip(y, 0.0, 1.0)
    return 0.5 - np.sin(np.arcsin(1.0 - 2.0 * y) / 3.0)


def smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return 3.0 * x * x - 2.0 * x ** 3


# ---------------------------------------------------------------- reglages d'entree
def input_params(settings, kind, probe=None):
    """Courbe, tonalite, exposition et tremblement retenus pour une source deja traitee."""
    tr = settings.get("input_transfer", "auto") or "auto"
    if tr == "auto":
        tr = "srgb"
        if kind == "video" and probe:
            # Video etiquetee BT.709 (ou sans etiquette) : on inverse la courbe de prise de vue BT.709.
            t = (probe.get("color_transfer") or "").lower()
            tr = {"bt709": "bt709", "iec61966-2-1": "srgb", "linear": "linear", "gamma22": "gamma22"}.get(t, "bt709")
    if tr not in TRANSFERS:
        raise ValueError("input_transfer inconnu : %r" % tr)
    tone = settings.get("input_tone", "none") or "none"
    if tone not in ("none", "smoothstep"):
        raise ValueError("input_tone inconnu : %r" % tone)
    return {"transfer": tr, "tone": tone, "exposure": 2.0 ** float(settings.get("input_exposure_ev", 0.0)),
            "dither": bool(settings.get("input_dequantize", True))}


def display_to_scene(code, levels, params, rng=None):
    """Valeurs codees (entiers ou flottants, ..., 3) -> lumiere de scene ACEScg."""
    v = code.astype(np.float64)
    if params["dither"] and levels > 1 and rng is not None:
        v = v + rng.random(v.shape) - 0.5             # un demi-pas de chaque cote : efface les marches
    v = np.clip(v / (levels - 1), 0.0, 1.0)
    if params["tone"] == "smoothstep":
        v = inverse_smoothstep(v)
    lin = TRANSFERS[params["transfer"]][0](v)
    return apply_matrix(lin, REC709_TO_AP1) * params["exposure"]


def scene_to_display(ap1, params):
    """Chemin inverse (pour ecrire une sortie dans le meme codage que la source)."""
    lin = apply_matrix(ap1 / params["exposure"], AP1_TO_REC709)
    v = TRANSFERS[params["transfer"]][1](np.clip(lin, 0.0, None))
    if params["tone"] == "smoothstep":
        v = smoothstep(v)
    return np.clip(v, 0.0, 1.0)


# ---------------------------------------------------------------- ffmpeg
def find_ffmpeg(name="ffmpeg"):
    cfg = ROOT / "config.json"
    if cfg.exists():
        p = json.loads(cfg.read_text(encoding="utf-8")).get(name)
        if p and Path(p).exists():
            return p
    p = shutil.which(name)
    if p:
        return p
    raise FileNotFoundError("%s introuvable : indiquer son chemin dans %s (cle \"%s\")" % (name, cfg, name))


def probe_video(path):
    exe = find_ffmpeg("ffprobe")
    out = subprocess.run([exe, "-v", "error", "-select_streams", "v:0", "-count_packets",
                          "-show_entries", "stream=width,height,r_frame_rate,nb_read_packets,pix_fmt,"
                          "color_transfer,color_primaries,color_space,color_range",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    s = json.loads(out)["streams"][0]
    num, den = (int(x) for x in s["r_frame_rate"].split("/"))
    s["fps"] = num / den if den else 25.0
    s["frames"] = int(s.get("nb_read_packets") or 0)
    s["alpha"] = any(k in s.get("pix_fmt", "") for k in ("rgba", "yuva", "argb", "bgra", "gbrap"))
    return s


def video_bits(pix_fmt):
    """Profondeur des echantillons d'apres le format de pixel ffmpeg (yuv420p -> 8, yuv422p10le -> 10...)."""
    m = re.search(r"p(\d{2})(le|be)?$", pix_fmt or "")
    if m:
        return int(m.group(1))
    if any(k in (pix_fmt or "") for k in ("48", "64")):
        return 16
    return 8


class VideoReader:
    """Lecture d'une video image par image, a travers ffmpeg, avec un petit cache."""

    def __init__(self, path):
        self.path = Path(path)
        self.info = probe_video(path)
        self.w, self.h = int(self.info["width"]), int(self.info["height"])
        self.ch = 4 if self.info["alpha"] else 3
        self.proc, self.next_n, self.cache = None, None, {}

    def _start(self, n):
        self.close()
        exe = find_ffmpeg()
        # Matrice YUV -> RVB : celle de l'etiquette ; sans etiquette, BT.709 (videos HD).
        cs = (self.info.get("color_space") or "").lower()
        matrix = "bt601" if cs in ("bt470bg", "smpte170m") else "bt709"
        rng = "pc" if (self.info.get("color_range") or "").lower() == "pc" else "tv"
        vf = "scale=in_color_matrix=%s:in_range=%s:out_range=pc" % (matrix, rng)
        cmd = [exe, "-v", "error", "-ss", "%.6f" % (n / self.info["fps"]), "-i", str(self.path), "-vf", vf,
               "-f", "rawvideo", "-pix_fmt", "rgba64le" if self.ch == 4 else "rgb48le", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.next_n = n

    def get(self, n):
        if n in self.cache:
            return self.cache[n]
        if self.proc is None or self.next_n != n:
            self._start(n)
        size = self.w * self.h * self.ch * 2
        buf = self.proc.stdout.read(size)
        if len(buf) < size:
            self.close()
            return None
        img = np.frombuffer(buf, dtype="<u2").reshape(self.h, self.w, self.ch)
        self.next_n = n + 1
        self.cache[n] = img
        for k in [k for k in self.cache if k < n - 2 or k > n + 2]:
            del self.cache[k]
        return img

    def close(self):
        if self.proc is not None:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None


# ---------------------------------------------------------------- sources
class Source:
    """numbers : numeros des images ; get(n) -> (rvb ACEScg, alpha ou None, infos) ou None ; stem(n)."""

    kind = "exr"

    def __init__(self, src_file, settings):
        self.src = Path(src_file)
        self.settings = settings
        self.files = {}
        self.params = None
        self.cache = {}

    @property
    def numbers(self):
        return sorted(self.files)

    def stem(self, n):
        return self.files[n].stem

    def folder(self):
        return self.src.resolve().parent

    def _read(self, n):
        raise NotImplementedError

    def get(self, n):
        if n not in self.cache:
            try:
                self.cache[n] = self._read(n) if n in self.files else None
            except Exception:
                self.cache[n] = None
            for k in [k for k in self.cache if k < n - 2]:
                del self.cache[k]
        return self.cache[n]

    def rng(self, n):
        return np.random.default_rng([int(self.settings.get("seed", 1)), 7, int(n)])

    def close(self):
        pass


def numbered(src, exts):
    m = SEQ.match(src.name)
    if not m or m.group(3).lower() not in exts:
        return {0: src}
    prefix, _, ext = m.groups()
    out = {}
    for f in src.parent.iterdir():
        mm = SEQ.match(f.name)
        if mm and mm.group(1) == prefix and mm.group(3).lower() == ext.lower():
            out[int(mm.group(2))] = f
    return out


class ExrSource(Source):
    kind = "exr"

    def __init__(self, src_file, settings):
        super().__init__(src_file, settings)
        self.files = numbered(self.src, EXR_EXT)

    def _read(self, n):
        return read_exr(self.files[n])


class ImageSource(Source):
    kind = "image"

    def __init__(self, src_file, settings):
        super().__init__(src_file, settings)
        self.files = numbered(self.src, IMAGE_EXT)
        self.params = input_params(settings, "image")

    def _read(self, n):
        raw = cv2.imread(str(self.files[n]), cv2.IMREAD_UNCHANGED)
        if raw is None:
            return None
        if raw.ndim == 2:
            raw = np.stack([raw] * 3, axis=-1)
        levels = 65536 if raw.dtype == np.uint16 else (256 if raw.dtype == np.uint8 else 2)
        if raw.dtype.kind == "f":                          # TIFF flottant : deja dans [0, 1]
            raw, levels = raw.astype(np.float64), 2
        rgb = raw[..., 2::-1] if raw.shape[2] >= 3 else raw         # BGR(A) -> RGB
        alpha = raw[..., 3].astype(np.float32) / (levels - 1) if raw.shape[2] == 4 else None
        scene = display_to_scene(rgb[..., :3], levels, self.params, self.rng(n))
        return scene, alpha, {"source": "image", "levels": levels}


class VideoSource(Source):
    kind = "video"

    def __init__(self, src_file, settings):
        super().__init__(src_file, settings)
        self.reader = VideoReader(self.src)
        n = self.reader.info["frames"]
        self.files = {i: self.src for i in range(n)}
        self.params = input_params(settings, "video", self.reader.info)

    def stem(self, n):
        return "%s_%05d" % (self.src.stem, n)

    def _read(self, n):
        raw = self.reader.get(n)
        if raw is None:
            return None
        alpha = raw[..., 3].astype(np.float32) / 65535.0 if raw.shape[2] == 4 else None
        # ffmpeg sort toujours 16 bits. Pour une video 8 bits, le tremblement doit couvrir un pas
        # de code 8 bits (257 en 16 bits), sinon les marches restent.
        if video_bits(self.reader.info.get("pix_fmt", "")) <= 8:
            scene = display_to_scene(raw[..., :3].astype(np.float64) / 257.0, 256, self.params, self.rng(n))
        else:
            scene = display_to_scene(raw[..., :3], 65536, self.params, self.rng(n))
        return scene, alpha, {"source": "video", "pix_fmt": self.reader.info.get("pix_fmt")}

    def close(self):
        self.reader.close()


def open_source(src_file, settings):
    ext = Path(src_file).suffix.lower()
    if ext in EXR_EXT:
        return ExrSource(src_file, settings)
    if ext in IMAGE_EXT:
        return ImageSource(src_file, settings)
    if ext in VIDEO_EXT:
        return VideoSource(src_file, settings)
    raise ValueError("format non pris en charge : %s" % ext)
