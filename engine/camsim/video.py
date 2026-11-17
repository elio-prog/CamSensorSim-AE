"""Lecture video des appareils photo : sauts de lignes, regroupement de photosites, recadrage.

En video, un appareil photo ne lit pas tous ses photosites a chaque image : c'est trop
lent. Selon le modele, il
  - regroupe des photosites de MEME couleur sur une ligne (moyenne de 3 voisins de meme
    couleur, colonnes x-2, x, x+2) : "regroupement horizontal" ;
  - ne lit qu'une ligne sur trois : "saut de lignes" ;
  - ou regroupe aussi verticalement (5D Mark III : 3 x 3, aucun photosite perdu) ;
  - ou lit une fenetre du capteur photosite par photosite (recadrage, 5D Mark IV en 4K) ;
  - ou lit tout et reduit ensuite (Sony A7S II).
Le resultat reste une mosaique de Bayer, plus petite, que la camera dematrice puis
met a la taille de la video. Les photosites ignores sont la cause du moire colore et
des escaliers typiques des reflex en video : la lumiere qui tombe entre deux lignes
lues n'est jamais vue.

Sources : forum et wiki Magic Lantern (fil "Pixel binning patterns in LiveView", a1ex) :
la plupart des Canon regroupent horizontalement et sautent des lignes (une sur trois),
le 5D Mark III regroupe 3 x 3 en 1080p ("averaging pixels of the same color, no line
skipping") ; analyse du Nikon D850 (landingfield.wordpress.com, 2020) : 1080p par
regroupement horizontal et saut de lignes, 1/3 dans chaque sens (2784 x 1854) ; 4K FX
sur 8352 x 2328, "appears to use vertical binning".
"""
import cv2
import numpy as np

from . import resolution
from .filters import optical_low_pass

# Methodes de lecture : (regroupement vertical, regroupement horizontal, saut vertical)
READOUTS = {
    "full": (1, 1, 1),        # tout est lu (puis reduit par la camera)
    "skip3": (1, 3, 3),       # regroupement horizontal de 3, une ligne sur 3 (Canon, Nikon en 1080p)
    "bin3x3": (3, 3, 1),      # regroupement 3 x 3 (5D Mark III)
    "vbin2": (2, 1, 1),       # regroupement vertical de 2 (Nikon D850 en 4K FX, d'apres l'analyse citee)
}


def _same_colour_indices(length, n, out_index):
    """Indices des photosites de meme couleur regroupes pour chaque sortie, le long d'un axe.

    n impair : sortie k centree sur le photosite n*k, avec ses voisins de meme couleur a +-2, +-4...
               (n*k a la parite de k : la mosaique de Bayer est conservee).
    n pair   : par blocs de 2n photosites, sortie k = photosites 2n*(k//2) + (k%2) + 2m, m = 0..n-1.
    Au bord, un indice hors du capteur est remplace par le plus proche de meme couleur.
    """
    k = np.asarray(out_index)
    if n % 2 == 1:
        centre = n * k
        idx = centre[:, None] + 2 * (np.arange(n) - (n - 1) // 2)[None, :]
    else:
        idx = (2 * n * (k // 2) + (k % 2))[:, None] + 2 * np.arange(n)[None, :]
    par = (k % 2)[:, None] * np.ones_like(idx)
    low = par                                   # premier indice de cette couleur
    high = np.where((length - 1) % 2 == par, length - 1, length - 2)
    return np.where(idx < 0, low, np.where(idx > length - 1, high, idx))


def output_count(length, n):
    if n == 1:
        return length
    if n % 2 == 1:
        return (length - 1) // n + 1
    return 2 * (length // (2 * n)) + min(length % (2 * n), 2)


def bin_same_colour(e, bv, bh):
    """Somme des photosites de meme couleur regroupes (bv x bh). e : mosaique (H, W)."""
    out = e
    if bh > 1:
        idx = _same_colour_indices(out.shape[1], bh, np.arange(output_count(out.shape[1], bh)))
        out = out[:, idx].sum(axis=-1)
    if bv > 1:
        idx = _same_colour_indices(out.shape[0], bv, np.arange(output_count(out.shape[0], bv)))
        out = out[idx, :].sum(axis=1)
    return out


def mode_spec(sim):
    """Mode video choisi : (fenetre lue [L, H] en photosites, methode, taille video [L, H], infos)."""
    s, p = sim.settings, sim.profile
    name = s["video_mode"]
    modes = p.get("video_modes", {})
    if name not in modes:
        raise ValueError("le mode video %r n'existe pas sur %s (modes : %s)" %
                         (name, p.get("name"), ", ".join(sorted(modes)) or "aucun"))
    m = modes[name]
    method = m["readout"] if s["video_readout"] == "auto" else s["video_readout"]
    if method not in READOUTS:
        raise ValueError("video_readout inconnu : %r" % method)
    return m["window"], method, m["size"], m


def video_frame(sim, rgb, frame):
    """Scene (ACEScg, taille de l'image) -> image video de la camera (ACEScg, taille de la video)."""
    s, p = sim.settings, sim.profile
    window, method, size, _ = mode_spec(sim)
    bv, bh, skip = READOUTS[method]
    h, w = rgb.shape[:2]
    # 1. Partie de la fenetre lue qui recoit l'image (forme reelle des photosites).
    hs, ws = resolution.sensor_grid({"sensor_pixels": window, "pixel_pitch_um": p.get("pixel_pitch_um", (1, 1)),
                                     "name": p.get("name")}, h, w)
    cam = sim.scene_to_cam(rgb)
    # 2. Lumiere recueillie par chaque photosite, en deux temps pour ne pas fabriquer toute la grille :
    #    lignes d'abord (filtre passe-bas vertical a l'echelle des photosites), puis seules les lignes lues.
    cam = cv2.resize(np.ascontiguousarray(cam), (w, hs), interpolation=cv2.INTER_AREA)
    cam = optical_low_pass(cam, 0.0, float(s["olpf_v_px"]))
    if skip > 1:
        cam = cam[::skip]                       # lignes 0, 3, 6... : la parite alterne, la mosaique reste juste
    cam = cv2.resize(np.ascontiguousarray(cam), (ws, cam.shape[0]),
                     interpolation=cv2.INTER_AREA if ws < w else cv2.INTER_LINEAR)
    cam = optical_low_pass(cam, float(s["olpf_h_px"]), 0.0)
    # 3. Capteur avec regroupement, puis traitement de la camera sur la mosaique reduite.
    dn = sim.sensor.expose(cam, frame, binning=(bv, bh))
    from .isp import develop
    out = develop(dn, sim.sensor, sim.color, s)
    # 4. Mise a la taille de la video : la fenetre entiere devient size[0] x size[1].
    # (la grille est arrondie a un nombre pair de photosites : on ne garde pas cet ecart d'une ligne)
    th = size[1] if abs(hs - window[1]) <= 2 else max(2, int(round(hs * size[1] / window[1])))
    tw = size[0] if abs(ws - window[0]) <= 2 else max(2, int(round(ws * size[0] / window[0])))
    interp = cv2.INTER_AREA if tw < out.shape[1] else cv2.INTER_LINEAR
    return cv2.resize(np.ascontiguousarray(out), (tw, th), interpolation=interp)
