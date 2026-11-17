"""Convertit les parametres de bruit calibres d'ELD (.npy) en profils JSON lisibles.

Source des donnees : depot Vandermode/ELD (licence MIT), dossier camera_params/release.
  Wei, Fu, Yang, Huang, "A Physics-based Noise Formation Model for Extreme
  Low-light Raw Denoising", CVPR 2020 ; version longue TPAMI 2021.
Matrices couleur : LibRaw, src/tables/colordata.cpp (XYZ D65 -> camera, x10000).

Les .npy contiennent un objet Python serialise. On ne les charge PAS avec
allow_pickle=True : on utilise un lecteur qui n'accepte qu'une liste blanche de
classes numpy, donc aucun code contenu dans le fichier ne peut s'executer.

Usage : python -I tools/import_eld_profiles.py
"""
import io
import json
import pickle
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "references" / "third_party" / "eld"
DST = ROOT / "engine" / "camsim" / "profiles"

ALLOWED = {
    ("numpy.core.multiarray", "scalar"), ("numpy._core.multiarray", "scalar"),
    ("numpy.core.multiarray", "_reconstruct"), ("numpy._core.multiarray", "_reconstruct"),
    ("numpy", "dtype"), ("numpy", "ndarray"), ("_codecs", "encode"),
}


class SafeUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) not in ALLOWED:
            raise pickle.UnpicklingError(f"classe refusee : {module}.{name}")
        return super().find_class(module, name)


def load_npy_object(path):
    b = path.read_bytes()
    assert b[:6] == b"\x93NUMPY", "pas un fichier .npy"
    major = b[6]
    hlen = int.from_bytes(b[8:10], "little") if major == 1 else int.from_bytes(b[8:12], "little")
    off = (10 if major == 1 else 12) + hlen
    return SafeUnpickler(io.BytesIO(b[off:])).load()


def plain(o):
    if isinstance(o, dict):
        return {str(k): plain(v) for k, v in o.items()}
    if isinstance(o, np.ndarray):
        return plain(o.tolist())
    if isinstance(o, (list, tuple)):
        return [plain(x) for x in o]
    if isinstance(o, np.generic):
        return o.item()
    return o


# nom ELD -> (nom affiche, matrice LibRaw, niveau noir, niveau blanc, origine des niveaux)
CAMERAS = {
    "SonyA7S2": ("Sony A7S II (ILCE-7SM2)",
                 [5838, -1430, -246, -3497, 11477, 2297, -748, 1885, 5778],
                 512, 16383, "niveaux noir/blanc cites de memoire (ARW 14 bits), a verifier sur un vrai fichier"),
    "NikonD850": ("Nikon D850",
                  [10405, -3755, -1270, -5461, 13787, 1793, -1040, 2015, 6785],
                  800, 16383, "niveaux generiques du code ELD (16383-800), pas ceux de l'appareil"),
    "CanonEOS5D4": ("Canon EOS 5D Mark IV",
                    [6446, -366, -864, -4436, 12204, 2513, -952, 2496, 6348],
                    800, 16383, "niveaux generiques du code ELD (16383-800), pas ceux de l'appareil"),
    "CanonEOS70D": ("Canon EOS 70D",
                    [7034, -804, -1014, -4420, 12564, 2058, -851, 1994, 5758],
                    800, 16383, "niveaux generiques du code ELD (16383-800), pas ceux de l'appareil"),
    "CanonEOS700D": ("Canon EOS 700D",
                     [6602, -841, -939, -4472, 12458, 2247, -975, 2039, 6148],
                     800, 16383, "niveaux generiques du code ELD (16383-800), pas ceux de l'appareil"),
}


def main():
    DST.mkdir(parents=True, exist_ok=True)
    for key, (label, cm, black, white, levels_note) in CAMERAS.items():
        p = plain(load_npy_object(SRC / f"{key}_params.npy"))
        prof = p["Profile-1"]
        out = {
            "name": label,
            "sensor_type": "cmos",
            "cfa": "RGGB",
            "bits": 14,
            "black_level": black,
            "white_level": white,
            "xyz_d65_to_cam": [[cm[r * 3 + c] / 10000.0 for c in range(3)] for r in range(3)],
            "noise": {
                "unit": "DN sur l'echelle 14 bits ; K = gain du systeme en DN par electron",
                "K_min": p["Kmin"],
                "K_max": p["Kmax"],
                "read_gauss_logsigma": prof["g_scale"],
                "read_tukey_logscale": prof["G_scale"],
                "read_tukey_shape": p["G_shape"],
                "row_logsigma": prof["R_scale"],
                "color_bias": p["color_bias"],
            },
            "fixed_pattern": {
                "prnu_sigma": 0.0,
                "dsnu_rel_sigma": 0.4,
                "dark_current_e_per_s_at_20C": 0.0,
                "hot_pixel_fraction": 0.0,
                "note": "non mesure pour cet appareil : valeurs neutres. dsnu_rel_sigma = 40 % du courant "
                        "d'obscurite pour un CMOS selon Janesick (Photon Transfer, p.169), cite par pyradi.",
            },
            "provenance": {
                "noise": "ELD, Vandermode/ELD camera_params/release (MIT). Parametres calibres par les auteurs.",
                "color": "LibRaw colordata.cpp (matrice Adobe XYZ D65 -> camera).",
                "levels": levels_note,
                "assumptions": [
                    "K est suppose proportionnel a l'ISO, avec K_min = ISO de base (100). Non confirme par ELD.",
                    "read_tukey_shape et color_bias sont des listes par reglage ISO calibre ; on les "
                    "interpole regulierement en log(K) entre K_min et K_max. Correspondance exacte non publiee.",
                ],
            },
        }
        dst = DST / f"{key.lower()}.json"
        dst.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        print("ecrit", dst.name, "K", round(p["Kmin"], 4), "->", round(p["Kmax"], 3))


if __name__ == "__main__":
    main()
