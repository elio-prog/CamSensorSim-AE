"""Camera a tube (vidicon, Plumbicon) : cible photoconductrice lue par un faisceau d'electrons.

Principe (manuel Mullard "Plumbicon camera tubes", 1987, notes generales) : la cible
est une mosaique de petits condensateurs. La lumiere les decharge en partie ; a
chaque trame (1/50 s) le faisceau les ramene au potentiel de la cathode, et le
courant de recharge est le signal video.

Modele de lecture. Le courant qui atterrit sur un element depend de son potentiel v
(Yamamoto et Kiuchi, "Calculations of Image Lag in Vidicon-Type Camera Tubes",
Jpn. J. Appl. Phys. 10, 1203, 1971, forme qui colle le mieux aux mesures) :

    i_faisceau(v) = I_b / (1 + exp(-a v))          a = e / (k T_faisceau)

En posant u = a v (potentiel sans dimension), l'integration pendant le passage du
faisceau donne une forme fermee :

    u' - exp(-u') = u - exp(-u) - beta             beta = g * I_b

et la lumiere d'une trame ajoute  u += g * i_signal,  avec  g = a * T_trame / C
(C : capacite de la couche). Le signal de sortie est la charge rendue : (u - u') / g.

Ce seul mecanisme produit, sans rien ajouter :
  - la remanence de decharge, bien plus forte dans les basses lumieres ;
  - l'effet de la lumiere de polarisation (bias light), qui la reduit ;
  - la limite du faisceau : au-dela de I_b le faisceau ne suffit plus, la charge
    reste et se vide sur plusieurs trames (trainees de comete).

La derivation de la forme fermee est la mienne, a partir de la loi d'atterrissage
citee ; je n'ai pas pu relire les equations de l'article lui-meme.

S'y ajoutent : gamma de la couche, remanence photoconductrice (couche Sb2S3),
courant d'obscurite et sa loi de temperature, etalement des hautes lumieres,
tache du faisceau (resolution), bruit du preamplificateur, ombrage, marquage.
"""
import math

import numpy as np


def f_landing(u):
    return u - np.exp(-u)


def beam_scan(u, beta, iterations=8):
    """Potentiel apres le passage du faisceau : resout u' - exp(-u') = u - exp(-u) - beta."""
    c = f_landing(u) - beta
    # Point de depart : regime lineaire si c > 1, regime exponentiel si c < -1.
    x = np.where(c > 1.0, c, np.where(c < -1.0, -np.log(np.maximum(-c, 1e-300)), 0.0))
    for _ in range(iterations):
        e = np.exp(-x)
        x = x - (x - e - c) / (1.0 + e)
    return x


def gaussian_blur(img, sigma):
    """Flou gaussien separable applique dans le domaine de Fourier (exact, bords en miroir)."""
    if sigma <= 0:
        return img
    pad = int(math.ceil(4 * sigma))
    x = np.pad(img, ((pad, pad), (pad, pad)), mode="reflect")
    H, W = x.shape
    fy = np.fft.fftfreq(H)[:, None]
    fx = np.fft.rfftfreq(W)[None, :]
    mtf = np.exp(-2.0 * (math.pi * sigma) ** 2 * (fx * fx + fy * fy))
    return np.fft.irfft2(np.fft.rfft2(x) * mtf, s=(H, W))[pad:-pad, pad:-pad]


def spot_sigma_from_modulation(height_px, modulation, tv_lines=400.0):
    """Ecart-type (en pixels) d'une tache gaussienne qui laisse 'modulation' de contraste a tv_lines.

    400 lignes TV = 200 cycles sur la hauteur de l'image.
    """
    f = (tv_lines / 2.0) / height_px
    return math.sqrt(-math.log(modulation) / (2.0 * math.pi ** 2)) / f


def triangular_noise(rng, shape, sigma, triangular=1.0):
    """Bruit du preamplificateur : densite qui monte avec la frequence le long des lignes.

    triangular=1 : spectre purement triangulaire ; 0 : bruit blanc. Ecart-type total = sigma.
    """
    n = rng.standard_normal(shape)
    if triangular <= 0:
        return sigma * n
    w = shape[1]
    f = np.fft.rfftfreq(w)
    shape_f = (1.0 - triangular) + triangular * f / f.max()
    out = np.fft.irfft(np.fft.rfft(n, axis=1) * shape_f[None, :], n=w, axis=1)
    return out * (sigma / out.std())


class TubeTarget:
    """Une cible (un tube). Garde son etat d'une trame a l'autre."""

    def __init__(self, p, seed):
        self.p = p
        self.seed = int(seed)
        self.u = None            # potentiel sans dimension de chaque element
        self.trap = None         # charge retenue par les pieges (remanence photoconductrice)
        self.burn = None         # memoire lente de l'exposition (marquage)
        self.field = 0

    def reset(self):
        self.u = self.trap = self.burn = None
        self.field = 0

    def expose_field(self, light, field_time_s=0.02):
        """light : eclairement relatif (1.0 = blanc de reference). Renvoie le courant de signal en nA."""
        p = self.p
        h, w = light.shape
        g = p["g_per_nA"]
        beta = g * p["beam_nA"]

        # Courant photoelectrique : loi en puissance (gamma de la couche), ombrage, marquage.
        i_sig = p["white_nA"] * np.power(np.maximum(light, 0.0), p["gamma"])
        if p["shading"] > 0:
            yy, xx = np.mgrid[0:h, 0:w]
            r2 = ((yy - h / 2) / (h / 2)) ** 2 + ((xx - w / 2) / (w / 2)) ** 2
            i_sig = i_sig * (1.0 - p["shading"] * r2 / 2.0)
        if p["burn_gain"] > 0:
            if self.burn is None:
                self.burn = np.zeros((h, w))
            i_sig = i_sig * (1.0 - p["burn_gain"] * np.clip(self.burn, 0.0, 1.0))
            k = field_time_s / max(p["burn_seconds"], field_time_s)
            self.burn += k * (np.clip(light, 0.0, 4.0) - self.burn)

        # Remanence photoconductrice : une part de la charge est piegee puis rendue lentement.
        if p["photo_lag"] > 0:
            if self.trap is None:
                self.trap = np.zeros((h, w))
            rate = 1.0 / max(p["photo_lag_fields"], 1e-6)
            released = self.trap * rate
            self.trap += p["photo_lag"] * i_sig - released
            i_sig = (1.0 - p["photo_lag"]) * i_sig + released

        # Courant d'obscurite (double tous les 7 a 8 degres pour un vidicon) et lumiere de polarisation.
        dark = p["dark_nA"] * 2.0 ** ((p["temperature_c"] - p["dark_ref_c"]) / p["dark_doubling_c"])
        pedestal = dark + p["bias_nA"]

        if self.u is None:       # cible deja stabilisee dans le noir
            self.u = np.full((h, w), -math.log(max(beta - g * pedestal, 1e-9) / -math.expm1(-g * max(pedestal, 1e-9))))

        # La lumiere decharge les condensateurs pendant la trame.
        u = self.u + g * (i_sig + pedestal)
        u = np.minimum(u, p["u_max"])                      # la tension de cible borne la charge
        # Hautes lumieres non stabilisees : l'exces s'etale sur les elements voisins.
        if p["bloom_px"] > 0:
            excess = np.maximum(u - beta, 0.0)
            if excess.max() > 0:
                u = u - excess + gaussian_blur(excess, p["bloom_px"])

        # Lecture par le faisceau.
        u_after = beam_scan(u, beta)
        out = (u - u_after) / g - pedestal                 # le niveau noir est recale par la camera
        self.u = u_after
        self.field += 1

        # Tache du faisceau (resolution), puis bruit du preamplificateur.
        out = gaussian_blur(out, p["spot_px"])
        if p["noise_nA"] > 0:
            rng = np.random.default_rng([self.seed, 5, self.field])
            out = out + triangular_noise(rng, (h, w), p["noise_nA"], p["noise_triangular"])
        return out


class TubeCamera:
    """Camera a trois tubes (rouge, vert, bleu) derriere un prisme, ou a un seul tube."""

    def __init__(self, profile, settings):
        t = dict(profile["tube"])
        s = settings
        for key, name in (("beam_nA", "tube_beam_nA"), ("bias_nA", "tube_bias_nA"), ("dark_nA", "tube_dark_nA"),
                          ("gamma", "tube_gamma"), ("photo_lag", "tube_photo_lag"),
                          ("photo_lag_fields", "tube_photo_lag_fields"), ("noise_nA", "tube_noise_nA"),
                          ("bloom_px", "tube_bloom_px"), ("shading", "tube_shading"), ("spot_px", "tube_spot_px")):
            if s.get(name) is not None:
                t[key] = float(s[name])
        t["burn_gain"] = float(s["tube_burn_gain"])
        t["burn_seconds"] = float(s["tube_burn_seconds"])
        t["temperature_c"] = float(s["temperature_c"])
        t.setdefault("noise_triangular", 1.0)
        t.setdefault("dark_ref_c", 30.0)
        t.setdefault("dark_doubling_c", 7.5)
        t.setdefault("u_max", 150.0)
        self.t = t
        self.s = s
        self.white_scene = 0.9 / 2.0 ** float(s["ev"])    # un blanc a 90 % donne le courant de blanc
        self.targets = [TubeTarget(t, int(s["seed"]) * 10 + c) for c in range(3)]
        self._spot_ready = t.get("spot_px") is not None

    def process(self, cam_rgb, frame=0, second_field=None):
        """cam_rgb : RVB camera en unites de scene. Renvoie le RVB camera apres le tube (lineaire)."""
        t, s = self.t, self.s
        h = cam_rgb.shape[0]
        if not self._spot_ready:
            t["spot_px"] = spot_sigma_from_modulation(h, t["modulation_400"])
            self._spot_ready = True
        fields = max(int(s["tube_fields_per_frame"]), 1)
        field_time = 1.0 / (float(s["fps"]) * fields)
        out = np.empty_like(cam_rgb)
        mono = bool(t.get("mono"))
        luma = np.array([0.2126, 0.7152, 0.0722])          # un seul tube : il ne voit que la luminance
        for c in range(1 if mono else 3):
            results = []
            for k in range(fields):
                src = cam_rgb if (k == 0 or second_field is None) else second_field
                plane = src @ luma if mono else src[..., c]
                results.append(self.targets[c].expose_field(plane / self.white_scene, field_time))
            if fields >= 2 and s["tube_interlace"]:
                sig = results[0].copy()
                sig[1::2] = results[1][1::2]             # trame impaire : lignes impaires
            else:
                sig = sum(results) / len(results)
            # Signal en nA -> lumiere relative. La camera corrige le gamma du tube.
            rel = sig / t["white_nA"]
            if s["tube_gamma_correct"] and t["gamma"] != 1.0:
                rel = np.sign(rel) * np.power(np.abs(rel), 1.0 / t["gamma"])
            out[..., c] = rel * self.white_scene
        if mono:
            out[..., 1] = out[..., 2] = out[..., 0]
        return out

    def describe(self):
        t = self.t
        return {"blanc_nA": t["white_nA"], "faisceau_nA": t["beam_nA"], "polarisation_nA": t["bias_nA"],
                "obscurite_nA": t["dark_nA"], "gamma": t["gamma"], "g_par_nA": t["g_per_nA"],
                "tache_px": t.get("spot_px"), "bruit_nA": t["noise_nA"],
                "saturation_en_valeur_de_scene": self.white_scene * (t["beam_nA"] / t["white_nA"]) ** (1 / t["gamma"])}
