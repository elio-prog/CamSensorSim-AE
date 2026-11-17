"""Ecrit native/core/profiles_data.h : les profils des appareils photo et leurs constantes deja
calculees par le moteur Python (bruit, matrices de couleur, tables spectrales).

Le moteur Python reste la seule source de verite : le code C++ ne fait que lire ces nombres.
A relancer apres toute modification d'un profil ou de la couleur spectrale :
  python tools/export_native.py
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from camsim import CameraSim, load_profile                    # noqa: E402
from camsim.sensor import GREY_FRACTION_OF_SATURATION, SCENE_GREY   # noqa: E402

# Cameras portees dans l'effet natif (les tubes, qui ont une memoire d'image, viendront ensuite).
PROFILES = ["sonya7s2", "nikond850", "canoneos5d4", "canoneos70d", "canoneos700d", "kai0373_3ccd", "icx409ak_camcorder"]
KIND = {"RGGB": 0, "3chip": 1, "CMYG": 2}


def arr(a, fmt="%.9g"):
    return "{" + ", ".join(fmt % float(v) for v in np.asarray(a, dtype=np.float64).ravel()) + "}"


def farr(a):
    """Tableau de float C++ : litteraux suffixes f (pas d'avertissement de troncature)."""
    out = []
    for v in np.asarray(a, dtype=np.float32).ravel():
        s = "%.8g" % float(v)
        if "." not in s and "e" not in s and "inf" not in s and "nan" not in s:
            s += ".0"
        out.append(s + "f")
    return "{" + ", ".join(out) + "}"


def main():
    out = ["// Fichier ecrit par tools/export_native.py : ne pas modifier a la main.",
           "#pragma once", "#include \"profile.h\"", "", "namespace camsim {", ""]
    out.append("constexpr double GREY_FRACTION_OF_SATURATION = %.17g;" % GREY_FRACTION_OF_SATURATION)
    out.append("constexpr double SCENE_GREY = %.17g;" % SCENE_GREY)
    out.append("")
    names = []
    for key in PROFILES:
        p = load_profile(key)
        n = p["noise"]
        fp = p["fixed_pattern"]
        shape = np.asarray(n["read_tukey_shape"], dtype=np.float64).reshape(-1, 2).mean(axis=1)
        bias = np.asarray(n["color_bias"], dtype=np.float64).reshape(-1, 2, 4).mean(axis=1)
        mat = CameraSim(key, spectral="off").color
        has_ssf = bool(p.get("spectral_ssf"))
        cname = "P_" + key
        out.append("// %s" % p["name"])
        out.append("static const double %s_tukey_shape[] = %s;" % (cname, arr(shape)))
        out.append("static const double %s_color_bias[] = %s;" % (cname, arr(bias)))
        if has_ssf:
            spe_sim = CameraSim(key, spectral="on")
            spe = spe_sim.color
            lut = spe_sim.spectral.resp_lut                   # (3, G, G, 3)
            g = lut.shape[1]
            out.append("static const float %s_lut[] = %s;" % (cname, farr(lut)))
            lut_ref = "%s_lut" % cname
        else:
            spe, g, lut_ref = mat, 0, "nullptr"
        ccd = p.get("ccd", {})
        dsp = p.get("dsp", {})
        dkeys = ["knee_point", "knee_slope", "white_clip", "detail_h", "detail_v", "detail_coring", "detail_limit",
                 "highlight_chroma"]
        dbase = [0.85, 0.25, 1.07, 0.6, 0.4, 0.01, 0.12, 0.8]
        out.append("static const Profile %s = {" % cname)
        out.append('  "%s", "%s", %d, %s, %d, %.9g, %.9g, %.9g,' % (
            key, p["name"].replace('"', "'"), KIND[p.get("cfa", "RGGB")],
            "true" if p.get("sensor_type") == "ccd" else "false", int(p["bits"]),
            float(p["black_level"]), float(p["white_level"]), float(p.get("base_iso") or 100.0)))
        out.append("  %.17g, %.17g," % (n["K_min"], n["K_max"]))
        out.append("  {%.17g, %.17g}, {%.17g, %.17g}, {%.17g, %.17g}," % (
            n["read_gauss_logsigma"]["slope"], n["read_gauss_logsigma"]["bias"],
            n["read_tukey_logscale"]["slope"], n["read_tukey_logscale"]["bias"],
            n["row_logsigma"]["slope"], n["row_logsigma"]["bias"]))
        out.append("  %s_tukey_shape, %d, %s_color_bias," % (cname, len(shape), cname))
        out.append("  %.9g, %.9g, %.9g," % (fp["prnu_sigma"], fp["dsnu_rel_sigma"], fp["dark_current_e_per_s_at_20C"]))
        out.append("  %s, %s, %s," % (arr(mat.ap1_to_cam), arr(mat.wb_gains), arr(mat.cam_to_ap1_wb)))
        out.append("  %s, %s, %s," % (arr(spe.ap1_to_cam), arr(spe.wb_gains), arr(spe.cam_to_ap1_wb)))
        out.append("  %s, %d," % (lut_ref, g))
        out.append("  %.9g, %.9g, %.9g, %.9g, %s," % (ccd.get("smear_fraction", 0.0), ccd.get("smear_ref_exposure_s", 0.02),
                                                     ccd.get("antiblooming", 1.0), ccd.get("cti", 0.0),
                                                     "true" if ccd.get("interlace", "off") == "fields" else "false"))
        out.append("  %s, %.9g," % (arr(p.get("cmyg_rgb_response", [1, 1, 1])), float(p.get("pixel_clock_mhz", 14.1875))))
        out.append("  %.9g, %.9g, %.9g," % (float(p.get("shading", 0.0)), float(p.get("field_flicker", 0.0)),
                                           float(p.get("line_crawl", 0.0))))
        out.append("  %s, %s," % ("true" if dsp.get("enabled") else "false",
                                  arr([dsp.get(k, b) for k, b in zip(dkeys, dbase)])))
        px = p.get("sensor_pixels", [0, 0])
        pitch = p.get("pixel_pitch_um", [1.0, 1.0])
        vm = p.get("video_modes", {})
        RO = {"full": 0, "skip3": 1, "bin3x3": 2, "vbin2": 3}
        win, siz, ro, has = [], [], [], []
        for m in ("1080p", "4k"):
            if m in vm:
                win.append("{%d, %d}" % tuple(vm[m]["window"]))
                siz.append("{%d, %d}" % tuple(vm[m]["size"]))
                ro.append(str(RO[vm[m]["readout"]]))
                has.append("true")
            else:
                win.append("{0, 0}"); siz.append("{0, 0}"); ro.append("0"); has.append("false")
        out.append("  %d, %d, %.9g, %.9g, %d, {%s}, {%s}, {%s}, {%s}" % (
            int(px[0]), int(px[1]), float(pitch[0]), float(pitch[1]), len(vm), ", ".join(win), ", ".join(siz),
            ", ".join(ro), ", ".join(has)))
        out.append("};")
        out.append("")
        names.append("&" + cname)
    out.append("static const Profile* const PROFILES[] = {%s};" % ", ".join(names))
    out.append("constexpr int PROFILE_COUNT = %d;" % len(names))
    out.append("")
    out.append("}  // namespace camsim")
    dst = ROOT / "native" / "core" / "profiles_data.h"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out) + "\n", encoding="ascii")
    print("ecrit %s : %d profils" % (dst.relative_to(ROOT), len(names)))


if __name__ == "__main__":
    main()
