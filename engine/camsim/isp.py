"""Traitement d'image dans la camera : du brut (DN) a l'image RVB.

Dematricage : plus proche voisin, bilineaire, et Malvar-He-Cutler
(Malvar, He, Cutler, ICASSP 2004 ; noyaux 5x5 de l'article, divises par 8).
"""
import numpy as np

from . import camcorder
from .sensor import bayer_masks


def conv2(img, kernel):
    """Convolution 2D par sommes decalees, bords en miroir. Noyau symetrique attendu."""
    k = np.asarray(kernel, dtype=np.float64)
    kh, kw = k.shape
    ph, pw = kh // 2, kw // 2
    pad = np.pad(img, ((ph, ph), (pw, pw)), mode="reflect")
    h, w = img.shape
    out = np.zeros((h, w), dtype=np.float64)
    for i in range(kh):
        for j in range(kw):
            c = k[i, j]
            if c != 0.0:
                out += c * pad[i:i + h, j:j + w]
    return out


def demosaic_nearest(cfa):
    h, w = cfa.shape
    out = np.empty((h, w, 3), dtype=np.float64)
    r = cfa[0::2, 0::2]
    g1 = cfa[0::2, 1::2]
    g2 = cfa[1::2, 0::2]
    b = cfa[1::2, 1::2]
    up = lambda a: np.repeat(np.repeat(a, 2, axis=0), 2, axis=1)
    out[..., 0] = up(r)
    out[..., 2] = up(b)
    g = np.empty((h, w))
    g[0::2, :] = np.repeat(g1, 2, axis=1)
    g[1::2, :] = np.repeat(g2, 2, axis=1)
    out[..., 1] = g
    return out


def demosaic_bilinear(cfa):
    h, w = cfa.shape
    r, g1, g2, b = bayer_masks(h, w)
    k_rb = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]]) / 4.0
    k_g = np.array([[0, 1, 0], [1, 4, 1], [0, 1, 0]]) / 4.0
    out = np.empty((h, w, 3), dtype=np.float64)
    out[..., 0] = conv2(cfa * r, k_rb)
    out[..., 1] = conv2(cfa * (g1 | g2), k_g)
    out[..., 2] = conv2(cfa * b, k_rb)
    return out


_GR_GB = np.array([[0, 0, -1, 0, 0],
                   [0, 0, 2, 0, 0],
                   [-1, 2, 4, 2, -1],
                   [0, 0, 2, 0, 0],
                   [0, 0, -1, 0, 0]]) / 8.0
_RG_RB = np.array([[0, 0, 0.5, 0, 0],
                   [0, -1, 0, -1, 0],
                   [-1, 4, 5, 4, -1],
                   [0, -1, 0, -1, 0],
                   [0, 0, 0.5, 0, 0]]) / 8.0
_RG_BR = _RG_RB.T
_RB_BB = np.array([[0, 0, -1.5, 0, 0],
                   [0, 2, 0, 2, 0],
                   [-1.5, 0, 6, 0, -1.5],
                   [0, 2, 0, 2, 0],
                   [0, 0, -1.5, 0, 0]]) / 8.0


def demosaic_malvar(cfa):
    h, w = cfa.shape
    r_m, g1_m, g2_m, b_m = bayer_masks(h, w)
    g_m = g1_m | g2_m
    R = cfa * r_m
    G = cfa * g_m
    B = cfa * b_m

    G = np.where(r_m | b_m, conv2(cfa, _GR_GB), G)
    rbg_rbbr = conv2(cfa, _RG_RB)   # R ou B sur un vert, voisins sur la meme ligne
    rbg_brrb = conv2(cfa, _RG_BR)   # R ou B sur un vert, voisins sur la meme colonne
    rbgr_bbrr = conv2(cfa, _RB_BB)  # R sur un bleu, B sur un rouge

    yy, xx = np.mgrid[0:h, 0:w]
    r_row, b_row = (yy % 2 == 0), (yy % 2 == 1)
    r_col, b_col = (xx % 2 == 0), (xx % 2 == 1)

    R = np.where(r_row & b_col, rbg_rbbr, R)
    R = np.where(b_row & r_col, rbg_brrb, R)
    B = np.where(b_row & r_col, rbg_rbbr, B)
    B = np.where(r_row & b_col, rbg_brrb, B)
    R = np.where(b_row & b_col, rbgr_bbrr, R)
    B = np.where(r_row & r_col, rbgr_bbrr, B)
    return np.stack([R, G, B], axis=-1)


DEMOSAIC = {"nearest": demosaic_nearest, "bilinear": demosaic_bilinear, "malvar": demosaic_malvar}


def develop(dn, sensor, color, settings):
    """Brut (DN) -> image ACEScg en unites de scene."""
    s = settings
    if sensor.cmyg:
        # Camescope a mosaique complementaire : chaque DN est la somme de deux photosites,
        # et le gain a ete divise par deux. On revient aux unites RVB de la camera
        # (exprimees en DN d'un canal), puis on separe les couleurs comme le circuit du camescope.
        p = 2.0 * (dn - sensor.black - float(s["isp_black_offset_dn"]))
        # Defauts de la fiche Sony : ecart de niveau entre trames (papillotement), et entre lignes
        # "rouges" et "bleues" d'une meme trame (rampement de lignes).
        ff = float(s["cmyg_field_flicker"]) if s.get("cmyg_field_flicker") is not None else float(sensor.p.get("field_flicker", 0.0))
        lc = float(s["cmyg_line_crawl"]) if s.get("cmyg_line_crawl") is not None else float(sensor.p.get("line_crawl", 0.0))
        if ff or lc:
            p = p.copy()
            if ff:
                p[1::2] *= 1.0 + ff                       # trame B
            if lc:
                t = camcorder.line_types(p.shape[0])[:, None]
                p *= np.where(t == 1, 1.0 + 0.5 * lc, 1.0 - 0.5 * lc)
        rgb = camcorder.separate(p, float(s["cmyg_chroma_bw_mhz"]) / float(sensor.p.get("pixel_clock_mhz", 14.1875)),
                                 s["cmyg_chroma_vertical"], white=sensor.cmyg_response)
        x = rgb / sensor.span
        # La balance des blancs compense aussi la faible sensibilite du capteur au rouge.
        x = x * (color.wb_gains / np.asarray(sensor.cmyg_response) *
                 np.array([float(s["wb_red"]), 1.0, float(s["wb_blue"])]))
        # Niveau de couleur, apres la balance des blancs (comme le gain de chrominance d'un camescope).
        x = camcorder.chroma_level(x, float(s["cmyg_chroma_gain"]))
        if s["clip_highlights"]:
            x = np.minimum(x, 1.0)
        return color.cam_wb_to_scene(x) * (sensor.span / sensor.dn_per_scene_unit)
    if sensor.three_chip:
        # Trois capteurs : un plan par couleur, pas de mosaique a reconstruire.
        x = (dn - sensor.black - float(s["isp_black_offset_dn"])) / sensor.span
        x = x * (color.wb_gains * np.array([float(s["wb_red"]), 1.0, float(s["wb_blue"])]))
        if s["clip_highlights"]:
            x = np.minimum(x, 1.0)
        return color.cam_wb_to_scene(x) * (sensor.span / sensor.dn_per_scene_unit)
    # 1. Soustraction du niveau noir nominal. Le biais par canal reste dans l'image,
    #    sauf si la camera est supposee l'avoir calibre (isp_remove_color_bias).
    x = dn - sensor.black - float(s["isp_black_offset_dn"])
    # Erreur de motif de Bayer : le traitement croit que la mosaique commence un pixel plus loin.
    bdx, bdy = int(s["isp_bayer_dx"]) % 2, int(s["isp_bayer_dy"]) % 2
    if bdx or bdy:
        x = np.roll(x, (bdy, bdx), axis=(0, 1))
    if s["isp_remove_color_bias"] and float(s["color_bias_gain"]) != 0.0:
        cb = sensor.color_bias * float(s["color_bias_gain"])
        x = x.copy()
        x[0::2, 0::2] -= cb[0]
        x[0::2, 1::2] -= cb[1]
        x[1::2, 1::2] -= cb[2]
        x[1::2, 0::2] -= cb[3]
    x = x / sensor.span                      # 1.0 = saturation du brut

    # 2. Balance des blancs, appliquee sur la mosaique.
    g = color.wb_gains * np.array([float(s["wb_red"]), 1.0, float(s["wb_blue"])])
    x = x.copy()
    x[0::2, 0::2] *= g[0]
    x[0::2, 1::2] *= g[1]
    x[1::2, 0::2] *= g[1]
    x[1::2, 1::2] *= g[2]

    # 3. Ecretage des hautes lumieres apres balance : evite les blancs magenta.
    if s["clip_highlights"]:
        x = np.minimum(x, 1.0)

    # 4. Dematricage.
    rgb = DEMOSAIC[s["demosaic"]](x)
    if bdx or bdy:
        rgb = np.roll(rgb, (-bdy, -bdx), axis=(0, 1))

    # 5. Matrice couleur vers ACEScg, puis retour a l'echelle de la scene.
    out = color.cam_wb_to_scene(rgb)
    return out * (sensor.span / sensor.dn_per_scene_unit)
