"""Description des reglages pour l'interface (panneau After Effects).

Chaque reglage de pipeline.DEFAULTS apparait ici une seule fois, avec un libelle,
les familles de cameras concernees et, pour certains, une plage de "mutation"
(valeurs entre lesquelles le bouton Mutation tire au hasard).

kinds : "all", ou une liste parmi "cmos", "ccd", "cmyg" (camescope a un CCD), "tube".
"""
from .pipeline import DEFAULTS

SOLID = ["cmos", "ccd", "cmyg"]  # capteurs a semi-conducteur (tout sauf les tubes)
CCD = ["ccd", "cmyg"]            # tous les CCD

# (cle, libelle, kinds, options)   options : {"choices": [...], "mut": [bas, haut], "hint": "..."}
GROUPS = [
    ("Entree", [
        ("input_transfer", "Courbe de la source (PNG, video)", "all",
         {"choices": ["auto", "srgb", "bt709", "gamma22", "gamma24", "linear"],
          "hint": "auto = sRGB pour les images, BT.709 pour les videos. Sans effet sur les EXR"}),
        ("input_tone", "Courbe de tonalite a defaire", "all",
         {"choices": ["none", "smoothstep"], "hint": "smoothstep = courbe en S d'un traitement photo (Brooks et al. 2019)"}),
        ("input_exposure_ev", "Exposition de la source (diaphragmes)", "all",
         {"mut": [-2, 2], "hint": "0 = le blanc de l'ecran devient 1,0 en lumiere de scene"}),
        ("input_dequantize", "Effacer les marches 8 bits", "all", {}),
        ("output_format", "Format de sortie", "all",
         {"choices": ["exr", "png16"], "hint": "exr = ACEScg lineaire ; png16 = meme codage que la source"}),
    ]),
    ("Prise de vue", [
        ("iso", "ISO (gain)", SOLID, {"mut": [100, 25600], "hint": "Plus haut = moins de lumiere captee, donc plus de bruit"}),
        ("ev", "Exposition (diaphragmes)", "all", {"mut": [-3, 2]}),
        ("exposure_time_s", "Temps de pose (s)", "all", {"hint": "Agit sur la trainee CCD, le scintillement, le courant d'obscurite"}),
        ("temperature_c", "Temperature du capteur (C)", "all", {"mut": [20, 70]}),
        ("seed", "Numero de serie (graine)", "all", {"hint": "Fixe les defauts figes : pixels morts, rayures..."}),
        ("fps", "Cadence (images/s)", "all", {"hint": "Remplie automatiquement depuis le calque"}),
    ]),
    ("Obturateur", [
        ("readout_time_s", "Temps de lecture (s), 0 = global", SOLID, {"mut": [0, 0.04]}),
        ("shutter_direction", "Sens de lecture", SOLID, {"choices": ["top_to_bottom", "bottom_to_top"]}),
        ("flicker_hz", "Scintillement (Hz), 0 = aucun", "all", {"hint": "100 = eclairage sur secteur europeen"}),
        ("flicker_depth", "Profondeur du scintillement (0-1)", "all", {"mut": [0, 0.5]}),
        ("flicker_phase", "Phase du scintillement (rad)", "all", {}),
    ]),
    ("Capteur", [
        ("sensor_resolution", "Resolution de la simulation", "all",
         {"choices": ["image", "capteur"], "hint": "capteur = vraie grille de photosites (ex. 752 x 582 pour le camescope)"}),
        ("sensor_output", "Taille de sortie", "all",
         {"choices": ["image", "capteur"], "hint": "image = remise a la taille du calque ; capteur = taille de la grille"}),
        ("sensor_upscale", "Agrandissement vers la taille du calque", "all", {"choices": ["lanczos", "bilinear", "nearest"]}),
        ("video_mode", "Mode video de l'appareil photo", ["cmos"],
         {"choices": ["off", "1080p", "4k"], "hint": "Lecture partielle du capteur en video : sauts de lignes, regroupement, recadrage"}),
        ("video_readout", "Methode de lecture video", ["cmos"],
         {"choices": ["auto", "full", "skip3", "bin3x3", "vbin2"],
          "hint": "auto = celle de l'appareil ; skip3 = 1 ligne sur 3 (Canon) ; bin3x3 = 5D Mark III ; full = tout lire"}),
        ("olpf_h_px", "Filtre passe-bas horizontal (px)", SOLID, {"mut": [0, 2], "hint": "0 = pas de filtre (moire), 1 = normal"}),
        ("olpf_v_px", "Filtre passe-bas vertical (px)", SOLID, {"mut": [0, 2]}),
        ("shot_noise", "Bruit de photons", SOLID, {}),
        ("read_noise_model", "Bruit de lecture", SOLID, {"choices": ["tukey", "gauss", "off"]}),
        ("read_noise_gain", "Bruit de lecture x", SOLID, {"mut": [0.5, 8]}),
        ("row_noise_gain", "Bruit de ligne x", ["cmos"], {"mut": [0, 30]}),
        ("color_bias_gain", "Biais du noir par canal x", ["cmos"], {"mut": [0, 20]}),
        ("prnu_sigma", "Non-uniformite de sensibilite", SOLID, {"mut": [0, 0.08]}),
        ("dsnu_rel_sigma", "Non-uniformite d'obscurite", SOLID, {}),
        ("dark_current_e_per_s", "Courant d'obscurite (e-/s a 20 C)", SOLID, {"mut": [0, 3000]}),
        ("hot_pixel_fraction", "Part de pixels chauds", SOLID, {"mut": [0, 0.002]}),
        ("hot_pixel_gain", "Force des pixels chauds x", SOLID, {}),
        ("full_well_gain", "Capacite du puits x", SOLID, {"mut": [0.1, 1]}),
        ("clip_full_well", "Saturation du puits", SOLID, {}),
    ]),
    ("Defauts fixes", [
        ("column_fpn_dn", "Decalage des colonnes (DN)", SOLID, {"mut": [0, 40]}),
        ("column_gain_sigma", "Ecart de gain des colonnes", SOLID, {"mut": [0, 0.1]}),
        ("dead_pixel_fraction", "Part de pixels morts", SOLID, {"mut": [0, 0.01]}),
        ("stuck_pixel_fraction", "Part de pixels colles", SOLID, {"mut": [0, 0.003]}),
        ("dead_columns", "Colonnes mortes", SOLID, {"mut": [0, 12]}),
        ("dead_rows", "Lignes mortes", SOLID, {"mut": [0, 8]}),
    ]),
    ("Pannes", [
        ("row_address_stuck", "Bit d'adresse de ligne colle (ex. 6:0)", SOLID, {}),
        ("col_address_stuck", "Bit d'adresse de colonne colle", SOLID, {}),
        ("row_address_error_rate", "Lignes lues au mauvais endroit (0-1)", SOLID, {"mut": [0, 0.08]}),
        ("col_address_error_rate", "Colonnes lues au mauvais endroit (0-1)", SOLID, {"mut": [0, 0.08]}),
        ("interference_dn", "Parasite d'alimentation (DN)", SOLID, {"mut": [0, 200]}),
        ("interference_period_px", "Periode du parasite (px)", SOLID, {"mut": [40, 4000]}),
        ("bitslip", "Glissement de bits (liaison serie)", SOLID, {}),
        ("line_shift_rate", "Lignes qui glissent (0-1)", SOLID, {"mut": [0, 0.1]}),
        ("line_shift_max_px", "Glissement maximal (px)", SOLID, {}),
        ("line_shift_persist", "Synchro perdue (glissements cumules)", SOLID, {}),
        ("frame_tear_rate", "Images dechirees (0-1)", SOLID, {"mut": [0, 0.5]}),
    ]),
    ("Convertisseur", [
        ("quantize", "Numerisation", SOLID, {}),
        ("adc_bits", "Profondeur (bits), vide = profil", SOLID, {"mut": [3, 12]}),
        ("adc_dnl_lsb", "Marches irregulieres (LSB)", SOLID, {"mut": [0, 1.2]}),
        ("adc_inl_lsb", "Courbure (LSB)", SOLID, {"mut": [0, 400]}),
        ("adc_stuck_bits", "Bits colles (ex. 10:1,8:0)", SOLID, {}),
        ("adc_sparkle_rate", "Bits inverses au hasard (0-1)", SOLID, {"mut": [0, 0.02]}),
    ]),
    ("CCD et 3 capteurs", [
        ("ccd_smear_gain", "Trainee verticale x", CCD, {"mut": [0, 200]}),
        ("ccd_antiblooming", "Protection contre l'eblouissement (1 = aucune)", CCD, {"mut": [1, 300]}),
        ("ccd_cti_v", "Perte au transfert vertical", CCD, {"mut": [0, 0.002]}),
        ("ccd_cti_h", "Perte au transfert horizontal", CCD, {"mut": [0, 0.002]}),
        ("ccd_blocked_columns", "Colonnes bloquees", CCD, {"mut": [0, 10]}),
        ("interlace", "Lecture entrelacee", ["ccd"], {"choices": ["", "off", "fields"]}),
        ("registration_r_dx", "Rouge decale en X (px)", ["ccd", "tube"], {"mut": [-4, 4]}),
        ("registration_r_dy", "Rouge decale en Y (px)", ["ccd", "tube"], {"mut": [-4, 4]}),
        ("registration_b_dx", "Bleu decale en X (px)", ["ccd", "tube"], {"mut": [-4, 4]}),
        ("registration_b_dy", "Bleu decale en Y (px)", ["ccd", "tube"], {"mut": [-4, 4]}),
    ]),
    ("Tube", [
        ("tube_beam_nA", "Courant du faisceau (nA)", ["tube"], {"mut": [100, 1200], "hint": "Bas = trainees de comete"}),
        ("tube_bias_nA", "Lumiere de polarisation (nA)", ["tube"], {"mut": [0, 10], "hint": "0 = plus de remanence"}),
        ("tube_dark_nA", "Courant d'obscurite (nA)", ["tube"], {}),
        ("tube_gamma", "Gamma de la couche", ["tube"], {"mut": [0.5, 1]}),
        ("tube_photo_lag", "Remanence de la couche (0-1)", ["tube"], {"mut": [0, 0.7]}),
        ("tube_photo_lag_fields", "Duree de la remanence (trames)", ["tube"], {"mut": [2, 40]}),
        ("tube_noise_nA", "Bruit du preamplificateur (nA)", ["tube"], {"mut": [0, 8]}),
        ("tube_spot_px", "Tache du faisceau (px)", ["tube"], {"mut": [0, 6]}),
        ("tube_bloom_px", "Etalement des hautes lumieres (px)", ["tube"], {"mut": [0, 20]}),
        ("tube_shading", "Ombrage vers les bords (0-1)", ["tube"], {"mut": [0, 0.6]}),
        ("tube_burn_gain", "Marquage de la cible (0-1)", ["tube"], {"mut": [0, 0.6]}),
        ("tube_burn_seconds", "Vitesse du marquage (s)", ["tube"], {}),
        ("tube_gamma_correct", "Correction du gamma par la camera", ["tube"], {}),
        ("tube_fields_per_frame", "Trames par image", ["tube"], {}),
        ("tube_interlace", "Entrelacement", ["tube"], {}),
    ]),
    ("Camescope", [
        ("cmyg_chroma_bw_mhz", "Bande passante des couleurs (MHz)", ["cmyg"],
         {"mut": [0.3, 4], "hint": "Bas = couleurs qui bavent horizontalement"}),
        ("cmyg_chroma_vertical", "Couleur verticale", ["cmyg"],
         {"choices": ["1H", "2H"], "hint": "1H = reprise de la ligne precedente (couleurs decalees)"}),
        ("cmyg_chroma_gain", "Niveau de couleur x", ["cmyg"], {"mut": [0, 2]}),
        ("cmyg_field_flicker", "Ecart entre trames (fiche : 0,02 max)", ["cmyg"], {"mut": [0, 0.15]}),
        ("cmyg_line_crawl", "Ecart entre lignes (fiche : 0,03 max)", ["cmyg"], {"mut": [0, 0.2]}),
        ("ccd_lag", "Remanence (0-1, fiche : 0,005 max)", CCD, {"mut": [0, 0.5]}),
        ("sensor_shading", "Ombrage vers les coins (0-1, fiche : 0,20 max)", SOLID, {"mut": [0, 0.6]}),
    ]),
    ("Automatismes", [
        ("auto_exposure", "Exposition automatique", SOLID, {"hint": "Diaphragme puis gain ; images traitees dans l'ordre"}),
        ("ae_metering", "Mesure", SOLID, {"choices": ["center", "average", "spot", "peak"],
                                          "hint": "center = ponderee centrale ; peak = protege les hautes lumieres"}),
        ("ae_target", "Luminosite visee (0,18 = gris moyen)", SOLID, {"mut": [0.08, 0.4]}),
        ("ae_speed_s", "Temps de reaction (s)", SOLID, {"mut": [0.05, 3.0]}),
        ("ae_damping", "Amortissement (1 = doux, 0,2 = pompage)", SOLID, {"mut": [0.1, 1.2]}),
        ("ae_deadband_ev", "Ecart tolere (diaphragmes)", SOLID, {"mut": [0, 1.0]}),
        ("ae_max_rate_ev_s", "Vitesse max. du diaphragme (diaph./s)", SOLID, {"mut": [0.5, 10]}),
        ("ae_iris_min_ev", "Butee du diaphragme : fermeture (diaph.)", SOLID, {}),
        ("ae_iris_max_ev", "Butee du diaphragme : ouverture (diaph.)", SOLID, {"mut": [0, 4]}),
        ("auto_gain", "Gain automatique (AGC)", SOLID, {}),
        ("ae_gain_max_db", "Gain maximal (dB)", SOLID, {"mut": [0, 30]}),
        ("auto_wb", "Balance des blancs automatique", SOLID, {}),
        ("awb_method", "Methode", SOLID, {"choices": ["grayworld", "whitepatch"],
                                          "hint": "grayworld = moyenne des couleurs ; whitepatch = points les plus clairs"}),
        ("awb_speed_s", "Temps de reaction de la balance (s)", SOLID, {"mut": [0.2, 6.0]}),
        ("awb_strength", "Part de la correction (1 = complete)", SOLID, {"mut": [0.3, 1.0]}),
        ("awb_deadband", "Ecart tolere de la balance", SOLID, {"mut": [0, 0.2]}),
    ]),
    ("Analogique", [
        ("analog_standard", "Signal analogique", "all", {"choices": ["off", "pal", "ntsc"]}),
        ("analog_framing", "Cadrage dans la trame 4:3", "all", {"choices": ["letterbox", "anamorphic"],
                                                                "hint": "letterbox = bandes noires ; anamorphic = l'image remplit la trame (16:9 comprime)"}),
        ("analog_tape", "Bande", "all", {"choices": ["none", "vhs", "vhs_lp", "svhs", "video8", "hi8"]}),
        ("analog_composite", "Liaison composite (sous-porteuse)", "all", {"hint": "Chenillement de points, irisations"}),
        ("analog_decoder", "Separation luminance / couleur", "all", {"choices": ["notch", "comb"],
                                                                     "hint": "notch = televiseur simple ; comb = peigne, plus propre"}),
        ("analog_link_snr_db", "Bruit de la liaison (dB, bas = neige)", "all", {"mut": [25, 60]}),
        ("analog_hue_deg", "Erreur de teinte (NTSC, degres)", "all", {"mut": [-30, 30]}),
        ("analog_luma_bw_mhz", "Bande passante luminance (MHz)", "all", {"mut": [1.0, 5.0]}),
        ("analog_chroma_bw_mhz", "Bande passante couleur (MHz)", "all", {"mut": [0.1, 0.6]}),
        ("analog_snr_db", "Bruit de la bande (dB)", "all", {"mut": [25, 46]}),
        ("analog_chroma_snr_db", "Bruit de la couleur (dB)", "all", {"mut": [20, 40]}),
        ("analog_chroma_delay_us", "Retard de la couleur (us)", "all", {"mut": [0, 1.0]}),
        ("analog_jitter_us", "Instabilite des lignes (us)", "all", {"mut": [0, 0.5]}),
        ("analog_skew_us", "Haut de l'image qui penche (us)", "all", {"mut": [0, 3.0]}),
        ("analog_head_switch_lines", "Lignes de commutation des tetes", "all", {"mut": [0, 12]}),
        ("analog_head_switch_us", "Decalage de commutation (us)", "all", {"mut": [0, 8]}),
        ("analog_dropouts", "Pertes de signal par image", "all", {"mut": [0, 20]}),
        ("analog_dropout_comp", "Compensateur de pertes", "all", {}),
        ("analog_tracking", "Bande mal calee (0-1)", "all", {"mut": [0, 1]}),
        ("analog_generations", "Copies successives", "all", {"mut": [1, 5]}),
        ("analog_sharpness", "Accentuation du magnetoscope", "all", {"mut": [0, 1.5]}),
    ]),
    ("Enregistrement", [
        ("rec_format", "Format d'enregistrement", "all",
         {"choices": ["off", "dv_pal", "dv_ntsc", "dvd", "hdv", "mjpeg", "h264_phone", "h264_dslr", "3gp"],
          "hint": "Vrai codec (ffmpeg). Calcule sur toute la sequence ; pas de couche alpha en sortie"}),
        ("rec_bitrate_scale", "Debit x (bas = blocs)", "all", {"mut": [0.05, 1.0]}),
        ("rec_gop", "Groupe d'images (vide = format)", "all", {"mut": [1, 60]}),
        ("rec_errors", "Octets abimes (ex. 0.0001)", "all", {"mut": [0, 0.0005], "hint": "Vraies erreurs de flux, decodees par un vrai decodeur"}),
        ("rec_drop", "Paquets perdus (0-1)", "all", {"mut": [0, 0.1]}),
        ("rec_native_fps", "Cadence du format (3GP : 15 im/s)", "all", {}),
    ]),
    ("Etage video", [
        ("dsp_enabled", "Etage video (coude, contours)", "all",
         {"choices": ["profil", "on", "off"], "hint": "Actif par defaut pour le camescope"}),
        ("dsp_knee_point", "Point de coude (1 = 100 %)", "all", {"mut": [0.6, 1.0]}),
        ("dsp_knee_slope", "Pente au-dessus du coude", "all", {"mut": [0.05, 1.0]}),
        ("dsp_white_clip", "Ecretage du blanc (1 = 100 %)", "all", {"mut": [0.9, 1.1]}),
        ("dsp_detail_h", "Accentuation horizontale des contours", "all", {"mut": [0, 2.5], "hint": "Halos clairs et sombres autour des bords"}),
        ("dsp_detail_v", "Accentuation verticale des contours", "all", {"mut": [0, 2.0]}),
        ("dsp_detail_coring", "Seuil des details (evite d'accentuer le bruit)", "all", {"mut": [0, 0.05]}),
        ("dsp_detail_limit", "Amplitude maximale des halos", "all", {"mut": [0.02, 0.4]}),
        ("dsp_highlight_chroma", "Couleurs reduites dans les blancs (0-1)", "all", {"mut": [0, 1]}),
    ]),
    ("Traitement", [
        ("wb_red", "Balance des blancs : rouge x", SOLID, {"mut": [0.5, 2]}),
        ("wb_blue", "Balance des blancs : bleu x", SOLID, {"mut": [0.5, 2]}),
        ("spectral", "Couleur spectrale (vraies courbes du capteur)", ["cmos"],
         {"choices": ["profil", "on", "off"], "hint": "off = simple matrice ; 70D et 700D : courbes d'appareils voisins"}),
        ("demosaic", "Dematricage", ["cmos"], {"choices": ["malvar", "bilinear", "nearest"]}),
        ("clip_highlights", "Ecretage des hautes lumieres", SOLID, {}),
        ("isp_bayer_dx", "Motif de Bayer decale en X (0 ou 1)", ["cmos"], {}),
        ("isp_bayer_dy", "Motif de Bayer decale en Y (0 ou 1)", ["cmos"], {}),
        ("isp_black_offset_dn", "Erreur de niveau noir (DN)", SOLID, {"mut": [-150, 150]}),
        ("isp_remove_color_bias", "Biais par canal corrige par la camera", ["cmos"], {}),
    ]),
]


def item_type(key, options):
    d = DEFAULTS[key]
    if "choices" in options:
        return "choice"
    if isinstance(d, bool):
        return "bool"
    if isinstance(d, str):
        return "text"
    return "number"


def build_schema(profiles):
    """Structure exportee en JSON pour le panneau. profiles : liste de {key, name, kind}."""
    groups = []
    for title, items in GROUPS:
        out = []
        for key, label, kinds, opt in items:
            d = DEFAULTS[key]
            out.append({
                "key": key, "label": label, "type": item_type(key, opt),
                "default": d, "kinds": ["cmos", "ccd", "cmyg", "tube"] if kinds == "all" else list(kinds),
                "choices": opt.get("choices"), "mut": opt.get("mut"), "hint": opt.get("hint", ""),
                "integer": isinstance(d, int) and not isinstance(d, bool),
            })
        groups.append({"title": title, "items": out})
    return {"profiles": profiles, "groups": groups}


def all_keys():
    return [k for _, items in GROUPS for k, _, _, _ in items]
