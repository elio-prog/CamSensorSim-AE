"""Capteur a semi-conducteur (CMOS ou CCD) : de la lumiere recue jusqu'au brut (valeurs numeriques DN).

Modele de formation du bruit (ELD : Wei et al., CVPR 2020 / TPAMI 2021) :

    DN = K * Poisson(electrons) + bruit_de_lecture + bruit_de_ligne + biais_couleur + noir
    puis quantification par le convertisseur.

    K                 gain du systeme, en DN par electron (proportionnel a l'ISO)
    bruit de lecture  loi de Tukey-lambda (queue plus longue qu'une gaussienne)
    bruit de ligne    une valeur gaussienne par ligne du capteur (bandes horizontales)
    biais couleur     decalage du noir propre a chaque canal du Bayer

Complements dans le domaine des electrons (Konnik & Welsh 2014, pyradi/rystare ;
norme EMVA 1288) : non-uniformite de sensibilite (PRNU, gaussienne), courant
d'obscurite et sa non-uniformite (DSNU, log-normale), pixels chauds, saturation du puits.

Pour un CCD (voir ccd.py) : eblouissement, trainee verticale, transfert imparfait,
colonnes bloquees, lecture entrelacee par addition de deux lignes.

Deux dispositions : mosaique de Bayer sur un seul capteur, ou trois capteurs
derriere un prisme (un par couleur, sans mosaique).

Ce qui est fige d'une image a l'autre depend seulement de la graine. Ce qui est
aleatoire depend aussi du numero d'image.
"""
import math

import numpy as np

from . import ccd, faults

# Exposition : selon la sensibilite ISO "saturation" (ISO 12232), un gris a 18 %
# correctement expose arrive a 10/78 = 12,8 % de la saturation du brut.
GREY_FRACTION_OF_SATURATION = 10.0 / 78.0
SCENE_GREY = 0.18

# Courant d'obscurite du silicium (Konnik & Welsh 2014, repris par pyradi) :
# D(T) proportionnel a T^1.5 * exp(-Eg(T) / (2 k T)), Eg(T) = 1.1557 - 7.021e-4 T^2 / (T + 1108).
K_BOLTZMANN_EV = 8.617333262e-5


def silicon_bandgap_ev(T):
    return 1.1557 - 7.021e-4 * T * T / (T + 1108.0)


def dark_current_temperature_factor(temp_c, ref_c=20.0):
    """Rapport du courant d'obscurite a temp_c sur celui a ref_c."""
    def f(tc):
        T = tc + 273.15
        return T ** 1.5 * math.exp(-silicon_bandgap_ev(T) / (2.0 * K_BOLTZMANN_EV * T))
    return f(temp_c) / f(ref_c)


def tukey_lambda_sample(rng, lam, size):
    """Tirage dans une loi de Tukey-lambda centree, d'echelle 1 (fonction quantile)."""
    u = rng.random(size)
    u = np.clip(u, 1e-12, 1.0 - 1e-12)
    if abs(lam) < 1e-9:
        return np.log(u) - np.log1p(-u)
    return (np.power(u, lam) - np.power(1.0 - u, lam)) / lam


def tukey_lambda_variance(lam):
    """Variance theorique de la loi de Tukey-lambda d'echelle 1 (lam > -0.5)."""
    if abs(lam) < 1e-9:
        return math.pi ** 2 / 3.0
    return (2.0 / lam ** 2) * (1.0 / (1.0 + 2.0 * lam) - math.gamma(lam + 1.0) ** 2 / math.gamma(2.0 * lam + 2.0))


def bayer_masks(h, w):
    """Masques booleens R, G1 (ligne du rouge), G2 (ligne du bleu), B pour un motif RGGB."""
    yy, xx = np.mgrid[0:h, 0:w]
    r = (yy % 2 == 0) & (xx % 2 == 0)
    g1 = (yy % 2 == 0) & (xx % 2 == 1)
    g2 = (yy % 2 == 1) & (xx % 2 == 0)
    b = (yy % 2 == 1) & (xx % 2 == 1)
    return r, g1, g2, b


def mosaic_rggb(rgb):
    """Echantillonne une image RVB selon le motif de Bayer RGGB."""
    h, w, _ = rgb.shape
    m = np.empty((h, w), dtype=rgb.dtype)
    m[0::2, 0::2] = rgb[0::2, 0::2, 0]
    m[0::2, 1::2] = rgb[0::2, 1::2, 1]
    m[1::2, 0::2] = rgb[1::2, 0::2, 1]
    m[1::2, 1::2] = rgb[1::2, 1::2, 2]
    return m


# Mosaique complementaire des capteurs interligne de camescope (Sony ICX...AK, "YCMG").
# Motif de 2 colonnes x 4 lignes, d'apres le "Color Coding Diagram" de la fiche Sony
# ICX409AK (E00611B28, p. 9) : trame A, ligne A1 = (G+Cy),(Mg+Ye) ; ligne A2 = (Mg+Cy),(G+Ye).
#   ligne 0 : Cy Ye
#   ligne 1 : G  Mg
#   ligne 2 : Cy Ye
#   ligne 3 : Mg G
CMYG_PATTERN = (("Cy", "Ye"), ("G", "Mg"), ("Cy", "Ye"), ("Mg", "G"))
# Filtres complementaires ideaux (fiche Sony et brevet US 5 136 370 : Ye = R+G, Cy = G+B, Mg = R+B).
CMYG_FILTERS = {"Ye": (1.0, 1.0, 0.0), "Cy": (0.0, 1.0, 1.0), "Mg": (1.0, 0.0, 1.0), "G": (0.0, 1.0, 0.0)}


def mosaic_cmyg(rgb, response=(1.0, 1.0, 1.0)):
    """Charge recue par chaque photosite derriere la mosaique complementaire.

    response : sensibilite relative du capteur a R, V, B (silicium + filtre anti-infrarouge).
    """
    h, w, _ = rgb.shape
    m = np.empty((h, w), dtype=rgb.dtype)
    for dy, row in enumerate(CMYG_PATTERN):
        for dx, name in enumerate(row):
            f = [CMYG_FILTERS[name][c] * response[c] for c in range(3)]
            sub = rgb[dy::4, dx::2]
            m[dy::4, dx::2] = f[0] * sub[..., 0] + f[1] * sub[..., 1] + f[2] * sub[..., 2]
    return m


class Sensor:
    def __init__(self, profile, settings):
        self.p = profile
        self.s = settings
        n = profile["noise"]
        self.is_ccd = profile.get("sensor_type", "cmos") == "ccd"
        self.three_chip = profile.get("cfa", "RGGB") == "3chip"
        self.cmyg = profile.get("cfa", "RGGB") == "CMYG"
        self.cmyg_response = tuple(float(v) for v in profile.get("cmyg_rgb_response", (1.0, 1.0, 1.0)))
        self.ccd = dict(profile.get("ccd", {}))
        self.black = float(profile["black_level"])
        self.white = float(profile["white_level"])
        self.span = self.white - self.black

        # ISO correspondant au gain 0 dB : reglage, sinon profil, sinon 100.
        self.base_iso = float(settings.get("base_iso") or profile.get("base_iso", 100.0))
        self.K_base = float(n["K_min"])
        # Capacite du puits, en electrons : saturation du brut a l'ISO de base.
        self.full_well_e = self.span / self.K_base * float(settings.get("full_well_gain", 1.0))
        self.set_exposure(float(settings["iso"]), float(settings["ev"]))

        self.interlaced = (settings.get("interlace") or self.ccd.get("interlace", "off")) == "fields"
        # Mosaique complementaire : l'addition de deux lignes dans le registre vertical fait
        # partie du codage des couleurs (le traitement en a besoin). Elle est donc toujours active.
        if self.cmyg:
            self.interlaced = True
        if self.interlaced and not (self.three_chip or self.cmyg):
            raise ValueError("la lecture entrelacee par addition de lignes n'est codee que pour les cameras a trois capteurs")

        self._fixed = {}
        self._adc_thresholds = None
        self._prev_dn = {}

    def set_exposure(self, iso, ev):
        """Gain (ISO) et exposition (diaphragmes). Appelable a chaque image (automatismes)."""
        n = self.p["noise"]
        self.iso, self.ev = float(iso), float(ev)
        # Gain du systeme. Hypothese : K proportionnel a l'ISO, K_min = ISO de base.
        self.K = self.K_base * self.iso / self.base_iso
        self.K_in_calibrated_range = n["K_min"] * 0.999 <= self.K <= n["K_max"] * 1.001
        logK = math.log(self.K)

        def loglin(d):
            return math.exp(d["slope"] * logK + d["bias"])

        self.read_gauss_sigma = loglin(n["read_gauss_logsigma"])   # DN
        self.read_tukey_scale = loglin(n["read_tukey_logscale"])   # DN
        self.row_sigma = loglin(n["row_logsigma"])                 # DN

        # Listes par reglage ISO calibre (deux mesures par reglage) : on moyenne
        # les paires puis on interpole en log(K) entre K_min et K_max.
        t = (logK - math.log(n["K_min"])) / (math.log(n["K_max"]) - math.log(n["K_min"]))
        t = min(max(t, 0.0), 1.0)
        shape = np.asarray(n["read_tukey_shape"], dtype=np.float64).reshape(-1, 2).mean(axis=1)
        bias = np.asarray(n["color_bias"], dtype=np.float64).reshape(-1, 2, 4).mean(axis=1)
        x = t * (len(shape) - 1)
        i0 = int(math.floor(x))
        i1 = min(i0 + 1, len(shape) - 1)
        f = x - i0
        self.tukey_lambda = float(shape[i0] * (1 - f) + shape[i1] * f)
        self.color_bias = bias[i0] * (1 - f) + bias[i1] * f       # ordre ELD : R, G1, B, G2

        # Exposition : valeur de scene -> DN au-dessus du noir.
        self.dn_per_scene_unit = (GREY_FRACTION_OF_SATURATION / SCENE_GREY) * self.span * 2.0 ** self.ev

    def _setting(self, name, key, default=0.0):
        """Reglage utilisateur, sinon valeur du profil CCD, sinon valeur par defaut."""
        v = self.s.get(name)
        return float(v) if v is not None else float(self.ccd.get(key, default))

    def _seed(self, *parts, chip=0):
        seq = [int(self.s["seed"])] + [int(x) for x in parts]
        return seq + [100 + chip] if chip else seq

    # ---------- motifs figes (identiques pour toutes les images) ----------
    def fixed_patterns(self, h, w, chip=0):
        fx = self._fixed.get(chip)
        if fx is not None and fx["shape"] == (h, w):
            return fx
        s, fp = self.s, self.p["fixed_pattern"]
        rng = np.random.default_rng(self._seed(0, chip=chip))
        prnu_sigma = s["prnu_sigma"] if s.get("prnu_sigma") is not None else fp["prnu_sigma"]
        dsnu_sigma = s["dsnu_rel_sigma"] if s.get("dsnu_rel_sigma") is not None else fp["dsnu_rel_sigma"]
        hot_frac = s["hot_pixel_fraction"] if s.get("hot_pixel_fraction") is not None else fp["hot_pixel_fraction"]

        # PRNU : gain par pixel, 1 + N(0, sigma) (Konnik & Welsh ; EMVA 1288).
        prnu = 1.0 + prnu_sigma * rng.standard_normal((h, w)) if prnu_sigma > 0 else None
        # DSNU : courant d'obscurite par pixel, log-normal de moyenne 1.
        if dsnu_sigma > 0:
            sg = math.sqrt(math.log(1.0 + dsnu_sigma ** 2))
            dsnu = rng.lognormal(mean=-0.5 * sg * sg, sigma=sg, size=(h, w))
        else:
            dsnu = np.ones((h, w))
        # Pixels chauds : une fraction des pixels a un courant d'obscurite multiplie.
        if hot_frac > 0:
            hot = rng.random((h, w)) < hot_frac
            dsnu = np.where(hot, dsnu * float(s.get("hot_pixel_gain", 1000.0)) * rng.random((h, w)), dsnu)
        # Amplis de colonnes : decalage (DN) et gain propres a chaque colonne, figes.
        rc = np.random.default_rng(self._seed(0, 1, chip=chip))
        col_off = float(s["column_fpn_dn"]) * rc.standard_normal(w) if float(s["column_fpn_dn"]) > 0 else None
        col_gain = 1.0 + float(s["column_gain_sigma"]) * rc.standard_normal(w) if float(s["column_gain_sigma"]) > 0 else None
        # Defauts de fabrication de la matrice.
        rd = np.random.default_rng(self._seed(0, 2, chip=chip))
        dead, stuck = faults.defect_masks(rd, h, w, float(s["dead_pixel_fraction"]), float(s["stuck_pixel_fraction"]),
                                          int(s["dead_columns"]), int(s["dead_rows"]))
        fx = {"shape": (h, w), "prnu": prnu, "dark_map": dsnu, "col_off": col_off, "col_gain": col_gain,
              "dead": dead if dead.any() else None, "stuck": stuck if stuck.any() else None}
        self._fixed[chip] = fx
        return fx

    # ---------- exposition d'une image ----------
    def expose(self, cam_rgb, frame=0, cam_rgb_field2=None, binning=None):
        """cam_rgb : RVB natif de la camera, en unites de scene.

        Renvoie le brut en DN : (H, W) pour une mosaique de Bayer, (H, W, 3) pour trois capteurs.
        cam_rgb_field2 : la scene une demi-image plus tard, pour la seconde trame d'une lecture entrelacee.
        """
        scale = self.dn_per_scene_unit / self.K
        if self.cmyg:
            # Lecture par trames : trame A = lignes (0+1), (2+3)... ; trame B = lignes (1+2), (3+4)...
            # prise une demi-image plus tard. Sortie : une ligne de sommes par ligne d'image,
            # trame A sur les lignes paires, trame B sur les lignes impaires.
            # Une ligne de photosites de plus en bas (le capteur reel en a au-dela de l'image utile),
            # pour que la derniere ligne de la trame B ait une vraie paire.
            second = cam_rgb if cam_rgb_field2 is None else cam_rgb_field2
            pad = lambda x: np.concatenate([x, x[-1:]], axis=0)
            resp = self.cmyg_response
            a = self._expose_plane(mosaic_cmyg(pad(cam_rgb), resp) * scale, frame, 0, bayer=False, field=0, bin_offset=0)
            b = self._expose_plane(mosaic_cmyg(pad(second), resp) * scale, frame, 0, bayer=False, field=1, bin_offset=1)
            dn = np.empty(cam_rgb.shape[:2])
            dn[0::2] = a[: dn[0::2].shape[0]]
            dn[1::2] = b[: dn[1::2].shape[0]]
            return dn
        if binning is not None and tuple(binning) != (1, 1):
            if self.three_chip or self.is_ccd:
                raise ValueError("le regroupement de photosites video n'est code que pour les capteurs CMOS a mosaique de Bayer")
            return self._expose_plane(mosaic_rggb(cam_rgb) * scale, frame, 0, bayer=True, binning=tuple(binning))
        if not self.three_chip:
            return self._expose_plane(mosaic_rggb(cam_rgb) * scale, frame, 0, bayer=True)
        second = cam_rgb if cam_rgb_field2 is None else cam_rgb_field2
        planes = []
        for c in range(3):
            if self.interlaced:
                a = self._expose_plane(cam_rgb[..., c] * scale, frame, c, bayer=False, field=0, bin_offset=0)
                b = self._expose_plane(second[..., c] * scale, frame, c, bayer=False, field=1, bin_offset=1)
                h = cam_rgb.shape[0]
                dn = np.empty((h, cam_rgb.shape[1]))
                dn[0::2] = a[: dn[0::2].shape[0]]       # trame paire
                dn[1::2] = b[: dn[1::2].shape[0]]       # trame impaire, prise 1/50 s plus tard
            else:
                dn = self._expose_plane(cam_rgb[..., c] * scale, frame, c, bayer=False)
            planes.append(dn)
        return np.stack(planes, axis=-1)

    def _expose_plane(self, electrons, frame, chip, bayer, field=0, bin_offset=None, binning=None):
        """electrons : charges attendues sur chaque photosite d'un capteur (H, W). Renvoie le brut en DN."""
        s = self.s
        h, w = electrons.shape
        fx = self.fixed_patterns(h, w, chip)
        seq = self._seed(1, frame, chip=chip)
        rng = np.random.default_rng(seq + [200 + field] if field else seq)
        K = self.K

        # 1. Electrons attendus sur chaque photosite.
        if fx["prnu"] is not None:
            electrons = electrons * fx["prnu"]
        # 1b. Ombrage du capteur : la sensibilite baisse du centre vers les coins (forme parabolique).
        #     Definition de la fiche Sony ICX409AK : SHy = (Ymax - Ymin) / Y, mesure sur la zone utile.
        shade = float(s["sensor_shading"]) if s.get("sensor_shading") is not None else float(self.p.get("shading", 0.0))
        if shade > 0:
            yy = (np.arange(h) - (h - 1) / 2.0) / max((h - 1) / 2.0, 1.0)
            xx = (np.arange(w) - (w - 1) / 2.0) / max((w - 1) / 2.0, 1.0)
            electrons = electrons * (1.0 - shade * 0.5 * (yy[:, None] ** 2 + xx[None, :] ** 2))
        electrons = np.maximum(electrons, 0.0)

        # 2. Courant d'obscurite (electrons thermiques), selon la temperature et le temps de pose.
        fp = self.p["fixed_pattern"]
        dark_ref = s["dark_current_e_per_s"] if s.get("dark_current_e_per_s") is not None else fp["dark_current_e_per_s_at_20C"]
        if dark_ref > 0:
            dark = dark_ref * dark_current_temperature_factor(float(s["temperature_c"])) * float(s["exposure_time_s"])
            electrons = electrons + dark * fx["dark_map"]
        expected = electrons

        # 2b. Lecture video : regroupement de photosites de meme couleur (moyenne de n photosites).
        #     Chaque photosite sature d'abord seul ; la somme de n lois de Poisson est une loi de
        #     Poisson de la somme, donc tirer le bruit sur la somme puis diviser par n est exact.
        #     Hypothese : le bruit de lecture et les defauts fixes de colonne s'appliquent une fois,
        #     a la sortie regroupee (le regroupement se fait dans le capteur, avant la conversion).
        n_bin = 1
        if binning is not None:
            from .video import bin_same_colour
            if s["clip_full_well"]:
                electrons = np.minimum(electrons, self.full_well_e)
            n_bin = int(binning[0]) * int(binning[1])
            electrons = bin_same_colour(electrons, int(binning[0]), int(binning[1]))
            expected = electrons / n_bin
            h, w = electrons.shape
            fx = self.fixed_patterns(h, w, chip + 50)
            rng = np.random.default_rng(self._seed(1, frame, chip=chip + 50))

        # 3. Bruit de photons : loi de Poisson sur le nombre d'electrons.
        if s["shot_noise"]:
            electrons = rng.poisson(electrons).astype(np.float64)
        if n_bin > 1:
            electrons = electrons / n_bin

        # 3b. Remanence : une part de la charge de l'image precedente n'a pas ete videe et s'ajoute.
        lag = self._setting("ccd_lag", "lag") if self.is_ccd else 0.0
        if lag > 0:
            key = ("lag", chip, field)
            prev = self._prev_dn.get(key)
            current = electrons
            if prev is not None and prev.shape == electrons.shape:
                electrons = (1.0 - lag) * electrons + lag * prev
            self._prev_dn[key] = current

        if self.is_ccd:
            # 4. CCD : eblouissement, trainee verticale, colonnes bloquees, transfert imparfait.
            if s["clip_full_well"]:
                electrons = ccd.blooming(electrons, self.full_well_e, self._setting("ccd_antiblooming", "antiblooming", 1.0))
            sm = ccd.smear(expected, self.ccd.get("smear_fraction", 0.0) * float(s["ccd_smear_gain"]),
                           float(s["exposure_time_s"]), self.ccd.get("smear_ref_exposure_s", 0.02),
                           rng if s["shot_noise"] else None)
            if sm is not None:
                electrons = electrons + sm
            if int(s["ccd_blocked_columns"]) > 0:
                electrons = ccd.blocked_columns(electrons, int(s["ccd_blocked_columns"]),
                                                np.random.default_rng(self._seed(0, 4, chip=chip)))
            eps_v, eps_h = self._setting("ccd_cti_v", "cti"), self._setting("ccd_cti_h", "cti")
            if eps_v > 0 or eps_h > 0:
                electrons = ccd.transfer_inefficiency(electrons, eps_v, eps_h)
            # Lecture entrelacee : deux lignes voisines sont additionnees dans le registre vertical.
            if bin_offset is not None:
                e = electrons[bin_offset:]
                if e.shape[0] % 2:
                    e = np.concatenate([e, e[-1:]], axis=0)
                electrons = e[0::2] + e[1::2]
                K = K / 2.0                               # deux photosites pour une ligne : gain divise par deux
                h = electrons.shape[0]
        elif s["clip_full_well"]:
            # 4. Saturation du puits.
            electrons = np.minimum(electrons, self.full_well_e)

        # 5. Conversion en DN (avec le gain de chaque ampli de colonne), niveau noir, biais par canal.
        signal = K * electrons
        if fx["col_gain"] is not None:
            signal = signal * fx["col_gain"][None, :]
        dn = signal + self.black
        bias_gain = float(s["color_bias_gain"])
        if bayer and bias_gain != 0.0:
            cb = self.color_bias * bias_gain
            dn[0::2, 0::2] += cb[0]
            dn[0::2, 1::2] += cb[1]
            dn[1::2, 1::2] += cb[2]
            dn[1::2, 0::2] += cb[3]

        # 5b. Pannes d'adressage : la ligne ou la colonne lue n'est pas celle demandee.
        row_map = col_map = None
        if s["row_address_stuck"] or float(s["row_address_error_rate"]) > 0:
            row_map = faults.address_map(h, s["row_address_stuck"], float(s["row_address_error_rate"]), rng)
        if s["col_address_stuck"] or float(s["col_address_error_rate"]) > 0:
            col_map = faults.address_map(w, s["col_address_stuck"], float(s["col_address_error_rate"]), rng)
        if row_map is not None or col_map is not None:
            dn = faults.remap(dn, row_map, col_map, self.black)

        # 5c. Pixels, lignes et colonnes morts (noir) ou colles (blanc).
        for key, level in (("dead", self.black), ("stuck", self.white)):
            m = fx[key]
            if m is not None:
                if m.shape[0] != h:                      # lecture entrelacee : une ligne sur deux
                    m = m[(bin_offset or 0)::2][:h]
                    if m.shape[0] < h:
                        m = np.concatenate([m, m[-1:]], axis=0)
                dn = np.where(m, level, dn)

        # 6. Bruit de lecture.
        rg = float(s["read_noise_gain"])
        model = "gauss" if self.is_ccd and s["read_noise_model"] == "tukey" else s["read_noise_model"]
        if rg > 0 and model != "off":
            if model == "tukey":
                dn += rg * self.read_tukey_scale * tukey_lambda_sample(rng, self.tukey_lambda, (h, w))
            elif model == "gauss":
                dn += rg * self.read_gauss_sigma * rng.standard_normal((h, w))
            else:
                raise ValueError("read_noise_model inconnu : %r" % s["read_noise_model"])

        # 7. Bruit de ligne : une valeur par ligne.
        rowg = float(s["row_noise_gain"])
        if rowg > 0:
            dn += rowg * self.row_sigma * rng.standard_normal((h, 1))

        # 7b. Decalage fixe des colonnes, parasite periodique de l'alimentation.
        if fx["col_off"] is not None:
            dn = dn + fx["col_off"][None, :]
        if float(s["interference_dn"]) > 0:
            dn = dn + faults.interference(h, w, frame, float(s["interference_dn"]), float(s["interference_period_px"]))

        if not s["quantize"]:
            return dn

        # 8. Convertisseur analogique-numerique.
        native = int(self.p["bits"])
        bits = int(s["adc_bits"]) if s.get("adc_bits") is not None else native
        bits = min(max(bits, 1), native)
        thresholds = None
        if float(s["adc_dnl_lsb"]) > 0:
            n_codes = int(np.floor(self.white / 2.0 ** (native - bits))) + 1
            if self._adc_thresholds is None or len(self._adc_thresholds) != n_codes - 1:
                self._adc_thresholds = faults.adc_thresholds(np.random.default_rng(self._seed(0, 3)),
                                                             n_codes, float(s["adc_dnl_lsb"]))
            thresholds = self._adc_thresholds
        dn = faults.adc_convert(dn, self.white, native, bits, thresholds, float(s["adc_inl_lsb"]))
        dn = faults.stuck_bits(dn, s["adc_stuck_bits"])
        dn = faults.sparkle(dn, float(s["adc_sparkle_rate"]), native, rng)

        # 9. Liaison serie entre le capteur et le processeur.
        dn = faults.bitslip(dn, int(s["bitslip"]), native)
        dn = faults.line_shift(dn, float(s["line_shift_rate"]), int(s["line_shift_max_px"]),
                               bool(s["line_shift_persist"]), self.black, rng)
        dn = np.clip(dn, 0.0, float((1 << native) - 1))

        # 10. Memoire tampon : image dechiree (le bas vient de l'image precedente).
        current = dn
        key = (chip, field)
        prev = self._prev_dn.get(key)
        if float(s["frame_tear_rate"]) > 0 and prev is not None and prev.shape == dn.shape:
            rt = np.random.default_rng([int(s["seed"]), 2, int(frame)])
            if rt.random() < float(s["frame_tear_rate"]):
                cut = int(rt.integers(1, h))
                dn = dn.copy()
                dn[cut:] = prev[cut:]
        self._prev_dn[key] = current
        return dn

    def describe(self):
        d = {
            "iso": self.iso, "K_dn_per_e": self.K, "K_dans_plage_calibree": self.K_in_calibrated_range,
            "lecture_gauss_dn": self.read_gauss_sigma, "lecture_gauss_e": self.read_gauss_sigma / self.K,
            "lecture_tukey_echelle_dn": self.read_tukey_scale, "tukey_lambda": self.tukey_lambda,
            "ligne_dn": self.row_sigma, "biais_couleur_dn": [float(x) for x in self.color_bias],
            "puits_electrons": self.full_well_e, "dn_par_unite_de_scene": self.dn_per_scene_unit,
            "saturation_en_valeur_de_scene": self.span / self.dn_per_scene_unit,
        }
        if self.is_ccd:
            d["ccd"] = self.ccd
        return d
