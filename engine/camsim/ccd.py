"""Capteur CCD : ce qui arrive aux charges entre le photosite et la sortie.

Dans un CCD, les charges ne sont pas lues sur place : elles sont transportees de
proche en proche, d'abord le long des colonnes (registres verticaux), puis le
long d'une ligne (registre horizontal), jusqu'a un amplificateur de sortie unique.
Les defauts typiques viennent de ce transport.

  eblouissement (blooming)  un photosite sature deborde dans ses voisins de la colonne
  trainee verticale (smear) la lumiere continue d'arriver pendant le transport
  transfert imparfait (CTI) chaque transfert laisse une petite part de charge en arriere
  colonne bloquee           un defaut arrete les charges : tout ce qui est derriere est perdu

Valeurs de reference : fiche technique du capteur interligne Kodak / onsemi KAI-0373
(768 x 484, 2/3 de pouce), rev. 4, mars 2015.
"""
import numpy as np


def blooming(electrons, full_well, antiblooming, max_iter=400):
    """Debordement vertical des photosites satures.

    antiblooming : facteur de protection (fiche KAI-0373 : 300, c'est-a-dire que le
    drain evacue l'exces jusqu'a 300 fois l'eclairement de saturation). Au-dela, le
    surplus deborde, moitie vers le haut, moitie vers le bas, jusqu'a trouver de la place.
    Renvoie les charges apres debordement (chaque photosite <= full_well).
    """
    # Le drain evacue tout l'exces tant que la charge recue reste sous antiblooming x puits.
    excess = np.maximum(electrons - full_well * max(antiblooming, 1.0), 0.0)
    e = np.minimum(electrons, full_well)
    if excess.max() <= 0:
        return e
    # Seules les colonnes touchees sont traitees. L'exces part moitie vers le haut, moitie
    # vers le bas, et garde sa direction : il remplit les photosites rencontres jusqu'a
    # etre absorbe. Au bord du capteur, la charge sort de la matrice et est perdue.
    cols = np.nonzero(excess.max(axis=0) > 0)[0]
    sub = e[:, cols]
    up = 0.5 * excess[:, cols]
    down = up.copy()
    for _ in range(max(max_iter, e.shape[0])):
        if up.max() <= 1e-6 and down.max() <= 1e-6:
            break
        moved = np.zeros_like(up)
        moved[:-1] = up[1:]                       # le flux montant avance d'un photosite
        take = np.minimum(moved, full_well - sub)
        sub = sub + take
        up = moved - take
        moved = np.zeros_like(down)
        moved[1:] = down[:-1]                     # le flux descendant aussi
        take = np.minimum(moved, full_well - sub)
        sub = sub + take
        down = moved - take
    e[:, cols] = sub
    return e


def smear(expected_electrons, smear_fraction, exposure_s, ref_exposure_s, rng=None):
    """Trainee verticale : charge ajoutee a toute la colonne pendant le transport.

    Chaque paquet de charges traverse toute sa colonne ; il recoit au passage une
    petite part de la lumiere de chaque photosite. La trainee est donc la meme du
    haut en bas de la colonne et vaut

        k * moyenne de la colonne,   k = 10 * smear_fraction * T_ref / T_pose

    smear_fraction est la valeur de fiche technique, mesuree avec une tache blanche
    sur 10 % de la hauteur (d'ou le facteur 10). La trainee ne depend pas du temps
    de pose alors que l'image, si : en relatif, elle augmente quand la pose diminue
    (fiche KAI-0373 : "This effect is basic to interline devices").

    expected_electrons : charges attendues AVANT saturation (la lumiere continue
    d'arriver meme quand le photosite est plein).
    """
    if smear_fraction <= 0:
        return None
    k = 10.0 * smear_fraction * ref_exposure_s / max(exposure_s, 1e-9)
    col = k * expected_electrons.mean(axis=0, keepdims=True)
    col = np.broadcast_to(col, expected_electrons.shape)
    return rng.poisson(col).astype(np.float64) if rng is not None else col.copy()


def transfer_inefficiency(e, eps_vertical, eps_horizontal, terms=5):
    """Transfert de charge imparfait (CTI = 1 - CTE).

    Un paquet situe a n transferts de la sortie laisse en arriere, a chaque transfert,
    une fraction eps de sa charge. Apres n transferts, sa charge est repartie entre
    lui et les paquets qui le suivent selon une loi binomiale, approchee ici par une
    loi de Poisson de parametre n * eps (exact tant que eps est petit).
    Le registre de sortie est en haut (ligne 0) et a gauche (colonne 0) : les
    trainees partent vers le bas et vers la droite.
    """
    out = e
    for axis, eps in ((0, eps_vertical), (1, eps_horizontal)):
        if eps <= 0:
            continue
        n = np.arange(1, out.shape[axis] + 1, dtype=np.float64)
        lam = (n * eps).reshape((-1, 1) if axis == 0 else (1, -1))
        acc = np.zeros_like(out)
        w = np.exp(-lam)
        for k in range(terms + 1):
            src = out * w                                 # part que chaque paquet depose k cases derriere lui
            if k > 0:
                shifted = np.zeros_like(src)
                if axis == 0:
                    shifted[k:] = src[:-k]
                else:
                    shifted[:, k:] = src[:, :-k]
                src = shifted
            acc += src
            w = w * lam / (k + 1)
        out = acc
    return out


def blocked_columns(e, count, rng):
    """Colonnes bloquees : au-dela du defaut (en s'eloignant de la sortie), les charges ne passent plus."""
    if count <= 0:
        return e
    h, w = e.shape
    out = e.copy()
    for _ in range(int(count)):
        x = int(rng.integers(0, w))
        y = int(rng.integers(0, max(h // 2, 1)))
        out[y:, x] = 0.0
    return out
