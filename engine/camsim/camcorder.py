"""Traitement des couleurs d'un camescope a un seul CCD a mosaique complementaire.

Le capteur livre, sur chaque ligne, l'alternance de deux sommes de photosites
(deux lignes de la mosaique additionnees dans le registre vertical) :

  ligne "bleue" : (Cy+Mg), (Ye+G), (Cy+Mg), (Ye+G) ...
  ligne "rouge" : (Cy+G),  (Ye+Mg), (Cy+G),  (Ye+Mg) ...

Avec des filtres ideaux (Ye = R+G, Cy = G+B, Mg = R+B) :

  somme de deux voisins   S1 + S2 = 2R + 3G + 2B        -> luminance, sur toutes les lignes
  difference, ligne rouge (Ye+Mg) - (Cy+G) = 2R - G     -> Cr
  difference, ligne bleue (Cy+Mg) - (Ye+G) = 2B - G     -> Cb

Chaque ligne ne porte donc qu'UNE difference de couleur ; la camera recupere l'autre
sur la ligne precedente de la meme trame grace a une ligne a retard de 1H
(sequence ligne par ligne). Source : brevet Samsung US 5 136 370 (1992), circuit
"somme / difference / retard 1H / matrice" ; formules de luminance concordantes
dans les brevets Sharp US 6 628 718 et Canon US 6 757 016.

Consequences physiques reproduites ici, sans les ajouter "a la main" :
  - resolution verticale des couleurs divisee par deux, couleurs decalees d'une ligne ;
  - fausses couleurs sur les contours verticaux (la difference entre voisins melange
    couleur et detail de luminance) ;
  - bruit de couleur plus fort qu'avec des filtres primaires (la matrice soustrait
    des signaux voisins) ;
  - bande passante de couleur etroite : couleurs qui bavent horizontalement.
"""
import math

import numpy as np


def _hfilter(img, kernel):
    """Filtre horizontal (le long des lignes), bords en miroir."""
    k = np.asarray(kernel, dtype=np.float64)
    r = len(k) // 2
    if r == 0:
        return img * k[0]
    pad = np.pad(img, ((0, 0), (r, r)), mode="reflect")
    w = img.shape[1]
    out = np.zeros_like(img, dtype=np.float64)
    for i, c in enumerate(k):
        if c != 0.0:
            out += c * pad[:, i:i + w]
    return out


def gaussian_lowpass_kernel(f3db_cycles_per_px):
    """Noyau gaussien dont la reponse vaut -3 dB a la frequence donnee (cycles par photosite)."""
    if f3db_cycles_per_px <= 0 or f3db_cycles_per_px >= 0.5:
        return np.array([1.0])
    sigma = math.sqrt(math.log(2.0) / 2.0) / (math.pi * f3db_cycles_per_px)
    r = max(1, int(math.ceil(3.0 * sigma)))
    x = np.arange(-r, r + 1, dtype=np.float64)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    return k / k.sum()


def line_types(h):
    """0 = ligne bleue (Cb), 1 = ligne rouge (Cr), pour l'image entrelacee (trame A sur les lignes paires).

    Trame A, ligne k = lignes de mosaique (2k, 2k+1) ; trame B, ligne k = (2k+1, 2k+2).
    Une ligne est "rouge" si la colonne paire (Cy) recoit Cy+G : la difference vaut alors 2R - G.
    Le type est lu dans la disposition de la mosaique.
    """
    from .sensor import CMYG_PATTERN
    out = np.empty(h, dtype=int)
    for r in range(h):
        top = r if r % 2 == 0 else r       # trame A : 2k = r ; trame B : 2k+1 = r
        pair = {CMYG_PATTERN[top % 4][0], CMYG_PATTERN[(top + 1) % 4][0]}
        out[r] = 1 if pair == {"Cy", "G"} else 0
    return out


def chroma_level(rgb, gain):
    """Niveau de couleur sur une image equilibree en blanc : les ecarts a la luminance sont multiplies.

    Luminance du camescope : Y = (2R + 3G + 2B) / 7 (fiche Sony ICX409AK).
    """
    if gain == 1.0:
        return rgb
    y = (2.0 * rgb[..., 0] + 3.0 * rgb[..., 1] + 2.0 * rgb[..., 2])[..., None] / 7.0
    return y + (rgb - y) * gain


def separate(p, chroma_f3db=0.0, vertical="1H", white=(1.0, 1.0, 1.0)):
    """Sommes de photosites (p, en unites RVB de scene) -> RVB lineaire de la camera.

    p           : (H, W), chaque ligne = alternance des deux sommes (colonne paire = colonne Cy)
    chroma_f3db : bande passante des differences de couleur, en cycles par photosite (0 = pas de filtre)
    vertical    : "1H" = l'autre difference vient de la ligne precedente de la meme trame (retard 1H),
                  "2H" = moyenne des lignes precedente et suivante de la meme trame
    white       : reponse R, V, B de la camera a un blanc (balance des blancs du camescope)

    Comme dans un camescope, Cr et Cb sont d'abord ramenes a des differences de couleur
    (R-Y, B-Y) : on leur retire la part que donnerait un blanc de meme luminance. Ce sont
    ces differences, nulles sur un gris, qui sont filtrees et retardees ; la luminance garde
    toute sa finesse. Un detail gris reste donc gris, et les fausses couleurs ne viennent que
    de ce qui est vraiment colore ou des contours horizontaux (difference entre voisins).
    """
    h, w = p.shape
    # 1. Luminance : somme de deux voisins (filtre [1 2 1] / 2, nul a la frequence de l'alternance).
    ysum = _hfilter(p, [0.5, 1.0, 0.5])
    # 2. Difference de couleur : demodulation de l'alternance, puis meme filtre.
    sign = np.where(np.arange(w) % 2 == 0, 1.0, -1.0)[None, :]
    # q = p * signe : +S_Cy sur les colonnes paires, -S_Ye sur les impaires. Le filtre [1 2 1] / 2
    # donne S_Cy - S_Ye sur toutes les colonnes (difference des deux voisins, centree).
    d = _hfilter(p * sign, [0.5, 1.0, 0.5])
    t = line_types(h)[:, None]
    own = np.where(t == 1, -d, d)                          # ligne rouge : Cr = Ye+Mg - (Cy+G) ; bleue : Cb
    # 2b. Differences de couleur relatives au blanc : un gris donne zero (balance des blancs sur R-Y, B-Y).
    rw, gw, bw = (float(v) for v in white)
    y_w, cr_w, cb_w = 2 * rw + 3 * gw + 2 * bw, 2 * rw - gw, 2 * bw - gw
    ref = np.where(t == 1, cr_w, cb_w) / y_w
    own = own - ref * ysum
    # 3. Bande passante des differences de couleur (filtre passe-bas horizontal).
    if chroma_f3db > 0:
        own = _hfilter(own, gaussian_lowpass_kernel(chroma_f3db))
    # 4. L'autre difference : lignes voisines de la MEME trame (+-2 lignes d'image), de type oppose.
    above = np.concatenate([own[:2], own[:-2]], axis=0)     # ligne precedente de la trame (1H)
    if vertical == "2H":
        below = np.concatenate([own[2:], own[-2:]], axis=0)
        other = 0.5 * (above + below)
    elif vertical == "1H":
        other = above
    else:
        raise ValueError("cmyg_chroma_vertical inconnu : %r" % vertical)
    # Les deux premieres lignes n'ont pas de ligne precedente : la camera reprend la suivante.
    other[:2] = own[2:4] if h >= 4 else other[:2]
    # Retour a Cr et Cb avec la luminance pleine finesse de la ligne.
    cr = np.where(t == 1, own, other) + (cr_w / y_w) * ysum
    cb = np.where(t == 0, own, other) + (cb_w / y_w) * ysum
    # 5. Matrice : Y = 2R+3G+2B, Cr = 2R-G, Cb = 2B-G  ->  G = (Y - Cr - Cb) / 5.
    g = (ysum - cr - cb) / 5.0
    r = 0.5 * (cr + g)
    b = 0.5 * (cb + g)
    return np.stack([r, g, b], axis=-1)
