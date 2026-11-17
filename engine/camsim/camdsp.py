"""Etage video d'une camera : coude, accentuation des contours, couleurs dans les blancs, ecretage.

Les cameras video (camescopes, cameras de reportage) ne livrent pas une image lineaire :
leur processeur code le signal avec la courbe BT.709 puis le retravaille dans ce domaine.
Les traitements simules ici sont ceux de la chaine classique d'un processeur de camera :

  1. courbe de prise de vue BT.709 (100 % video = blanc de reference, 1,0 en lumiere de scene) ;
  2. coude ("knee") : au-dessus du point de coude, le signal monte moins vite, pour faire
     entrer des hautes lumieres plus fortes que le blanc dans le signal ;
  3. accentuation des contours ("aperture correction", "detail") sur la luminance : on ajoute
     au signal une version passe-haut de lui-meme. Horizontalement, la difference avec les
     points a +-2 photosites (retards de 2 periodes d'horloge, pic de la reponse au quart de la
     frequence d'echantillonnage) ; verticalement, avec les lignes voisines de la meme trame
     (retards de 1H). Un seuil ("coring") ignore les petits details pour ne pas accentuer le
     bruit ; un limiteur borne les halos ;
  4. reduction des couleurs dans les hautes lumieres (au-dessus du coude, la couleur tend vers
     le blanc : sans cela, une voie saturee avant les autres donne des blancs colores) ;
  5. ecretage du blanc ("white clip").

Le resultat est redecode par l'inverse de la courbe BT.709 (sans le coude) : l'image reste
lineaire pour After Effects, mais garde la forme du coude, les halos et l'ecretage.

Les valeurs par defaut des profils sont des valeurs courantes, pas des mesures : la fiche
du capteur ne decrit pas le processeur du camescope.
"""
import numpy as np

from .sources import AP1_TO_REC709, REC709_TO_AP1, bt709_decode, bt709_encode
from .color import apply_matrix

LUMA = np.array([0.2126, 0.7152, 0.0722])            # luminance BT.709


def _shift(a, d, axis):
    """Decalage avec bords repetes."""
    if d == 0:
        return a
    n = a.shape[axis]
    idx = np.clip(np.arange(n) - d, 0, n - 1)
    return np.take(a, idx, axis=axis)


def knee(v, point, slope):
    """Au-dela de 'point', la pente est multipliee par 'slope' (0 < slope <= 1)."""
    if point >= 1.5 or slope >= 1.0:
        return v
    return np.where(v > point, point + (v - point) * slope, v)


def aperture(y, gain_h, gain_v, coring, limit, line_step=1):
    """Detail a ajouter a la luminance codee y (H, W)."""
    d = np.zeros_like(y)
    if gain_h:
        hp = y - 0.5 * (_shift(y, 2, 1) + _shift(y, -2, 1))
        d += gain_h * hp
    if gain_v:
        hp = y - 0.5 * (_shift(y, line_step, 0) + _shift(y, -line_step, 0))
        d += gain_v * hp
    if coring > 0:
        d = np.sign(d) * np.maximum(np.abs(d) - coring, 0.0)
    if limit > 0:
        d = np.clip(d, -limit, limit)
    return d


def apply(ap1, p, interlaced=False, gain=1.0):
    """ap1 : image ACEScg lineaire (H, W, 3). p : parametres (voir dsp_params). Renvoie l'image traitee.

    gain : niveau du signal de la camera par rapport a l'image rendue (2^ev). Le moteur ramene
    toujours l'image a la luminosite de la scene ; une camera video surexposee envoie pourtant un
    signal plus fort a son processeur, et c'est ce signal qu'elle enregistre. L'etage travaille donc
    sur ap1 x gain et rend l'image a ce niveau : avec l'etage actif, +2 diaphragmes donnent une
    image 4 fois plus lumineuse, comme sur un camescope.
    """
    if not p["enabled"]:
        return ap1
    rgb = apply_matrix(ap1 * gain, AP1_TO_REC709)
    v = bt709_encode(np.maximum(rgb, 0.0))
    # 2. Coude, voie par voie (R, V, B), comme dans les processeurs de camera classiques :
    #    une couleur vive qui depasse le coude sur une seule voie se rapproche du blanc.
    v = knee(v, p["knee_point"], p["knee_slope"])
    y = v @ LUMA
    # 3. Accentuation des contours sur la luminance.
    det = aperture(y, p["detail_h"], p["detail_v"], p["detail_coring"], p["detail_limit"],
                   line_step=2 if interlaced else 1)
    v = v + det[..., None]
    y = y + det
    # 4. Couleurs ramenees vers le blanc quand la voie la plus haute depasse le coude.
    if p["highlight_chroma"] > 0 and p["knee_point"] < p["white_clip"]:
        top = v.max(axis=-1)
        t = np.clip((top - p["knee_point"]) / (p["white_clip"] - p["knee_point"]), 0.0, 1.0) * p["highlight_chroma"]
        v = v + (y[..., None] - v) * t[..., None]
    # 5. Ecretage du blanc.
    v = np.minimum(v, p["white_clip"])
    return apply_matrix(bt709_decode(v), REC709_TO_AP1)


def dsp_params(settings, profile):
    """Reglage utilisateur, sinon valeur du profil (cle "dsp"), sinon etage desactive."""
    prof = profile.get("dsp", {})
    keys = {"enabled": "dsp_enabled", "knee_point": "dsp_knee_point", "knee_slope": "dsp_knee_slope",
            "white_clip": "dsp_white_clip", "detail_h": "dsp_detail_h", "detail_v": "dsp_detail_v",
            "detail_coring": "dsp_detail_coring", "detail_limit": "dsp_detail_limit",
            "highlight_chroma": "dsp_highlight_chroma"}
    base = {"enabled": False, "knee_point": 2.0, "knee_slope": 1.0, "white_clip": 10.0, "detail_h": 0.0,
            "detail_v": 0.0, "detail_coring": 0.0, "detail_limit": 0.0, "highlight_chroma": 0.0}
    out = {}
    for k, sk in keys.items():
        v = settings.get(sk)
        if k == "enabled":
            if v in (None, "", "profil"):
                v = prof.get("enabled", False)
            elif v in ("on", "off"):
                v = v == "on"
            else:
                raise ValueError("dsp_enabled inconnu : %r" % v)
            out[k] = bool(v)
            continue
        if v is None or v == "":
            v = prof.get(k, base[k])
        out[k] = float(v)
    return out
