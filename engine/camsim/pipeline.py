"""Chaine complete : image de scene (ACEScg lineaire) -> capteur -> traitement -> image ACEScg."""
import json
from pathlib import Path

import numpy as np

from . import analog, camdsp, resolution, video
from .auto import Automatics
from .color import CameraColor
from .filters import optical_low_pass, subpixel_shift
from .isp import develop
from .sensor import Sensor
from .shutter import flicker_gain, half_frame_later, rolling_shutter
from .tube import TubeCamera

PROFILE_DIR = Path(__file__).resolve().parent / "profiles"

DEFAULTS = {
    # Prise de vue
    "iso": 800,                  # sensibilite : plus haut = moins d'electrons pour la meme image
    "ev": 0.0,                   # correction d'exposition, en diaphragmes
    "exposure_time_s": 1.0 / 50, # temps de pose (agit sur le courant d'obscurite et la trainee CCD)
    "temperature_c": 30.0,       # temperature du capteur
    "seed": 1,                   # "numero de serie" : fixe les defauts figes
    # Obturateur
    "fps": 25.0,                 # cadence du plan
    "readout_time_s": 0.0,       # temps de lecture du capteur ; 0 = obturateur global
    "shutter_direction": "top_to_bottom",
    "flicker_hz": 0.0,           # scintillement de l'eclairage (100 = secteur europeen) ; 0 = aucun
    "flicker_depth": 0.0,        # profondeur du scintillement, de 0 a 1
    "flicker_phase": 0.0,
    # Filtre passe-bas colle sur le capteur : ecart des 4 points, en pixels
    "olpf_h_px": 1.0,            # 1 = annule le moire (valeur theorique) ; 0 = pas de filtre
    "olpf_v_px": 1.0,
    # Camera a trois capteurs ou trois tubes : defaut d'alignement du rouge et du bleu, en pixels
    "registration_r_dx": 0.0,
    "registration_r_dy": 0.0,
    "registration_b_dx": 0.0,
    "registration_b_dy": 0.0,
    # Bruits (domaine capteur)
    "shot_noise": True,
    "read_noise_model": "tukey", # "tukey" (ELD), "gauss" ou "off"
    "read_noise_gain": 1.0,      # 1 = valeur calibree ; au-dela = hors limites
    "row_noise_gain": 1.0,
    "color_bias_gain": 1.0,
    "prnu_sigma": None,          # None = valeur du profil
    "dsnu_rel_sigma": None,
    "dark_current_e_per_s": None,
    "hot_pixel_fraction": None,
    "hot_pixel_gain": 1000.0,
    "full_well_gain": 1.0,
    "clip_full_well": True,
    # CCD
    "ccd_smear_gain": 1.0,       # 1 = trainee verticale de la fiche technique
    "ccd_antiblooming": None,    # protection contre l'eblouissement ; 1 = aucune
    "ccd_cti_v": None,           # part de charge perdue a chaque transfert vertical
    "ccd_cti_h": None,           # idem, transfert horizontal
    "ccd_blocked_columns": 0,    # nombre de colonnes bloquees
    "interlace": None,           # "fields" = lecture entrelacee par addition de deux lignes ; "off"
    # Tube (vidicon, Plumbicon) ; None = valeur du profil
    "tube_beam_nA": None,        # courant du faisceau : limite des hautes lumieres
    "tube_bias_nA": None,        # lumiere de polarisation : reduit la remanence
    "tube_dark_nA": None,
    "tube_gamma": None,
    "tube_photo_lag": None,      # part de charge piegee par la couche
    "tube_photo_lag_fields": None,
    "tube_noise_nA": None,
    "tube_spot_px": None,        # taille de la tache du faisceau
    "tube_bloom_px": None,       # etalement des hautes lumieres non stabilisees
    "tube_shading": None,        # perte de signal vers les bords
    "tube_burn_gain": 0.0,       # marquage de la cible par une image fixe
    "tube_burn_seconds": 30.0,
    "tube_gamma_correct": True,  # la camera corrige le gamma du tube
    "tube_fields_per_frame": 2,
    "tube_interlace": True,
    # Amplis de colonnes (bruit de colonnes fixe)
    "column_fpn_dn": 0.0,        # ecart-type du decalage par colonne, en DN
    "column_gain_sigma": 0.0,    # ecart relatif de gain par colonne
    # Defauts de fabrication de la matrice
    "dead_pixel_fraction": 0.0,  # pixels morts (noirs)
    "stuck_pixel_fraction": 0.0, # pixels colles (blancs)
    "dead_columns": 0,           # nombre de colonnes mortes
    "dead_rows": 0,              # nombre de lignes mortes
    # Pannes d'adressage
    "row_address_stuck": "",     # ex. "5:0" : le bit 5 de l'adresse de ligne reste a 0
    "col_address_stuck": "",
    "row_address_error_rate": 0.0,   # probabilite qu'une ligne soit lue a une mauvaise adresse
    "col_address_error_rate": 0.0,
    # Parasite de l'alimentation
    "interference_dn": 0.0,          # amplitude, en DN
    "interference_period_px": 517.3, # periode le long de la lecture, en pixels
    # Convertisseur
    "quantize": True,
    "adc_bits": None,            # None = profondeur du profil
    "adc_dnl_lsb": 0.0,          # irregularite des marches (codes manquants au-dela de ~0.5)
    "adc_inl_lsb": 0.0,          # courbure de la reponse, en LSB
    "adc_stuck_bits": "",        # ex. "9:1,3:0" : bit 9 colle a 1, bit 3 colle a 0
    "adc_sparkle_rate": 0.0,     # probabilite d'un bit inverse par pixel
    # Liaison serie et memoire
    "bitslip": 0,                # glissement du flux, en bits
    "line_shift_rate": 0.0,      # probabilite qu'une ligne glisse
    "line_shift_max_px": 8,
    "line_shift_persist": False, # True = la synchro est perdue, les glissements s'accumulent
    "frame_tear_rate": 0.0,      # probabilite qu'une image soit dechiree
    # Traitement dans la camera
    "isp_bayer_dx": 0,           # 1 = le traitement se trompe de motif de Bayer (decalage horizontal)
    "isp_bayer_dy": 0,
    "isp_black_offset_dn": 0.0,  # erreur sur le niveau noir soustrait
    "isp_remove_color_bias": False,
    "wb_red": 1.0,               # 1 = balance des blancs juste
    "wb_blue": 1.0,
    "clip_highlights": True,
    "demosaic": "malvar",        # "nearest", "bilinear" ou "malvar"
    # Camescope a mosaique complementaire (un seul CCD)
    "cmyg_chroma_bw_mhz": 1.0,   # bande passante des differences de couleur (fiche Sony ICX409AK : filtre a -3 dB a 1 MHz)
    "cmyg_chroma_vertical": "1H",  # "1H" = ligne precedente (retard 1H) ; "2H" = moyenne de deux lignes
    "cmyg_chroma_gain": 1.0,     # niveau de couleur
    # Entree : images deja traitees (PNG, TIFF, JPEG, videos) ; sans effet sur les EXR
    "input_transfer": "auto",    # "auto", "srgb", "bt709", "gamma22", "gamma24", "linear"
    "input_tone": "none",        # "none" ou "smoothstep" (courbe de tonalite inverse de Brooks et al. 2019)
    "input_exposure_ev": 0.0,    # 0 = le blanc de l'ecran vaut 1,0 en lumiere de scene
    "input_dequantize": True,    # tremblement d'un demi-pas de code : efface les marches 8 bits
    "output_format": "exr",      # "exr" (ACEScg lineaire) ou "png16" (meme codage que la source, 16 bits)
    # Resolution de la simulation
    "sensor_resolution": "image",  # "image" = un pixel par photosite ; "capteur" = vraie grille du capteur
    "sensor_output": "image",      # "image" = remise a la taille d'origine ; "capteur" = taille du capteur
    "sensor_upscale": "lanczos",   # agrandissement vers la taille d'origine : "lanczos", "bilinear", "nearest"
    # Defauts du capteur chiffres par les fiches ; None = valeur du profil
    "sensor_shading": None,        # perte de sensibilite vers les coins (0-1) ; fiche ICX409AK : 0,20 au plus
    "ccd_lag": None,               # remanence d'une image a l'autre (0-1) ; fiche ICX409AK : 0,005 au plus
    "cmyg_field_flicker": None,    # ecart de niveau de la trame B (fiche ICX409AK : 0,02 au plus)
    "cmyg_line_crawl": None,       # ecart entre lignes rouges et bleues (fiche ICX409AK : 0,03 au plus)
    # Etage video (coude, accentuation, couleurs dans les blancs) ; None = valeur du profil
    "dsp_enabled": "profil",       # "profil", "on" ou "off"
    "dsp_knee_point": None,        # niveau video (1 = 100 %) ou commence le coude
    "dsp_knee_slope": None,        # pente au-dessus du coude
    "dsp_white_clip": None,        # niveau d'ecretage du blanc
    "dsp_detail_h": None,          # accentuation horizontale des contours
    "dsp_detail_v": None,          # accentuation verticale
    "dsp_detail_coring": None,     # seuil sous lequel les details ne sont pas accentues
    "dsp_detail_limit": None,      # amplitude maximale des halos
    "dsp_highlight_chroma": None,  # reduction des couleurs dans les hautes lumieres (0-1)
    # Automatismes (valeurs courantes, pas des mesures)
    "auto_exposure": False,        # exposition automatique (diaphragme puis gain)
    "ae_metering": "center",       # "average", "center" (ponderee centrale), "spot", "peak" (hautes lumieres)
    "ae_target": 0.18,             # luminosite visee (gris moyen 18 %)
    "ae_speed_s": 0.4,             # temps de reaction (s)
    "ae_damping": 0.7,             # amortissement : 1 = sans depassement, 0,2 = pompage
    "ae_deadband_ev": 0.15,        # ecart tolere avant de reagir (diaphragmes)
    "ae_max_rate_ev_s": 4.0,       # vitesse maximale du diaphragme (diaphragmes par seconde)
    "ae_iris_min_ev": -6.0,        # butees du diaphragme, par rapport a l'exposition reglee
    "ae_iris_max_ev": 2.0,
    "auto_gain": True,             # gain automatique (AGC) quand le diaphragme est en butee
    "ae_gain_max_db": 18.0,        # gain maximal
    "auto_wb": False,              # balance des blancs automatique
    "awb_method": "grayworld",     # "grayworld" (moyenne) ou "whitepatch" (points les plus clairs)
    "awb_speed_s": 1.5,            # temps de reaction (s)
    "awb_strength": 1.0,           # part de la correction (1 = complete ; 0,7 = garde de la chaleur)
    "awb_deadband": 0.03,          # ecart tolere (log2 du gain)
    # Couleur spectrale (sensibilites mesurees du capteur)
    "spectral": "profil",          # "profil", "on" (spectres + vraies courbes du capteur) ou "off" (matrice)
    # Signal analogique : bande et liaison composite (image par image)
    "analog_standard": "off",      # "off", "pal" ou "ntsc"
    "analog_tape": "none",         # "none", "vhs", "vhs_lp", "svhs", "video8", "hi8"
    "analog_composite": True,      # liaison composite (couleur sur sous-porteuse) jusqu'a la capture
    "analog_decoder": "notch",     # separation luminance / couleur : "notch" (simple) ou "comb" (peigne)
    "analog_link_snr_db": 55.0,    # bruit de la liaison composite
    "analog_hue_deg": 0.0,         # erreur de teinte (NTSC seulement : le PAL la corrige)
    "analog_luma_bw_mhz": None,    # bande passante de la luminance sur la bande ; vide = format
    "analog_chroma_bw_mhz": None,  # bande passante de la couleur sur la bande
    "analog_snr_db": None,         # bruit de la luminance (dB)
    "analog_chroma_snr_db": None,  # bruit de la couleur (dB)
    "analog_chroma_delay_us": None,  # retard de la couleur sur la luminance (us)
    "analog_jitter_us": None,      # instabilite de la base de temps (us)
    "analog_skew_us": None,        # lignes du haut qui penchent (us)
    "analog_head_switch_lines": None,  # lignes touchees par la commutation des tetes
    "analog_head_switch_us": None,     # decalage de ces lignes (us)
    "analog_dropouts": 0.0,        # pertes de signal par image (moyenne)
    "analog_dropout_comp": True,   # compensateur de pertes (recopie la ligne precedente)
    "analog_tracking": 0.0,        # bande mal calee (0-1)
    "analog_generations": 1,       # nombre de passages sur bande (copies)
    "analog_sharpness": 0.0,       # accentuation du magnetoscope (halos)
    "analog_framing": "letterbox", # cadrage dans la trame 4:3 : "letterbox" (bandes noires) ou "anamorphic"
    # Enregistrement (codec reel par ffmpeg), applique par le lanceur sur toute la sequence
    "rec_format": "off",           # "off", "dv_pal", "dv_ntsc", "dvd", "hdv", "mjpeg", "h264_phone", "h264_dslr", "3gp"
    "rec_bitrate_scale": 1.0,      # debit x (plus bas = blocs plus visibles)
    "rec_gop": None,               # longueur des groupes d'images (formats MPEG / H.264) ; vide = celle du format
    "rec_errors": 0.0,             # part des octets du flux abimes (ex. 0,0001)
    "rec_drop": 0.0,               # part des paquets perdus (0-1)
    "rec_native_fps": True,        # cadence du format (3GP : 15 images/s) ; non = cadence de la sequence
    # Mode video des appareils photo (lecture partielle du capteur)
    "video_mode": "off",           # "off" (photo), "1080p" ou "4k" : modes du profil
    "video_readout": "auto",       # "auto" (celle de l'appareil), "full", "skip3", "bin3x3", "vbin2"
}


def load_profile(name):
    path = Path(name)
    if not path.suffix:
        path = PROFILE_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class CameraSim:
    def __init__(self, profile, **settings):
        self.profile = load_profile(profile) if isinstance(profile, (str, Path)) else profile
        unknown = set(settings) - set(DEFAULTS) - {"base_iso"}
        if unknown:
            raise ValueError("reglages inconnus : %s" % sorted(unknown))
        self.settings = {**DEFAULTS, **settings}
        self.kind = self.profile.get("sensor_type", "cmos")
        self.multi_plane = self.kind == "tube" or self.profile.get("cfa") == "3chip"
        # Couleur : sensibilites spectrales mesurees (si le profil en a et que c'est demande), sinon matrice.
        self.spectral = None
        mode = self.settings["spectral"]
        if mode not in ("profil", "on", "off"):
            raise ValueError("spectral inconnu : %r" % mode)
        ssf = self.profile.get("spectral_ssf")
        use = (mode == "on") or (mode == "profil" and ssf and self.profile.get("spectral_default", False))
        if use:
            if not ssf:
                raise ValueError("pas de sensibilites spectrales mesurees pour %s" % self.profile.get("name"))
            from .spectral import Spectral
            self.spectral = Spectral(ssf)
            self.color = CameraColor(self.spectral.xyz_to_cam)
        else:
            self.color = CameraColor(self.profile["xyz_d65_to_cam"])
        self.dsp = camdsp.dsp_params(self.settings, self.profile)
        self.auto = Automatics(self.settings)
        self.analog = analog.params(self.settings)
        self._ae_mult = 1.0
        self._wb_user = (float(self.settings["wb_red"]), float(self.settings["wb_blue"]))
        if self.kind == "tube":
            self.sensor = None
            self.tube = TubeCamera(self.profile, self.settings)
        else:
            self.sensor = Sensor(self.profile, self.settings)
            self.tube = None

    def scene_to_cam(self, scene_ap1):
        """Scene ACEScg -> reponse brute de la camera (spectrale si active, sinon matrice)."""
        x = np.asarray(scene_ap1, dtype=np.float64)
        return self.spectral.scene_to_cam(x) if self.spectral is not None else self.color.scene_to_cam(x)

    def describe(self):
        return self.tube.describe() if self.tube is not None else self.sensor.describe()

    def needs_neighbors(self):
        """Vrai si le reglage demande les images voisines (mouvement entre deux images)."""
        s = self.settings
        if float(s["readout_time_s"]) > 0:
            return True
        if self.tube is not None:
            return int(s["tube_fields_per_frame"]) >= 2
        return self.sensor.interlaced

    def shutter(self, cur, frame=0, prev=None, nxt=None, flow_next=None, flow_prev=None):
        """Obturateur deroulant et scintillement. cur peut contenir l'alpha en 4e canal."""
        s = self.settings
        out = rolling_shutter(cur, prev, nxt, fps=float(s["fps"]), readout_time_s=float(s["readout_time_s"]),
                              direction=s["shutter_direction"], flow_next=flow_next, flow_prev=flow_prev)
        g = flicker_gain(cur.shape[0], frame, fps=float(s["fps"]), readout_time_s=float(s["readout_time_s"]),
                         exposure_time_s=float(s["exposure_time_s"]), flicker_hz=float(s["flicker_hz"]),
                         depth=float(s["flicker_depth"]), phase=float(s["flicker_phase"]),
                         direction=s["shutter_direction"])
        if g is not None:
            out = out.copy()
            out[..., :3] *= g[:, None, None]
        return out

    def _to_camera(self, scene_ap1):
        """Scene -> RVB de la camera, avec le filtre passe-bas ou le defaut d'alignement des capteurs."""
        s = self.settings
        cam = self.scene_to_cam(scene_ap1)
        if self.multi_plane:
            for c, (dx, dy) in ((0, (s["registration_r_dx"], s["registration_r_dy"])),
                                (2, (s["registration_b_dx"], s["registration_b_dy"]))):
                if float(dx) or float(dy):
                    cam[..., c] = subpixel_shift(cam[..., c], float(dx), float(dy))
        if self.kind != "tube":
            cam = optical_low_pass(cam, float(s["olpf_h_px"]), float(s["olpf_v_px"]))
        return cam

    def raw(self, scene_ap1, frame=0, second_field=None):
        """Image de scene (deja passee par l'obturateur) -> brut du capteur (DN). Pas pour les tubes."""
        if self.sensor is None:
            raise ValueError("une camera a tube n'a pas de brut numerique")
        cam2 = self._to_camera(second_field) if second_field is not None and self.sensor.interlaced else None
        return self.sensor.expose(self._to_camera(scene_ap1), frame, cam2)

    def process(self, scene_ap1, frame=0, return_raw=False, second_field=None):
        """second_field : la scene une demi-image plus tard (seconde trame des cameras entrelacees)."""
        if self.tube is not None:
            cam2 = self._to_camera(second_field) if second_field is not None else None
            cam = self.tube.process(self._to_camera(scene_ap1), frame, cam2)
            out = self.color.cam_wb_to_scene(cam * self.color.wb_gains)
            out = self._finish(out, interlaced=bool(self.settings["tube_interlace"]))
            return (out, None) if return_raw else out
        dn = self.raw(scene_ap1, frame, second_field)
        out = develop(dn, self.sensor, self.color, self.settings)
        out = self._finish(out, interlaced=self.sensor.interlaced)
        return (out, dn) if return_raw else out

    def grid(self, h, w):
        """Taille de la grille de simulation pour une image (h, w), selon le mode de resolution."""
        mode = self.settings["sensor_resolution"]
        if mode == "image":
            return h, w
        if mode == "capteur":
            return resolution.sensor_grid(self.profile, h, w)
        raise ValueError("sensor_resolution inconnu : %r" % mode)

    def _finish(self, out, interlaced=False):
        """Etage video (au niveau du signal de la camera) ou, sans lui, niveau impose par l'exposition auto."""
        if self.dsp["enabled"]:
            return camdsp.apply(out, self.dsp, interlaced=interlaced,
                                gain=2.0 ** float(self.settings["ev"]) * self._ae_mult)
        return out * self._ae_mult if self._ae_mult != 1.0 else out

    def _apply_auto(self):
        """Avant une image : reglages decides par les automatismes a partir de l'image precedente."""
        a = self.auto
        if a.ae:
            if self.sensor is None:
                raise ValueError("l'exposition automatique n'est codee que pour les capteurs (pas les tubes)")
            iris, gain = a.split()
            s = self.settings
            # Diaphragme : plus de lumiere (plus d'electrons). Gain : signal et bruit amplifies,
            # meme nombre d'electrons (ISO x 2^gain et exposition + gain : les deux se compensent).
            self.sensor.set_exposure(float(s["iso"]) * 2.0 ** gain, float(s["ev"]) + iris + gain)
            self._ae_mult = 2.0 ** (iris + gain)
        if a.awb:
            if self.sensor is None:
                raise ValueError("la balance des blancs automatique n'est codee que pour les capteurs (pas les tubes)")
            self.settings["wb_red"] = self._wb_user[0] * 2.0 ** a.lr
            self.settings["wb_blue"] = self._wb_user[1] * 2.0 ** a.lb

    def process_frame(self, cur, frame=0, alpha=None, prev=None, nxt=None, flow_next=None, flow_prev=None):
        """Chaine complete pour une image d'une sequence. Renvoie (rvb, alpha).

        Avec les automatismes, les images doivent etre traitees dans l'ordre : chacune est
        reglee d'apres la precedente.
        """
        if self.auto.active:
            self._apply_auto()
        rgb, a = self._process_frame_any(cur, frame, alpha, prev, nxt, flow_next, flow_prev)
        if self.auto.active:
            self.auto.update(rgb, float(self.settings["fps"]))      # la camera mesure avant la bande
        if self.analog is not None:
            rgb = analog.apply(rgb, self.analog, frame)              # bande et liaison composite
        return rgb, a

    def _process_frame_any(self, cur, frame=0, alpha=None, prev=None, nxt=None, flow_next=None, flow_prev=None):
        """En mode "capteur", la scene est recueillie par la vraie grille de photosites, toute la
        chaine tourne a cette resolution, puis l'image est remise a la taille d'origine
        (sauf si sensor_output = "capteur").
        """
        h, w = cur.shape[:2]
        if self.settings["video_mode"] != "off":
            return self._process_video(cur, frame, alpha, prev, nxt, flow_next, flow_prev)
        hs, ws = self.grid(h, w)
        if (hs, ws) == (h, w):
            return self._process_frame(cur, frame, alpha, prev, nxt, flow_next, flow_prev)
        g = lambda x: resolution.to_grid(x, hs, ws)
        rgb, a = self._process_frame(g(cur), frame, g(alpha), g(prev), g(nxt),
                                     resolution.flow_to_grid(flow_next, hs, ws),
                                     resolution.flow_to_grid(flow_prev, hs, ws))
        if self.settings["sensor_output"] == "capteur":
            return rgb, a
        m = self.settings["sensor_upscale"]
        return resolution.to_image(rgb, h, w, m), resolution.to_image(a, h, w, m)

    def _process_video(self, cur, frame, alpha, prev, nxt, flow_next, flow_prev):
        """Mode video d'un appareil photo : lecture partielle du capteur (voir video.py)."""
        if self.sensor is None or self.sensor.three_chip or self.sensor.is_ccd:
            raise ValueError("le mode video (sauts de lignes, regroupement) concerne les appareils photo CMOS")
        h, w = cur.shape[:2]
        x = cur if alpha is None else np.concatenate([cur, alpha[..., None].astype(np.float64)], axis=-1)
        x = self.shutter(x, frame, prev, nxt, flow_next, flow_prev)
        rgb, a = (x[..., :3], None) if alpha is None else (x[..., :3], x[..., 3].astype(np.float32))
        out = self._finish(video.video_frame(self, rgb, frame))
        if self.settings["sensor_output"] == "capteur":
            return out, resolution.to_image(a, out.shape[0], out.shape[1], "bilinear")
        return resolution.to_image(out, h, w, self.settings["sensor_upscale"]), a

    def _process_frame(self, cur, frame=0, alpha=None, prev=None, nxt=None, flow_next=None, flow_prev=None):
        x = cur if alpha is None else np.concatenate([cur, alpha[..., None].astype(np.float64)], axis=-1)
        x = self.shutter(x, frame, prev, nxt, flow_next, flow_prev)
        rgb, a = (x[..., :3], None) if alpha is None else (x[..., :3], x[..., 3].astype(np.float32))
        second = None
        two_fields = (self.tube is not None and int(self.settings["tube_fields_per_frame"]) >= 2) or \
                     (self.sensor is not None and self.sensor.interlaced)
        if two_fields and (nxt is not None or flow_next is not None):
            second = half_frame_later(rgb, nxt, flow_next)
        return self.process(rgb, frame, second_field=second), a
