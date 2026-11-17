"""Fiche des reglages de l'effet After Effects : un exemple avant / apres par reglage.

Chaque exemple passe par le coeur natif (camsim_cli), avec les memes valeurs par defaut que l'effet AE.
Une scene de test synthetique (ACEScg lineaire) est construite pour montrer chaque defaut :
pastilles de couleur, mire de cercles (details fins), point tres lumineux dans le noir, gamme de gris,
zone sombre unie, barres noires et blanches.

Sortie : out/fiche/cards.json (images en JPEG base64 + textes) et un resume dans la console, qui
signale les reglages dont l'effet est trop faible pour se voir sur la scene.

Usage : python tools/make_fiche.py
"""
import base64
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
from camsim.color import preview_srgb      # noqa: E402

CLI = ROOT / "native" / "build" / "core" / "Release" / "camsim_cli.exe"
OUT = ROOT / "out" / "fiche"
W, H = 480, 270
A7S, D850, C5D, C70D, C700D, CCD3, CAM = 0, 1, 2, 3, 4, 5, 6
PNAMES = {A7S: "Sony a7S II", C700D: "Canon 700D", CCD3: "3 CCD (KAI-0373)", CAM: "Camescope (ICX409AK)", C70D: "Canon 70D"}
AE_DEFAULTS = dict(dsp_mode=0)             # l'effet AE laisse l'etage video au profil


def scene():
    img = np.full((H, W, 3), 0.10)
    # Pastilles de couleur (haut gauche).
    cols = [(0.35, 0.05, 0.03), (0.06, 0.25, 0.04), (0.03, 0.05, 0.30),
            (0.35, 0.20, 0.13), (0.40, 0.35, 0.03), (0.03, 0.25, 0.30)]
    for i, c in enumerate(cols):
        x, y = 8 + (i % 3) * 50, 8 + (i // 3) * 62
        img[y:y + 54, x:x + 44] = c
    # Mire de cercles (haut milieu) : details de plus en plus fins vers les bords.
    yy, xx = np.mgrid[0:135, 0:160].astype(float)
    r2 = ((xx - 80) ** 2 + (yy - 67) ** 2)
    img[0:135, 160:320] = (0.02 + 0.5 * (0.5 + 0.5 * np.cos(r2 * np.pi / 140)))[..., None]
    # Point tres lumineux dans le noir (haut droite) et petit blanc.
    img[0:135, 320:480] = 0.004
    yy, xx = np.mgrid[0:135, 0:160]
    img[0:135, 320:480][(xx - 60) ** 2 + (yy - 60) ** 2 <= 9] = 80.0
    img[95:120, 115:145] = 0.9
    # Gamme de gris par diaphragmes (milieu) : 12 marches de -9 a +2 IL autour du gris 18 %.
    for i in range(12):
        img[140:200, i * 40:(i + 1) * 40] = 0.18 * 2.0 ** (i - 9)
    # Zone sombre unie (bas gauche) et barres noires / blanches de plus en plus fines (bas droite).
    img[205:270, 0:200] = 0.012
    x = 200
    for wbar in (12, 8, 5, 3, 2, 1, 1):
        for k in range(3):
            if x + 2 * wbar > W:
                break
            img[205:270, x:x + wbar] = 0.008
            img[205:270, x + wbar:x + 2 * wbar] = 0.75
            x += 2 * wbar
    img[205:270, x:W] = np.linspace(0.008, 0.75, W - x)[None, :, None] if x < W else 0
    return img.astype(np.float32)


def panned(img, dx):
    """La scene deplacee de dx pixels (panoramique), bords en miroir."""
    import cv2
    m = np.float32([[1, 0, dx], [0, 1, 0]])
    return cv2.warpAffine(img, m, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)


def run(img, frame=3, pan=0, **kw):
    """pan : panoramique en pixels par image ; l'image precedente et la suivante sont alors fournies."""
    with tempfile.TemporaryDirectory() as td:
        a, b = Path(td) / "in.raw", Path(td) / "out.raw"
        img.astype("<f4").tofile(a)
        args = [str(CLI), str(a), str(b), str(W), str(H), str(frame)] + ["%s=%s" % (k, v) for k, v in kw.items()]
        if pan:
            for name, dx in (("prev", -pan), ("next", pan)):
                f = Path(td) / (name + ".raw")
                panned(img, dx).astype("<f4").tofile(f)
                args.append("%s=%s" % (name, f))
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        return np.fromfile(b, dtype="<f4").reshape(H, W, 3)


def jpeg(u8, q=82):
    """Image WebP en base64 (plus compacte que le JPEG pour le grain)."""
    buf = io.BytesIO()
    im = Image.fromarray(u8)
    if u8.ndim == 3 and np.array_equal(u8[..., 0], u8[..., 2]) and np.array_equal(u8[..., 0], u8[..., 1]):
        im = im.convert("L")
    im.save(buf, "WEBP", quality=q, method=6)
    return base64.b64encode(buf.getvalue()).decode()


# (groupe, nom dans AE, cle, valeur de l'exemple, texte de la valeur, camera, reglages de base, explication, condition)
# Les explications sont redigees pour l'utilisateur (motion designer) : ce que l'on voit, et pourquoi.
S = []


# Zones a agrandir (x, y, largeur, hauteur) pour les effets tres localises.
SPOT = (340, 20, 90, 90)          # point tres lumineux
DARK = (0, 205, 120, 65)          # zone sombre unie
ZOOM = {"clip_full_well": SPOT, "ccd_antiblooming": SPOT, "ccd_smear_gain": SPOT, "hot_pixel_fraction": DARK,
        "hot_pixel_gain": DARK, "dead_pixel_fraction": (20, 10, 110, 60), "stuck_pixel_fraction": (20, 10, 110, 60),
        "adc_sparkle_rate": DARK, "ccd_cti_v": SPOT, "ccd_cti_h": SPOT, "demosaic": (190, 10, 100, 60),
        "olpf_h_px": (190, 10, 100, 60), "olpf_v_px": (190, 10, 100, 60), "registration_r_dx": (200, 205, 120, 65),
        "registration_r_dy": (0, 50, 110, 70), "registration_b_dx": (200, 205, 120, 65), "registration_b_dy": (0, 50, 110, 70),
        "cmyg_line_crawl": (0, 0, 160, 135), "cmyg_vertical_2h": (0, 0, 160, 135), "analog_comb": (200, 205, 160, 65),
        "analog_chroma_delay_us": (0, 0, 160, 135), "prnu_sigma": (300, 140, 180, 60), "dsp_detail_coring": DARK,
        "analog_head_switch_lines": (0, 200, 480, 70), "analog_head_switch_us": (0, 200, 480, 70)}


def add(group, name, key, val, shown, prof, base, text, cond=None):
    S.append(dict(group=group, name=name, key=key, val=val, shown=shown, prof=prof, base=base, text=text, cond=cond,
                  zoom=ZOOM.get(key)))


G = "Camera et espace de travail"
add(G, "Camera", "profile", CAM, "Camescope", A7S, {},
    "Choisit l'appareil simule. Chaque camera a ses mesures : bruit, couleurs, taille et pas des photosites, "
    "electronique. Beaucoup de reglages plus bas ne concernent qu'un type de camera (voir « Agit seulement si »).")

add(G, "Calcul", "gpu", 0, "Processeur", A7S, dict(gpu=1, iso=3200),
    "Ou se fait le calcul : sur la carte graphique NVIDIA (toutes depuis 2014, GeForce GTX 900 et plus recentes, "
    "pilote 528 ou plus recent) ou sur le processeur. Le resultat est exactement le meme, bruit compris (la "
    "difference est nulle) ; la carte graphique est environ quatre fois plus rapide pour un appareil photo. "
    "Sans carte NVIDIA, l'effet calcule sur le processeur. Sur la carte graphique pour l'instant : la chaine des "
    "appareils photo, et la mesure du mouvement (obturateur deroulant, seconde trame), environ cinq fois plus "
    "vite ; le reste (CCD, camescope, mode video, etage video) suit.")

G = "Prise de vue"
add(G, "ISO", "iso", 25600, "25 600 (au lieu de 800)", A7S, {},
    "Sensibilite. La luminosite de l'image reste la meme (on suppose l'exposition reglee pour), mais le capteur "
    "recoit moins de lumiere : le grain monte, surtout dans les ombres.")
add(G, "Exposition (diaph.)", "ev", 2, "+2", A7S, {},
    "Sur- ou sous-exposition en diaphragmes. +1 = deux fois plus de lumiere. Les hautes lumieres saturent, "
    "les ombres sous-exposees deviennent bruitees.")
add(G, "Temps de pose (s)", "exposure_time_s", 4, "4 s (au lieu de 0,02 s)", A7S, dict(dark_current=20, hot_pixel_fraction=0.002),
    "Duree d'accumulation du courant d'obscurite (des electrons crees par la chaleur, sans lumiere). "
    "Il n'agit pas sur la luminosite de l'image. Effet tres faible aux temps de pose video : il se voit en pose "
    "longue, dans les noirs (ici avec un courant d'obscurite de 20 e-/s et quelques pixels chauds).",
    "courant d'obscurite > 0 (mesure sur les CCD ; a regler sur les appareils photo)")
add(G, "Temperature du capteur (C)", "temperature_c", 70, "70 °C (au lieu de 30)", A7S,
    dict(exposure_time_s=1, dark_current=20, hot_pixel_fraction=0.002),
    "Le courant d'obscurite double environ tous les 6 °C. Noirs qui montent, grain et pixels chauds en plus. "
    "Comme le temps de pose : discret a 1/50 s, visible en pose longue.", "courant d'obscurite > 0")
add(G, "Numero de serie (graine)", "seed", 7, "7 (au lieu de 1)", A7S, dict(iso=6400),
    "Change d'exemplaire de camera : les memes defauts, mais a d'autres endroits (pixels chauds, rayures, grain fige). "
    "L'image se ressemble ; seule la difference montre le changement.")

G = "Resolution et lecture video"
add(G, "Resolution de la simulation", "resolution", 1, "Vraie grille du capteur", CAM, {},
    "« Celle de l'image » : un pixel de l'image = un photosite. « Vraie grille du capteur » : l'image est ramenee "
    "au nombre reel de photosites de la camera (752 x 582 pour le camescope), puis agrandie. Le grain et les "
    "defauts prennent alors leur vraie taille.", "camera dont la grille est connue")
add(G, "Agrandissement", "upscale", 2, "Plus proche", CAM, dict(resolution=1),
    "Methode pour revenir a la taille de l'image. Lanczos : net ; bilineaire : doux ; plus proche : gros pixels.",
    "« Vraie grille du capteur » ou mode video")
add(G, "Mode video (appareils photo)", "video_mode", 1, "1080p", C700D, {},
    "Lit le capteur comme l'appareil le fait en video : sur les reflex anciens, une ligne sur trois (crenelage, "
    "moirage sur les details fins), puis mise a la taille 1920 x 1080.", "appareils photo qui ont ce mode")
add(G, "Lecture video", "video_readout", 2, "Regroupement 3x3", C700D, dict(video_mode=1),
    "Force une autre facon de lire le capteur en video : tout lire, 1 ligne sur 3, regroupement 3 x 3 (moins de "
    "moirage, plus doux), regroupement vertical par 2.", "mode video 1080p ou 4K")

G = "Bruit"
add(G, "Bruit de photons", "shot_noise", 0, "Coupe", A7S, dict(iso=12800),
    "Le grain naturel de la lumiere (les photons arrivent au hasard). C'est le grain principal dans les tons moyens. "
    "Le couper ne laisse que le bruit de l'electronique.")
add(G, "Bruit de lecture", "read_model", 2, "Aucun", A7S, dict(iso=12800),
    "Bruit de l'electronique de lecture, visible dans les noirs. « Tukey-lambda » : forme mesuree sur la camera "
    "(quelques valeurs extremes) ; « Gaussien » : forme simple ; « Aucun ».")
add(G, "Bruit de lecture x", "read_noise_gain", 6, "x 6", A7S, dict(iso=3200),
    "Multiplie le bruit de lecture mesure. Grain dans les noirs.")
add(G, "Bruit de ligne x", "row_noise_gain", 15, "x 15", A7S, dict(iso=6400),
    "Bruit commun a toute une ligne : rayures horizontales fines qui changent a chaque image. Typique des CMOS a "
    "haut ISO.")
add(G, "Biais du noir par voie x", "color_bias_gain", 15, "x 15", A7S, dict(iso=6400),
    "Petite erreur de niveau noir differente pour le rouge, le vert et le bleu : ombres teintees.")
add(G, "Non-uniformite (%, -1 = profil)", "prnu_sigma", 0.05, "5 %", A7S, {},
    "Chaque photosite a une sensibilite legerement differente (motif fige). Se voit surtout dans les zones claires "
    "unies, comme une fine texture qui ne bouge pas. Mesuree a 1 % sur les CCD ; non mesuree (0) sur les appareils "
    "photo : -1 garde la valeur du profil.")
add(G, "Courant d'obscurite (e-/s a 20 C, -1 = profil)", "dark_current", 200, "200 e-/s", A7S, dict(exposure_time_s=0.5),
    "Electrons crees par la chaleur, sans lumiere. Noirs qui montent et deviennent granuleux, de facon irreguliere "
    "(chaque photosite fuit differemment). Mesure sur les CCD (environ 140 e-/s) ; non mesure (0) sur les "
    "appareils photo.", "temps de pose assez long")
add(G, "Part de pixels chauds", "hot_pixel_fraction", 0.003, "0,3 %", A7S, dict(exposure_time_s=0.5, dark_current=5),
    "Photosites qui fuient beaucoup plus que les autres : points brillants fixes, surtout visibles dans le noir. "
    "Leur force suit le courant d'obscurite, le temps de pose et la temperature.", "courant d'obscurite > 0")
add(G, "Force des pixels chauds", "hot_pixel_gain", 10000, "10 000 (au lieu de 1000)", A7S,
    dict(hot_pixel_fraction=0.003, exposure_time_s=0.5, dark_current=5),
    "Combien de fois un pixel chaud fuit plus qu'un photosite normal (au plus).", "pixels chauds > 0")

G = "Temps et mouvement"
PAN = 15                                   # panoramique de l'exemple : 15 px par image, vers la droite
add(G, "Temps de lecture des lignes (ms, 0 = global)", "readout_time_s", 0.04, "40 ms", A7S, dict(pan=PAN),
    "Obturateur deroulant : les lignes d'un capteur CMOS sont lues l'une apres l'autre, pas toutes en meme temps. "
    "Ce qui bouge pendant la lecture est penche ou deforme (« jello »). Exemple : panoramique de 15 px par "
    "image ; les barres verticales penchent. 0 = obturateur global (CCD, cameras de cinema recentes). "
    "Ordres de grandeur publies : 15 a 35 ms pour les reflex et hybrides en video.",
    "images voisines disponibles (calque anime)")
add(G, "Sens de lecture", "bottom_to_top", 1, "Du bas vers le haut", A7S, dict(pan=PAN, readout_time_s=0.04),
    "Sens dans lequel les lignes sont lues. Change le sens de l'inclinaison.", "temps de lecture > 0")
add(G, "Mouvement entre images", "pan", 0, "Ignore", CAM, dict(pan=PAN),
    "L'effet regarde l'image precedente et la suivante pour savoir comment la scene bouge. Il s'en sert pour "
    "l'obturateur deroulant et pour la seconde trame des cameras entrelacees : prise 1/50 s plus tard, elle "
    "donne les « dents de peigne » sur ce qui bouge (exemple : camescope). « Ignore » : plus rapide, sans ces "
    "effets.", "obturateur deroulant, ou camera entrelacee (3 CCD par trames, camescope)")
add(G, "Precision du mouvement", "fast_motion", 1, "Rapide", A7S, dict(pan=PAN, readout_time_s=0.04),
    "« Fine » : le mouvement est mesure comme dans le moteur de reference (a la moitie de la resolution). "
    "« Rapide » : au quart de la resolution, deux fois plus vite ; la difference ne se voit que sur des details "
    "fins qui bougent differemment de ce qui les entoure. Pratique pendant les reglages, « Fine » pour le rendu.",
    "mouvement estime (obturateur deroulant ou camera entrelacee)")
add(G, "Scintillement de l'eclairage (Hz, 0 = aucun)", "flicker_hz", 100, "100 Hz", A7S,
    dict(readout_time_s=0.03, exposure_time_s=0.002, flicker_depth=0.3),
    "Un eclairage branche sur le secteur varie a deux fois la frequence du reseau (100 Hz en Europe, 120 Hz en "
    "Amerique du Nord). Avec un obturateur deroulant, cela fait des bandes horizontales sombres qui defilent ; "
    "avec un obturateur global, toute l'image pulse. Plus le temps de pose est court, plus c'est marque.")
add(G, "Profondeur du scintillement", "flicker_depth", 0.8, "0,8 (au lieu de 0,3)", A7S,
    dict(readout_time_s=0.03, exposure_time_s=0.002, flicker_hz=100),
    "Force de la variation de la lumiere : environ 0,1 pour une ampoule a incandescence, jusqu'a 1 pour certaines "
    "LED ou tubes fluorescents.", "scintillement > 0")
add(G, "Phase du scintillement (degres)", "flicker_phase", 3.14159265 / 2, "90 degres", A7S,
    dict(readout_time_s=0.03, exposure_time_s=0.002, flicker_hz=100, flicker_depth=0.3),
    "Decale les bandes dans l'image (le moment du cycle de la lumiere ou commence la lecture).", "scintillement > 0")

G = "Capteur"
add(G, "Filtre passe-bas horizontal (px)", "olpf_h_px", 0, "0 (coupe)", C700D, {},
    "Le filtre optique devant le capteur, qui adoucit legerement pour eviter le moirage. A 0 : plus net, mais "
    "fausses couleurs et moirage sur les details fins (mire de cercles).")
add(G, "Filtre passe-bas vertical (px)", "olpf_v_px", 2.5, "2,5", C700D, {},
    "Meme chose verticalement. Plus grand = image plus douce verticalement.")
add(G, "Profondeur (bits, 0 = profil)", "adc_bits", 6, "6 bits", A7S, {},
    "Nombre de niveaux du convertisseur. Peu de bits : marches visibles dans les degrades (postérisation), surtout "
    "dans les ombres. 0 = profondeur reelle de la camera (12 a 14 bits).")
add(G, "Saturation du puits", "clip_full_well", 0, "Coupe", CCD3, dict(ccd_antiblooming=1),
    "Chaque photosite ne peut garder qu'un nombre limite d'electrons ; au-dela, ils debordent (eblouissement sur "
    "les CCD). Coupe : pas de debordement (non physique, utile pour comparer). Sur les appareils photo, le "
    "convertisseur sature en general avant le puits : le reglage y change peu.")
add(G, "Ombrage vers les coins (-1 = profil)", "sensor_shading", 0.6, "0,6", A7S, {},
    "Le capteur lui-meme recoit moins bien la lumiere vers les bords (angle des microlentilles). Coins plus sombres. "
    "Ce n'est pas le vignetage de l'objectif.")

G = "Defauts fixes"
add(G, "Decalage des colonnes (DN)", "column_fpn_dn", 40, "40", A7S, dict(iso=6400),
    "Chaque colonne a son propre niveau noir : rayures verticales fixes dans les ombres.")
add(G, "Ecart de gain des colonnes", "column_gain_sigma", 0.08, "0,08", A7S, {},
    "Chaque colonne amplifie un peu differemment : rayures verticales dans les zones claires.")
add(G, "Part de pixels morts", "dead_pixel_fraction", 0.01, "1 %", A7S, {},
    "Photosites qui ne repondent plus (noirs). Apres le dematricage, ils deviennent de petites taches colorees.")
add(G, "Part de pixels colles", "stuck_pixel_fraction", 0.003, "0,3 %", A7S, {},
    "Photosites bloques au maximum : points brillants colores, meme dans le blanc.")
add(G, "Colonnes mortes", "dead_columns", 12, "12", A7S, {},
    "Colonnes entieres qui ne repondent plus : traits verticaux.")
add(G, "Lignes mortes", "dead_rows", 8, "8", A7S, {},
    "Lignes entieres qui ne repondent plus : traits horizontaux.")

G = "Pannes"
add(G, "Bit d'adresse de ligne colle (-1 = aucun)", "row_address_bit", 4, "4", A7S, {},
    "Un fil d'adresse du decodeur de lignes est bloque : certaines lignes sont lues a la place d'autres. L'image se "
    "repete en bandes horizontales (bit 4 = bandes de 16 lignes ; plus le bit est grand, plus les bandes sont "
    "grandes).")
add(G, "... colle a 1 (sinon a 0)", "row_address_value", 1, "Coche", A7S, dict(row_address_bit=4),
    "Choisit si le fil est bloque a 1 ou a 0 : les bandes repetees sont decalees.", "un bit de ligne est choisi")
add(G, "Bit d'adresse de colonne colle (-1 = aucun)", "col_address_bit", 4, "4", A7S, {},
    "Meme panne pour les colonnes : bandes verticales repetees.")
add(G, "... colle a 1 (sinon a 0) ", "col_address_value", 1, "Coche", A7S, dict(col_address_bit=4),
    "Bloque a 1 ou a 0.", "un bit de colonne est choisi")
add(G, "Lignes lues au mauvais endroit", "row_address_error_rate", 0.08, "8 %", A7S, {},
    "Erreurs d'adresse au hasard : des lignes isolees viennent d'ailleurs dans l'image. Change a chaque image.")
add(G, "Colonnes lues au mauvais endroit", "col_address_error_rate", 0.08, "8 %", A7S, {},
    "Meme chose pour des colonnes.")
add(G, "Parasite d'alimentation (DN)", "interference_dn", 300, "300", A7S, {},
    "Une frequence parasite (alimentation, horloge) s'ajoute pendant la lecture : fines rayures obliques.")
add(G, "Periode du parasite (px)", "interference_period_px", 40, "40 (au lieu de 517)", A7S, dict(interference_dn=300),
    "Pas des rayures du parasite, en pixels lus. Change leur finesse et leur angle.", "« Parasite » > 0")
add(G, "Glissement de bits", "bitslip", 2, "2", A7S, {},
    "La liaison numerique perd l'alignement des bits : les valeurs sont multipliees ou divisees par 2^n, avec des "
    "couleurs fausses et un aspect solarise.")
add(G, "Lignes qui glissent", "line_shift_rate", 0.1, "10 %", A7S, {},
    "Perte de synchronisation : des lignes sont decalees horizontalement.")
add(G, "Glissement maximal (px)", "line_shift_max_px", 64, "64 (au lieu de 8)", A7S, dict(line_shift_rate=0.1),
    "Amplitude maximale du decalage des lignes.", "« Lignes qui glissent » > 0")
add(G, "Synchro perdue (glissements cumules)", "line_shift_persist", 1, "Coche", A7S, dict(line_shift_rate=0.05),
    "Chaque decalage s'ajoute aux suivants : l'image se dechire en escalier vers le bas.", "« Lignes qui glissent » > 0")

G = "Convertisseur"
add(G, "Marches irregulieres (LSB)", "adc_dnl_lsb", 1.2, "1,2", A7S, dict(adc_bits=7),
    "Les marches du convertisseur n'ont pas toutes la meme hauteur : bandes irregulieres dans les degrades. "
    "En LSB = en marches : a 14 bits, une marche est minuscule et l'effet est invisible.",
    "profondeur faible (ici 7 bits)")
add(G, "Courbure (LSB)", "adc_inl_lsb", 2000, "2000", A7S, {},
    "La reponse du convertisseur n'est pas droite : les tons moyens sont un peu trop clairs ou sombres. Effet sur le "
    "contraste et une legere teinte. Il faut des centaines de LSB pour le voir a 14 bits.")
add(G, "Bit colle (-1 = aucun)", "adc_stuck_bit", 11, "11", A7S, {},
    "Un bit du convertisseur est bloque : des plages de valeurs sautent, cassures brutales dans les degrades. Les "
    "bits hauts (pres de la profondeur) font des effets enormes ; les bits bas sont invisibles.")
add(G, "... colle a 1 (sinon a 0)  ", "adc_stuck_value", 0, "Decoche (bloque a 0)", A7S, dict(adc_stuck_bit=11),
    "Bloque a 1 (eclaircit) ou a 0 (assombrit).", "un bit est choisi")
add(G, "Bits inverses au hasard", "adc_sparkle_rate", 0.02, "2 %", A7S, {},
    "Erreurs isolees du convertisseur : points brillants ou noirs qui scintillent d'une image a l'autre.")

G = "CCD et 3 capteurs"
add(G, "Trainee verticale x", "ccd_smear_gain", 200, "x 200", CCD3, {},
    "CCD : la lumiere tres forte deborde pendant le transfert et laisse une colonne claire au-dessus et au-dessous "
    "(phares, soleil). x 1 = valeur mesuree, souvent discrete.", "camera CCD (3 CCD, camescope)")
add(G, "Protection contre l'eblouissement (0 = profil, 1 = aucune)", "ccd_antiblooming", 1, "1 (aucune)", CCD3, {},
    "Quand un photosite deborde, les electrons coulent dans ses voisins verticaux. 1 = aucune protection : les "
    "points tres lumineux s'etirent verticalement. Plus grand = mieux protege.", "camera CCD")
add(G, "Perte au transfert vertical (-1 = profil)", "ccd_cti_v", 0.003, "0,003", CCD3, {},
    "A chaque transfert, un peu de charge reste en arriere : trainees vers le bas, plus fortes en haut... "
    "selon le sens de lecture. CCD vieilli ou irradie.", "camera CCD")
add(G, "Perte au transfert horizontal (-1 = profil)", "ccd_cti_h", 0.003, "0,003", CCD3, {},
    "Meme chose horizontalement : trainees vers la droite, plus fortes loin de la sortie.", "camera CCD")
add(G, "Colonnes bloquees", "ccd_blocked_columns", 10, "10", CCD3, {},
    "Defaut de transfert : la charge s'arrete a un point de la colonne ; tout ce qui est au-dessus est perdu.",
    "camera CCD")
add(G, "Lecture entrelacee", "interlace", 1, "Par trames", CCD3, {},
    "Lecture par trames (video entrelacee) : deux lignes voisines sont additionnees, la resolution verticale baisse. "
    "« Profil » = comme la camera.", "camera CCD")
add(G, "Rouge decale en X (px)", "registration_r_dx", 2, "2", CCD3, {},
    "Sur une camera a 3 capteurs, le capteur rouge est mal aligne : franges rouges / cyan sur les bords.",
    "camera 3 CCD")
add(G, "Rouge decale en Y (px)", "registration_r_dy", 2, "2", CCD3, {}, "Idem verticalement.", "camera 3 CCD")
add(G, "Bleu decale en X (px)", "registration_b_dx", -2, "-2", CCD3, {}, "Capteur bleu mal aligne : franges bleues / jaunes.",
    "camera 3 CCD")
add(G, "Bleu decale en Y (px)", "registration_b_dy", -2, "-2", CCD3, {}, "Idem verticalement.", "camera 3 CCD")

G = "Camescope"
add(G, "Bande passante des couleurs (MHz)", "cmyg_chroma_bw_mhz", 0.3, "0,3 (au lieu de 1)", CAM, {},
    "Le camescope traite les couleurs avec moins de definition que la luminance : couleurs qui bavent sur les "
    "bords. Plus bas = plus baveux.", "camescope")
add(G, "Couleur verticale moyennee (2H)", "cmyg_vertical_2h", 1, "Coche", CAM, {},
    "Les couleurs sont aussi moyennees entre lignes : bavure verticale en plus.", "camescope")
add(G, "Niveau de couleur x", "cmyg_chroma_gain", 2, "x 2", CAM, {},
    "Saturation du camescope.", "camescope")
add(G, "Ecart entre trames (-1 = profil)", "cmyg_field_flicker", 0.15, "0,15", CAM, {},
    "Les deux trames n'ont pas tout a fait la meme reponse : scintillement a 25 Hz en video, lignes alternees sur "
    "une image.", "camescope")
add(G, "Ecart entre lignes (-1 = profil)", "cmyg_line_crawl", 0.2, "0,2", CAM, {},
    "Les lignes n'ont pas toutes la meme reponse selon leurs filtres de couleur : motif de lignes qui « rampe » "
    "sur les couleurs saturees.", "camescope")

G = "Traitement"
add(G, "Dematricage", "demosaic", 2, "Plus proche", C700D, dict(olpf_h_px=0, olpf_v_px=0),
    "Reconstruction des couleurs a partir de la mosaique de filtres. Plus proche : bords en escalier et fausses "
    "couleurs ; bilineaire : doux ; Malvar : le meilleur.", "appareil photo (mosaique de Bayer)")
add(G, "Balance des blancs : rouge x", "wb_red", 1.6, "x 1,6", A7S, {},
    "Corrige ou fausse la balance : plus de rouge (chaud) ou moins (froid).")
add(G, "Balance des blancs : bleu x", "wb_blue", 1.6, "x 1,6", A7S, {}, "Plus ou moins de bleu.")
add(G, "Ecretage des hautes lumieres", "clip_highlights", 0, "Coupe", A7S, dict(ev=2),
    "Coupe les hautes lumieres proprement en blanc. Sans : les blancs satures prennent une teinte (souvent magenta), "
    "comme un fichier brut mal developpe.")
add(G, "Couleur spectrale (vraies courbes)", "spectral", 0, "Coupe", A7S, {},
    "Avec : la couleur passe par les courbes de sensibilite mesurees du capteur (rendu propre a chaque marque). "
    "Sans : simple matrice. La difference touche surtout les couleurs saturees.", "camera avec courbes mesurees")
add(G, "Erreur de niveau noir (DN)", "isp_black_offset_dn", 150, "150", A7S, {},
    "Le traitement retire un mauvais niveau noir. Positif : noirs ecrases ; negatif : noirs laiteux et teintes.")

G = "Etage video"
add(G, "Etage video (coude, contours)", "dsp_mode", 1, "Actif", A7S, {},
    "Le traitement d'une camera video : coude dans les hautes lumieres, accentuation des contours (halos), blancs "
    "ecretes. « Profil » : actif sur les cameras video (3 CCD, camescope), coupe sur les appareils photo. "
    "Les curseurs suivants n'agissent que si l'etage est actif.")
DSP = dict(dsp_mode=1)
add(G, "Point de coude (1 = 100 %)", "dsp_knee_point", 0.5, "0,5", A7S, dict(DSP, ev=1),
    "Au-dessus de ce niveau, les hautes lumieres sont compressees (aspect « video »).", "etage video actif")
add(G, "Pente au-dessus du coude", "dsp_knee_slope", 0.05, "0,05", A7S, dict(DSP, ev=1),
    "Force de la compression au-dessus du coude : petite = hautes lumieres tres tassees.", "etage video actif")
add(G, "Ecretage du blanc", "dsp_white_clip", 0.9, "0,9", A7S, dict(DSP, ev=1),
    "Niveau maximal du signal : au-dessus, tout est blanc.", "etage video actif")
add(G, "Accentuation horizontale", "dsp_detail_h", 2.5, "2,5", A7S, DSP,
    "Accentuation des contours horizontaux : halos clairs et sombres le long des bords verticaux.",
    "etage video actif")
add(G, "Accentuation verticale", "dsp_detail_v", 2.0, "2", A7S, DSP,
    "Meme chose verticalement (sur les bords horizontaux).", "etage video actif")
add(G, "Seuil des details", "dsp_detail_coring", 0.0, "0 (au lieu de 0,01)", A7S, dict(DSP, iso=6400, dsp_detail_h=2, dsp_detail_v=2),
    "Les tres petits details (le grain) ne sont pas accentues. A 0, le grain est accentue lui aussi.",
    "etage video actif, accentuation > 0")
add(G, "Amplitude maximale des halos", "dsp_detail_limit", 0.4, "0,4 (au lieu de 0,12)", A7S, dict(DSP, dsp_detail_h=2.5),
    "Limite la taille des halos sur les bords tres contrastes.", "etage video actif, accentuation > 0")
add(G, "Couleurs reduites dans les blancs", "dsp_highlight_chroma", 0, "0 (au lieu de 0,8)", A7S, dict(DSP, ev=2),
    "Les cameras video desaturent les tres hautes lumieres. 0 = desaturees, 1 = couleurs gardees.",
    "etage video actif")

G = "Effet CameraSim Analogique"
PAL = dict(analog_standard=1)
VHS = dict(analog_standard=1, analog_tape=1)
add(G, "Signal", "analog_standard", 1, "PAL", CAM, {},
    "Effet a part, a placer apres CameraSim (il marche aussi sur n'importe quelle image). Fait passer l'image par "
    "un signal video analogique PAL ou NTSC (sortie de la camera, cable). Couleurs moins definies, petit bruit. "
    "Rien d'autre dans cet effet n'agit sur « Aucun ». Par defaut : PAL et VHS.")
add(G, "Cadrage dans l'image 4:3", "analog_anamorphic", 1, "Anamorphose", CAM, VHS,
    "Le signal PAL / NTSC est en 4:3. « Bandes noires » : une image 16:9 est placee avec des bandes noires, et les "
    "defauts du bas de la trame (commutation des tetes) tombent dans la bande noire, donc hors de l'image. "
    "« Anamorphose » : l'image remplit toute la trame (comme les cameras 16:9 de l'epoque) ; la commutation des "
    "tetes devient visible en bas.", "signal PAL ou NTSC")
add(G, "Format de bande", "analog_tape", 1, "VHS", CAM, PAL,
    "Enregistrement sur bande : flou, bruit, couleurs baveuses et decalees, tremblement des lignes, commutation des "
    "tetes en bas de l'image. Chaque format a ses valeurs mesurees.", "signal PAL ou NTSC")
add(G, "Liaison composite (sous-porteuse)", "analog_composite", 0, "Decoche (S-video)", CAM, PAL,
    "Composite : luminance et couleur dans un seul fil. Motifs de couleur sur les details fins (« cross-color ») "
    "et points qui rampent sur les bords colores. Decoche : liaison separee, plus propre.", "signal PAL ou NTSC")
add(G, "Decodeur en peigne", "analog_comb", 1, "Coche", CAM, PAL,
    "Meilleur decodeur composite : plus net et moins de motifs, mais des points sur les bords de couleur horizontaux.",
    "liaison composite")
add(G, "Bruit de la liaison (dB)", "analog_link_snr_db", 28, "28 dB (au lieu de 55)", CAM, PAL,
    "Qualite du cable / de la transmission. Plus bas = plus de neige.", "signal PAL ou NTSC")
add(G, "Erreur de teinte (NTSC, degres)", "analog_hue_deg", 25, "25", CAM, dict(analog_standard=2),
    "Le NTSC tourne les couleurs si la phase est fausse (« Never The Same Color »). Le PAL corrige tout seul.",
    "NTSC composite")
add(G, "Bande passante luminance (MHz, -1 = bande)", "analog_luma_bw_mhz", 1.2, "1,2", CAM, VHS,
    "Definition de la bande. VHS : environ 3 MHz.", "une bande est choisie")
add(G, "Bande passante couleur (MHz, -1 = bande)", "analog_chroma_bw_mhz", 0.15, "0,15", CAM, VHS,
    "Definition des couleurs sur la bande.", "une bande est choisie")
add(G, "Bruit de la bande (dB, -1 = bande)", "analog_snr_db", 30, "30", CAM, VHS,
    "Bruit de la luminance sur la bande : grain horizontal.", "une bande est choisie")
add(G, "Bruit de la couleur (dB, -1 = bande)", "analog_chroma_snr_db", 22, "22", CAM, VHS,
    "Bruit des couleurs : taches colorees qui bougent.", "une bande est choisie")
add(G, "Retard de la couleur (us, -1 = bande)", "analog_chroma_delay_us", 2.5, "2,5", CAM, VHS,
    "Les couleurs sont decalees vers la droite par rapport a l'image.", "une bande est choisie")
add(G, "Instabilite des lignes (us, -1 = bande)", "analog_jitter_us", 0.6, "0,6", CAM, VHS,
    "Les lignes tremblent horizontalement (bords verticaux ondules).", "une bande est choisie")
add(G, "Haut de l'image qui penche (us, -1 = bande)", "analog_skew_us", 8, "8", CAM, VHS,
    "Le haut de l'image est tire sur le cote (tension de bande).", "une bande est choisie")
VHSA = dict(VHS, analog_anamorphic=1)
add(G, "Lignes de commutation des tetes (-1 = bande)", "analog_head_switch_lines", 25, "25", CAM, VHSA,
    "Hauteur de la bande perturbee en bas de l'image (exemple en anamorphose).", "une bande est choisie, et image "
    "en anamorphose ou en 4:3")
add(G, "Decalage de commutation (us, -1 = bande)", "analog_head_switch_us", 20, "20", CAM, VHSA,
    "Amplitude du decalage dans cette bande du bas.", "une bande est choisie, et image en anamorphose ou en 4:3")
add(G, "Pertes de signal par image", "analog_dropouts", 120, "120", CAM, VHS,
    "Manques d'oxyde sur la bande : petits traits horizontaux blancs ou masques.", "une bande est choisie")
add(G, "Compensateur de pertes", "analog_dropout_comp", 0, "Decoche", CAM, dict(VHS, analog_dropouts=40),
    "Coche : chaque perte est remplacee par la ligne d'avant (traits discrets). Decoche : traits blancs et bruites.",
    "pertes > 0")
add(G, "Bande mal calee", "analog_tracking", 0.8, "0,8", CAM, VHS,
    "Alignement des tetes : bande de bruit et lignes arrachees qui traversent l'image.", "une bande est choisie")
add(G, "Graine du hasard", "seed", 7, "7 (au lieu de 1)", CAM, dict(VHS, camera=0),
    "Change le tirage des defauts au hasard (pertes, tremblements, bruit) sans changer leur force.",
    "signal PAL ou NTSC")
add(G, "Copies successives", "analog_generations", 3, "3", CAM, VHS,
    "Copie de copie : tous les defauts de la bande s'accumulent.", "une bande est choisie")
add(G, "Accentuation du magnetoscope", "analog_sharpness", 2, "2", CAM, VHS,
    "Reglage « nettete » du magnetoscope : contours soulignes et bruit accentue.", "une bande est choisie")
add(G, "Calcul (effet analogique)", "gpu", 0, "Processeur", CAM, dict(VHS, gpu=1),
    "Ou se fait le signal analogique : sur la carte graphique NVIDIA (environ six fois plus vite) ou sur le "
    "processeur. Memes defauts tires au hasard, meme image (ecart de l'ordre du cent-millionieme).")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    img = scene()
    srgb = lambda a: preview_srgb(a.astype(np.float64))                        # noqa: E731
    cards, weak, cache, images = [], [], {}, {}

    def store(b64):
        k = "i%d" % len(images)
        images[k] = b64
        return k
    for s in S:
        base = dict(AE_DEFAULTS, profile=s["prof"], **s["base"])
        k = json.dumps(base, sort_keys=True)
        if k not in cache:
            u8 = srgb(run(img, **base))
            cache[k] = (u8, store(jpeg(u8)))
        before, before_id = cache[k]
        after = srgb(run(img, **dict(base, **{s["key"]: s["val"]})))
        d = np.abs(after.astype(float) - before.astype(float)).max(-1)
        mean = float(d.mean())
        gain = 255.0 / max(np.percentile(d, 99.7), 1.0)
        diff = np.clip(d * gain, 0, 255).astype(np.uint8)
        diff = np.repeat(diff[..., None], 3, -1)
        cards.append(dict({k2: s[k2] for k2 in ("group", "name", "shown", "text", "cond", "zoom")},
                          camera=PNAMES.get(s["prof"], str(s["prof"])), mean=round(mean, 2), gain=round(gain, 1),
                          before=before_id, after=store(jpeg(after)), diff=store(jpeg(diff, 60))))
        flag = "  <-- FAIBLE" if mean < 0.3 else ""
        print("%-55s ecart moyen %6.2f  max %3d%s" % (s["name"], mean, int(d.max()), flag))
        if mean < 0.3:
            weak.append(s["name"])
    scene_id = store(jpeg(srgb(img)))
    (OUT / "cards.json").write_text(json.dumps(dict(scene=scene_id, images=images, cards=cards)), encoding="utf-8")
    print("\n%d reglages, %d a effet faible : %s" % (len(cards), len(weak), ", ".join(weak)))


if __name__ == "__main__":
    main()
