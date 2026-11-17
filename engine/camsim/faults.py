"""Pannes et defauts de fabrication, injectes a l'etage ou ils naissent vraiment.

Chaque fonction correspond a un element reel de la chaine de lecture d'un capteur :

  matrice de pixels   pixels morts ou colles, lignes et colonnes mortes,
                      erreurs d'adressage (une ligne d'adresse collee ou parasitee)
  amplis de colonnes  decalage et gain propres a chaque colonne (bruit de colonnes fixe)
  alimentation        parasite periodique ajoute au signal analogique
  convertisseur       non-linearites (DNL, INL), codes manquants, bits colles,
                      erreurs de bits aleatoires ("sparkle codes")
  liaison serie       glissement de bits (bitslip), glissement de l'horloge pixel
  memoire tampon      image dechiree (une partie vient de l'image precedente)

Les valeurs sont en DN (valeurs numeriques du brut). Rien ici n'est calibre sur une
vraie camera : ce sont des mecanismes reels, avec des reglages libres.
"""
import numpy as np


def parse_bits(spec):
    """'5:1,2:0' -> [(5, 1), (2, 0)] : bit numero 5 colle a 1, bit numero 2 colle a 0."""
    if not spec:
        return []
    out = []
    for part in str(spec).replace(";", ",").split(","):
        part = part.strip()
        if part:
            b, _, v = part.partition(":")
            out.append((int(b), int(v or 1)))
    return out


# ---------------------------------------------------------------- matrice de pixels
def defect_masks(rng, h, w, dead_fraction, stuck_fraction, dead_columns, dead_rows):
    """Defauts de fabrication, figes. Renvoie (masque_noir, masque_blanc)."""
    black = np.zeros((h, w), dtype=bool)
    white = np.zeros((h, w), dtype=bool)
    if dead_fraction > 0:
        black |= rng.random((h, w)) < dead_fraction
    if stuck_fraction > 0:
        white |= rng.random((h, w)) < stuck_fraction
    for _ in range(int(dead_columns)):
        c = int(rng.integers(0, w))
        (white if rng.random() < 0.3 else black)[:, c] = True      # la plupart sont noires
    for _ in range(int(dead_rows)):
        r = int(rng.integers(0, h))
        (white if rng.random() < 0.3 else black)[r, :] = True
    return black, white


def address_map(n, stuck_spec, error_rate, rng):
    """Adresse reellement lue pour chaque ligne (ou colonne) demandee.

    stuck_spec : bits d'adresse colles ('3:0' = la ligne d'adresse numero 3 reste a 0).
    error_rate : probabilite qu'un parasite inverse un bit d'adresse pendant une lecture.
    Une adresse hors du capteur est marquee -1 (rien n'est lu).
    """
    idx = np.arange(n, dtype=np.int64)
    for bit, val in parse_bits(stuck_spec):
        idx = (idx | (1 << bit)) if val else (idx & ~(1 << bit))
    if error_rate > 0:
        nbits = max(int(np.ceil(np.log2(n))), 1)
        hit = rng.random(n) < error_rate
        flip = np.left_shift(1, rng.integers(0, nbits, size=n))
        idx = np.where(hit, idx ^ flip, idx)
    return np.where(idx < n, idx, -1)


def remap(dn, row_map, col_map, fill):
    out = dn
    if row_map is not None:
        out = np.where((row_map >= 0)[:, None], out[np.maximum(row_map, 0), :], fill)
    if col_map is not None:
        out = np.where((col_map >= 0)[None, :], out[:, np.maximum(col_map, 0)], fill)
    return out


# ---------------------------------------------------------------- analogique
def interference(h, w, frame, amplitude_dn, period_px, phase=0.0):
    """Parasite sinusoidal le long de la lecture (ligne apres ligne, image apres image).

    Une periode qui ne tombe pas juste sur la largeur donne des bandes diagonales
    qui defilent, comme un ronflement d'alimentation.
    """
    start = (float(frame) * h * w) % period_px
    i = start + np.arange(h, dtype=np.float64)[:, None] * w + np.arange(w, dtype=np.float64)[None, :]
    return amplitude_dn * np.sin(2.0 * np.pi * i / period_px + phase)


# ---------------------------------------------------------------- convertisseur
def adc_thresholds(rng, n_codes, dnl_sigma):
    """Seuils de transition d'un convertisseur dont les marches n'ont pas toutes la meme largeur.

    DNL (non-linearite differentielle) : ecart de largeur de chaque marche, en LSB.
    Une marche de largeur presque nulle donne un code manquant.
    """
    widths = np.maximum(1.0 + dnl_sigma * rng.standard_normal(n_codes), 0.0)
    widths *= n_codes / widths.sum()
    return np.cumsum(widths)[:-1] - 0.5          # ideal : 0.5, 1.5, 2.5, ...


def adc_convert(analog, white, native_bits, adc_bits, thresholds=None, inl_lsb=0.0):
    """Tension analogique (en DN flottants) -> codes entiers, remis sur l'echelle native."""
    step = 2.0 ** (native_bits - adc_bits)
    x = analog / step
    top = np.floor(white / step)
    if inl_lsb:
        # INL (non-linearite integrale) : courbure en arc, nulle aux deux bouts de l'echelle.
        x = x + inl_lsb * np.sin(np.pi * np.clip(x / top, 0.0, 1.0))
    if thresholds is None:
        code = np.rint(x)
    else:
        code = np.searchsorted(thresholds, x.ravel(), side="right").reshape(x.shape).astype(np.float64)
    return np.clip(code, 0.0, top) * step


def stuck_bits(dn, spec):
    """Bits de sortie du convertisseur colles a 0 ou a 1."""
    bits = parse_bits(spec)
    if not bits:
        return dn
    v = dn.astype(np.int64)
    for bit, val in bits:
        v = (v | (1 << bit)) if val else (v & ~(1 << bit))
    return v.astype(np.float64)


def sparkle(dn, rate, native_bits, rng):
    """Erreurs de bits aleatoires : avec la probabilite 'rate', un bit du code est inverse."""
    if rate <= 0:
        return dn
    v = dn.astype(np.int64)
    hit = rng.random(v.shape) < rate
    flip = np.left_shift(1, rng.integers(0, native_bits, size=v.shape))
    return np.where(hit, v ^ flip, v).astype(np.float64)


# ---------------------------------------------------------------- liaison serie
def bitslip(dn, n, native_bits):
    """Glissement de n bits dans le flux serie : chaque mot commence n bits trop tard.

    Le mot recu est fait de la fin du pixel courant et du debut du pixel suivant.
    """
    n = int(n) % native_bits
    if n == 0:
        return dn
    v = dn.astype(np.int64)
    nxt = np.zeros_like(v)
    nxt[:, :-1] = v[:, 1:]
    mask = (1 << native_bits) - 1
    return (((v << n) & mask) | (nxt >> (native_bits - n))).astype(np.float64)


def line_shift(dn, rate, max_px, persist, fill, rng):
    """Glissement de l'horloge pixel : certaines lignes sont decalees de quelques pixels.

    persist=False : chaque ligne se resynchronise (decalages isoles).
    persist=True  : la synchro de ligne est perdue, les decalages s'accumulent vers le bas.
    """
    if rate <= 0 or max_px <= 0:
        return dn
    h, w = dn.shape
    hit = rng.random(h) < rate
    slip = np.where(hit, rng.integers(-int(max_px), int(max_px) + 1, size=h), 0)
    off = np.cumsum(slip) if persist else slip
    out = dn.copy()
    for y in np.nonzero(off)[0]:
        k = int(off[y]) % w if persist else int(off[y])
        row = np.roll(dn[y], k)
        if not persist:                      # les echantillons perdus ne reviennent pas
            if k > 0:
                row[:k] = fill
            else:
                row[k:] = fill
        out[y] = row
    return out
