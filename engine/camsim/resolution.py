"""Resolution de la simulation : grille de l'image ou vraie grille de photosites du capteur.

Mode "image"   : un pixel de l'image = un photosite (comportement d'origine).
Mode "capteur" : la lumiere est d'abord recueillie par la vraie grille du capteur
                 (nombre et forme des photosites du profil), toute la chaine tourne a
                 cette resolution, puis l'image est remise a la taille de la composition
                 (ou laissee a la taille du capteur, au choix).

Cadrage : l'image est placee DANS la zone utile du capteur sans la deformer, en
respectant la forme reelle des photosites (6,50 x 6,25 um pour l'ICX409AK par
exemple). Une image carree n'utilise donc qu'une partie de la largeur d'un capteur
4:3 : c'est ce que ferait le capteur reel avec la meme image projetee.

Recueil de la lumiere : chaque photosite integre la lumiere sur sa surface
(moyenne par surface, cv2.INTER_AREA). C'est le comportement d'un capteur dont les
microlentilles couvrent toute la surface ; le filtre passe-bas optique et la
mosaique s'appliquent ensuite, a l'echelle des photosites.
"""
import cv2
import numpy as np

UPSCALE = {"lanczos": cv2.INTER_LANCZOS4, "bilinear": cv2.INTER_LINEAR, "nearest": cv2.INTER_NEAREST}


def sensor_grid(profile, h, w):
    """Taille (hauteur, largeur) en photosites de la zone du capteur qui recoit l'image (h, w).

    profile["sensor_pixels"] = [largeur, hauteur] utiles ; profile["pixel_pitch_um"] = [pas H, pas V].
    """
    px = profile.get("sensor_pixels")
    if not px:
        raise ValueError("le profil %r ne donne pas la grille de son capteur" % profile.get("name"))
    W, H = int(px[0]), int(px[1])
    ph, pv = (float(v) for v in profile.get("pixel_pitch_um", (1.0, 1.0)))
    aspect = w / h                                   # forme physique de l'image projetee
    if aspect >= (W * ph) / (H * pv):                # image plus large que le capteur : la largeur limite
        ws = W
        hs = W * ph / aspect / pv
    else:                                            # sinon, la hauteur limite
        hs = H
        ws = H * pv * aspect / ph
    # Dimensions paires : les mosaiques (Bayer 2x2, complementaire 2x4) et l'entrelacement en ont besoin.
    hs = max(2, 2 * int(round(hs / 2.0)))
    ws = max(2, 2 * int(round(ws / 2.0)))
    return min(hs, H + H % 2), min(ws, W + W % 2)


def to_grid(img, hs, ws):
    """Lumiere recueillie par une grille de hs x ws photosites (moyenne par surface)."""
    if img is None:
        return None
    if img.shape[:2] == (hs, ws):
        return img
    return cv2.resize(np.ascontiguousarray(img), (ws, hs), interpolation=cv2.INTER_AREA)


def flow_to_grid(flow, hs, ws):
    """Flux optique (deplacements en pixels) ramene a la grille : on reechantillonne et on change d'unite."""
    if flow is None:
        return None
    h, w = flow.shape[:2]
    out = cv2.resize(np.ascontiguousarray(flow), (ws, hs), interpolation=cv2.INTER_LINEAR)
    out = out.copy()
    out[..., 0] *= ws / w
    out[..., 1] *= hs / h
    return out


def to_image(img, h, w, method="lanczos"):
    """Image du capteur remise a la taille de la composition (simple affichage, pas de la physique)."""
    if img is None or img.shape[:2] == (h, w):
        return img
    if method not in UPSCALE:
        raise ValueError("sensor_upscale inconnu : %r" % method)
    return cv2.resize(np.ascontiguousarray(img), (w, h), interpolation=UPSCALE[method])
